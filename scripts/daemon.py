#!/usr/bin/env python3
"""Double-fork daemon launcher: survives the shell that started it.

Usage: daemon.py <logfile> <command> [args...]

Forks twice so the final process is reparented to init (ppid=1), detaches
from the controlling terminal and redirects stdio to the logfile.
"""
import os
import sys


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: daemon.py <logfile> <command> [args...]")
    logfile, command, args = sys.argv[1], sys.argv[2], sys.argv[3:]

    log = open(logfile, "ab", buffering=0)
    devnull = open(os.devnull, "rb")

    if os.fork() > 0:                     # parent exits immediately
        os._exit(0)

    os.setsid()                            # new session, no ctrl terminal

    if os.fork() > 0:                      # first child exits -> daemon
        os._exit(0)                        # reparented to init

    os.umask(0o022)
    os.dup2(devnull.fileno(), 0)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)

    os.execvp(command, [command, *args])


if __name__ == "__main__":
    main()
