#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_TEMPLATE="$SCRIPT_DIR/.env.example"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or is not available in PATH." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker in the fnOS Docker app first." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is unavailable. Install or enable the fnOS Docker app first." >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl is required to generate the initial secrets." >&2
    exit 1
  fi
  mysql_root_password=$(openssl rand -hex 24)
  mysql_password=$(openssl rand -hex 24)
  auth_secret=$(openssl rand -hex 32)
  sed \
    -e "s/CHANGE_ME_mysql_root_password/$mysql_root_password/" \
    -e "s/CHANGE_ME_mysql_password/$mysql_password/" \
    -e "s/CHANGE_ME_auth_secret_at_least_32_characters/$auth_secret/" \
    "$ENV_TEMPLATE" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE with generated local secrets."
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

http_port=$(sed -n 's/^UNIBOT_HTTP_PORT=//p' "$ENV_FILE" | tail -n 1)
backend_port=$(sed -n 's/^UNIBOT_BACKEND_PORT=//p' "$ENV_FILE" | tail -n 1)
echo "Unibot: http://192.168.1.8:${http_port:-8080}"
echo "API docs: http://192.168.1.8:${backend_port:-8000}/docs"
