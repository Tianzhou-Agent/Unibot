#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ -e .env ]; then
  echo '.env already exists; edit it directly.' >&2
  exit 1
fi
umask 077
secret() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }
mysql_root=$(secret)
mysql_user=$(secret)
auth_secret=$(secret)
sed -e "s/^UNIBOT_MYSQL_ROOT_PASSWORD=.*/UNIBOT_MYSQL_ROOT_PASSWORD=$mysql_root/" \
    -e "s/^UNIBOT_MYSQL_PASSWORD=.*/UNIBOT_MYSQL_PASSWORD=$mysql_user/" \
    -e "s/^UNIBOT_AUTH_SECRET=.*/UNIBOT_AUTH_SECRET=$auth_secret/" \
    .env.example > .env
echo 'Created .env with fresh secrets. Set SERVER_IP and your internal LLM endpoint before installation.'
