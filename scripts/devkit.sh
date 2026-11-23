#!/bin/sh
#
# Runs the development kit: the compose project smart-surveillance-dev from deploy/devkit/docker-compose.yml and
# its gitignored deploy/devkit/.env, with the simulated site of the devkit profile.
# Commands:
#   init: create deploy/devkit/.env from its .env.example with generated secrets and copy the sample videos
#   up: build and start the kit in the background
#   seed: create the demo organisation, its sites, members, cameras, analytics settings and history
#   anything else: passed to docker compose, as in scripts/devkit.sh logs -f backend

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
KIT=$ROOT/deploy/devkit
ENV=$KIT/.env

# Runs docker compose on the kit with the devkit profile unless COMPOSE_PROFILES names others.
# Arguments:
#   $@: the docker compose command and its arguments
compose() {
  COMPOSE_PROFILES=${COMPOSE_PROFILES:-devkit} docker compose -f "$KIT/docker-compose.yml" "$@"
}

# Prints 24 random bytes as hex.
# Outputs:
#   Writes the secret to stdout.
secret() {
  head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'
}

# Sets a variable of the kit's env file when it is still empty.
# Arguments:
#   $1: name of the variable
#   $2: value to set
fill() {
  key=$1
  value=$2
  if grep -q "^$key=$" "$ENV"; then
    sed -i "s|^$key=$|$key=$value|" "$ENV"
  fi
}

# Creates the kit's env file with generated secrets and copies the sample videos.
init() {
  if [ ! -f "$ENV" ]; then
    cp "$KIT/.env.example" "$ENV"
    chmod 600 "$ENV"
  fi
  fill SSS_SECRET_KEY "$(secret)"
  fill SSS_TV_RTSP_PASSWORD "$(secret)"
  fill SSS_ANALYTICS_TOKEN "$(secret)"
  fill SSS_DEV_CAMERA_PASSWORD "$(secret)"
  fill SSS_DEMO_PASSWORD "$(secret)"
  fill SSS_TELEGRAM_WEBHOOK_SECRET "$(secret)"
  sh "$ROOT/scripts/devkit-samples.sh"
}

if [ ! -f "$ENV" ] && [ "${1:-}" != "init" ]; then
  echo "devkit: run scripts/devkit.sh init first" >&2
  exit 1
fi

case "${1:-}" in
  init)
    init
    ;;
  up)
    shift
    compose up -d --build --wait "$@"
    ;;
  seed)
    shift
    compose exec -T backend python -m app.devkit.seed "$@"
    ;;
  *)
    compose "$@"
    ;;
esac
