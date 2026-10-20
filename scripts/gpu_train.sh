#!/usr/bin/env bash
# Optionally pause configured GPU services for the duration of a command.
# FC_GPU_CONTAINER=name FC_GPU_SERVICE=name scripts/gpu_train.sh python train.py ...
set -uo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
container=${FC_GPU_CONTAINER:-}
service=${FC_GPU_SERVICE:-}
container_stopped=0
service_stopped=0

restore() {
  if (( container_stopped )); then docker start "$container" >/dev/null; fi
  if (( service_stopped )); then systemctl --user start "$service"; fi
}
trap restore EXIT
if [[ -n "$container" ]] && [[ $(docker inspect -f '{{.State.Running}}' "$container") == true ]]; then
  docker stop "$container" >/dev/null || exit 1
  container_stopped=1
fi
if [[ -n "$service" ]] && systemctl --user is-active --quiet "$service"; then
  systemctl --user stop "$service" || exit 1
  service_stopped=1
fi
if (( container_stopped || service_stopped )); then sleep 5; fi
"$@"
status=$?
echo "[gpu_train] command exited with status $status"
exit "$status"
