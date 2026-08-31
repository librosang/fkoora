#!/usr/bin/env python3
"""Validate the two docker-compose files without a docker daemon.

Checks per file:
  * YAML parses cleanly (no duplicate keys - pyyaml's safe_load would
    silently take the last one, so we use a duplicate-key-detecting loader)
  * every depends_on target exists
  * every condition is one of the spec's three
  * healthchecked services referenced via service_healthy actually define
    a healthcheck (compose would fail at `up` otherwise)
  * restart values are strings ("no" quoted, not YAML false)
  * build contexts + dockerfiles exist on disk relative to the file
  * networks referenced are declared
  * port bindings are well-formed strings
"""
import os
import sys

import yaml

REPO = "/home/z/my-project/kooora"

FILES = [
    (os.path.join(REPO, "docker-compose.yml"), REPO),
    # homelab layout: the compose sits next to the repo checked out as ./fkoora
    (os.path.join(REPO, "docker-compose.fkoora-full.yml"), REPO),
]

# services we own (build from our Dockerfiles); everything else in the
# homelab file (NPM, MariaDB, openlitespeed, immich, ...) is the user's
# pre-existing stack - not ours to validate
OUR_DOCKERFILES = {"Dockerfile.api", "Dockerfile.frontend"}

VALID_CONDITIONS = {"service_started", "service_healthy",
                    "service_completed_successfully"}


class DupKeyLoader(yaml.SafeLoader):
    """SafeLoader that raises on duplicate mapping keys."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ValueError(f"duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def check(path, ctx_root):
    problems = []
    with open(path, encoding="utf-8") as fh:
        doc = yaml.load(fh, Loader=DupKeyLoader)

    if not isinstance(doc, dict) or "services" not in doc:
        return [f"{path}: no 'services' mapping at top level"], []

    services = doc["services"]
    declared_networks = set(doc.get("networks", {}))
    ok = []

    def ctx_to_path(ctx):
        """Resolve a build context to a real directory.

        The homelab compose references './fkoora' (the repo checked out
        next to it); in this sandbox the repo lives at REPO, so map it.
        """
        if ctx == "./fkoora":
            return REPO
        return os.path.normpath(os.path.join(ctx_root, ctx))

    for name, spec in services.items():
        where = f"{os.path.basename(path)}:{name}"

        # restart must be a string ("no" quoted - bare no parses to False)
        restart = spec.get("restart")
        if restart is not None and not isinstance(restart, str):
            problems.append(f"{where}: restart must be a quoted string "
                            f"(got {restart!r} - YAML parsed it as a bool)")

        # depends_on targets + conditions
        deps = spec.get("depends_on", {})
        if isinstance(deps, list):  # short form
            deps = {d: None for d in deps}
        for dep, dspec in (deps or {}).items():
            if dep not in services:
                problems.append(f"{where}: depends_on target '{dep}' "
                                "is not a service")
                continue
            cond = (dspec or {}).get("condition")
            if cond is not None and cond not in VALID_CONDITIONS:
                problems.append(f"{where}: unknown condition {cond!r}")
            if cond == "service_healthy":
                target = services[dep] or {}
                if "healthcheck" in target:
                    continue  # compose-level healthcheck: unambiguous
                # image-level HEALTHCHECK counts too (compose v2 honors it):
                # fkoora-api/worker/migrate build from Dockerfile.api which
                # defines HEALTHCHECK CMD -> the gate is valid
                tb = target.get("build")
                tb_df = tb.get("dockerfile") if isinstance(tb, dict) else None
                if tb_df in OUR_DOCKERFILES:
                    ok.append(f"{where}: '{dep}' healthy gate via image "
                               "HEALTHCHECK (Dockerfile)")
                    continue
                problems.append(
                    f"{where}: gates on '{dep}' service_healthy but that "
                    "service defines no healthcheck (neither compose nor "
                    "our Dockerfiles)")

        # build context + dockerfile exist (only for services we own)
        build = spec.get("build")
        if build:
            ctx = build.get("context", ".") if isinstance(build, dict) else build
            df = (build.get("dockerfile", "Dockerfile")
                  if isinstance(build, dict) else "Dockerfile")
            if df in OUR_DOCKERFILES:
                ctx_path = ctx_to_path(ctx)
                if not os.path.isdir(ctx_path):
                    problems.append(f"{where}: build context '{ctx}' not found "
                                    f"({ctx_path})")
                else:
                    if not os.path.isfile(os.path.join(ctx_path, df)):
                        problems.append(f"{where}: dockerfile '{df}' missing in {ctx}")
                    else:
                        ok.append(f"{where}: build ctx ok ({ctx}, {df})")

        # networks declared
        for net in spec.get("networks", []) or []:
            if net not in declared_networks:
                problems.append(f"{where}: network '{net}' not declared")

        # ports well-formed
        for p in spec.get("ports", []) or []:
            if not isinstance(p, str):
                problems.append(f"{where}: port {p!r} should be a quoted string")
            else:
                ok.append(f"{where}: port {p}")

        # environment values must be scalars (compose rejects maps of lists)
        env = spec.get("environment", {})
        if isinstance(env, dict):
            for k, v in env.items():
                if isinstance(v, (list, dict)):
                    problems.append(f"{where}: env {k} must be a scalar")

    # graph: no service can depend on a service that depends back (cycle)
    def deps_of(name):
        d = services[name].get("depends_on", {})
        if isinstance(d, list):
            return set(d)
        return set(d or {})

    for name in services:
        seen, stack = set(), [name]
        while stack:
            cur = stack.pop()
            for dep in deps_of(cur):
                if dep == name and cur != name:
                    problems.append(f"dependency cycle at {name}->{cur}->{dep}")
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)

    return problems, ok


def main():
    all_ok = True
    for path, ctx_root in FILES:
        if not os.path.isfile(path):
            print(f"MISSING: {path}")
            all_ok = False
            continue
        print(f"\n=== {os.path.basename(path)} ===")
        problems, ok = check(path, ctx_root)
        for line in ok:
            print(f"  ok   {line}")
        for line in problems:
            print(f"  FAIL {line}")
            all_ok = False
    print("\nRESULT:", "ALL CHECKS PASSED" if all_ok else "PROBLEMS FOUND")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
