#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

xhost +local: >/dev/null 2>&1 || true

SIZE="$(DISPLAY="${DISPLAY:-:0}" xrandr 2>/dev/null | awk '/\*/{print $1; exit}')"
SCREEN_W="${SIZE%x*}"; SCREEN_H="${SIZE#*x}"
: "${SCREEN_W:=1920}"; : "${SCREEN_H:=1080}"

docker run --rm -it \
  --name smart-surveillance-system-cv \
  --network host \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e STREAM="${STREAM:-main}" \
  -e SCREEN_W="$SCREEN_W" -e SCREEN_H="$SCREEN_H" \
  --env-file ../../deploy/server/.env \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  smart-surveillance-system-cv:latest "$@"
