#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
case "$(uname -m)" in x86_64|amd64) ;; *) echo 'This package requires an x86_64 Linux server.' >&2; exit 1;; esac
command -v docker >/dev/null
docker compose version
if [ ! -f .env ]; then echo 'Run bash init-config.sh, then edit .env first.' >&2; exit 1; fi
if grep -Eq 'CHANGE_ME|SERVER_IP' .env; then echo 'Replace the remaining placeholders in .env first.' >&2; exit 1; fi
sha256sum -c SHA256SUMS
docker load -i images.tar
for image in unibot/backend:0.3.0-amd64 unibot/frontend:0.3.0-amd64-superbot unibot/gateway:0.3.0-amd64 unibot/drawio:0.3.0-amd64 unibot/pptist-server:0.3.0-amd64 unibot/pptist-canvas:0.3.0-amd64 mysql:8.0 redis:7-alpine; do
  platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
  if [ "$platform" != linux/amd64 ]; then echo "Wrong platform for $image: $platform" >&2; exit 1; fi
done
docker compose -f compose.yml config --quiet
public_origin=$(sed -n 's/^UNIBOT_PUBLIC_ORIGIN=//p' .env | tr -d '\r')
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -e UNIBOT_PUBLIC_ORIGIN="$public_origin" -v "$PWD/apps:/apps" \
  --entrypoint python unibot/backend:0.3.0-amd64 /apps/configure-apps.py
docker compose -f compose.yml up -d --pull never --no-build --wait --wait-timeout 900
docker compose -f compose.yml ps
echo 'Unibot is ready. Open UNIBOT_FRONTEND_BASE_URL from .env.'
