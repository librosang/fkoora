#!/usr/bin/env bash
# Rebuild download/fkoora-match-center.zip from the project root.
# Re-run after any change; keeps the same inclusion/exclusion list as before.
set -euo pipefail
cd /home/z/my-project

OUT=download/fkoora-match-center.zip
rm -f "$OUT"

zip -r -q "$OUT" \
  src public scraper scripts examples \
  package.json bun.lock requirements.txt \
  next.config.ts tsconfig.json postcss.config.mjs components.json \
  eslint.config.mjs vercel.json \
  Dockerfile.api Dockerfile.frontend docker-entrypoint.sh \
  docker-compose.yml docker-compose.fkoora-full.yml \
  README.md README.scraper.md DEPLOY.md \
  .env .env.example \
  -x "*node_modules*" "*.next*" "*__pycache__*" "*.git*" "*tool-results*"

echo "--- zip rebuilt ---"
unzip -l "$OUT" | tail -3
echo "--- sanity: key files present ---"
for f in scraper/apicache.py scraper/worker.py scraper/jobs.py \
         docker-compose.fkoora-full.yml DEPLOY.md \
         scripts/mock_backend.js scripts/test_cache.py scripts/test_split.py \
         src/components/mc/team-dialog.tsx src/components/mc/player-dialog.tsx; do
  unzip -l "$OUT" | grep -q " $f\$" && echo "OK      $f" || echo "MISSING $f"
done
