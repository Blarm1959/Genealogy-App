#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
source "$root/admin/runtime_git.sh"
tag=${1:?Pass the exact released tag, e.g. v1.0.1.}
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid version tag.' >&2; exit 1; }
[[ -f /etc/genealogy/config.json && -x .venv/bin/python ]] || { echo 'Run install_lxc.sh for the first installation.' >&2; exit 1; }
[[ -z $(git status --porcelain) ]] || { echo 'Working tree has local changes; update stopped.' >&2; exit 1; }
origin=$(git remote get-url origin)
[[ $origin == *Blarm1959/Genealogy-App.git || $origin == *Blarm1959/Genealogy.git ]] || { echo 'Unexpected repository origin; update stopped.' >&2; exit 1; }
git fetch origin --tags
# Check the tag exists before stopping the running app.
git rev-parse --verify "refs/tags/$tag^{commit}" >/dev/null
old_commit=$(git rev-parse HEAD)
systemctl stop genealogy.service
backup_dir=${GENEALOGY_UPDATE_BACKUPS:-/var/backups/genealogy}
cd "$root"
if ! "$root/.venv/bin/python" -m backend.backup --config /etc/genealogy/config.json --destination "$backup_dir"; then
 systemctl start genealogy.service; echo 'Backup failed; application unchanged.' >&2; exit 1
fi
rollback() {
 echo 'Update failed. Returning to the previous code and rebuilding its dependencies.' >&2
 git checkout --detach "$old_commit"
 "$root/.venv/bin/pip" install -r requirements.txt
 npm install --no-audit --no-fund
 npm run build
 systemctl start genealogy.service
 echo 'Database backup retained. If a future release migrates the schema, follow its restore instructions.' >&2
}
trap 'status=$?; trap - ERR; rollback; exit "$status"' ERR
git checkout --detach "$tag"
"$root/.venv/bin/pip" install -r requirements.txt
npm install --no-audit --no-fund
npm run build
systemctl start genealogy.service
healthy=false
for attempt in {1..30}; do
 if curl --fail --silent http://127.0.0.1:18510/api/health >/dev/null; then healthy=true; break; fi
 sleep 1
done
$healthy || { echo 'Startup health check failed.' >&2; false; }
trap - ERR
echo "Genealogy updated to $tag. Pre-update backup: $backup_dir"
