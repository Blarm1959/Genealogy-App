#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
destination=${1:?Pass an explicit backup destination directory.}
was_active=false
if systemctl is-active --quiet genealogy.service; then was_active=true; systemctl stop genealogy.service; fi
trap 'if $was_active; then systemctl start genealogy.service; fi' EXIT
cd "$root"
"$root/.venv/bin/python" -m backend.backup --config /etc/genealogy/config.json --destination "$destination"
echo 'Backup complete. Keep a copy outside the LXC; the archive includes private configuration.'
