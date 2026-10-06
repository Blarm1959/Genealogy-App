#!/usr/bin/env bash
set -Eeuo pipefail
# Run in a dedicated Debian LXC after cloning Genealogy-App. No server access is assumed.
[[ $EUID -eq 0 ]] || { echo 'Run as root inside your chosen LXC.' >&2; exit 1; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
port=${1:-8510}
[[ $port =~ ^[0-9]+$ && $port -ge 1024 && $port -le 65535 && $port -ne 18510 ]] || { echo 'Choose a port between 1024 and 65535.' >&2; exit 1; }
[[ $root =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo 'Installation path must not contain spaces or shell metacharacters.' >&2; exit 1; }
[[ ! -e /etc/genealogy/config.json && ! -e /etc/systemd/system/genealogy.service && ! -e /etc/nginx/sites-enabled/genealogy ]] || { echo 'Genealogy is already configured. Use update_lxc.sh.' >&2; exit 1; }
command -v node >/dev/null || { echo 'Install Node.js 22.12+ in this LXC first; see docs/SETUP.md in Genealogy.' >&2; exit 1; }
command -v npm >/dev/null || { echo 'npm is required alongside Node.js.' >&2; exit 1; }
node -e 'const [m,n]=process.versions.node.split(".").map(Number);if(m<22||(m===22&&n<12))process.exit(1)' || { echo 'Node.js 22.12 or later is required.' >&2; exit 1; }
apt-get update
apt-get install -y python3 python3-venv postgresql postgresql-client nginx git curl
systemctl enable --now postgresql
if runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname='genealogy'" | grep -q 1; then
 echo 'PostgreSQL role genealogy already exists. No database changes made; inspect before installing.' >&2; exit 1
fi
if runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname='genealogy'" | grep -q 1; then
 echo 'Database genealogy already exists. No database changes made.' >&2; exit 1
fi
getent passwd genealogy >/dev/null || useradd --system --home /var/lib/genealogy --shell /usr/sbin/nologin genealogy
install -d -m 750 -o root -g genealogy /etc/genealogy
install -d -m 700 -o genealogy -g genealogy /var/lib/genealogy
source "$root/admin/runtime_git.sh"
python3 -m venv "$root/.venv"
"$root/.venv/bin/pip" install -r "$root/requirements.txt"
cd "$root"
npm install --no-audit --no-fund
npm run build
dbpass=$("$root/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')
runuser -u postgres -- psql -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE genealogy LOGIN PASSWORD '$dbpass';
CREATE DATABASE genealogy OWNER genealogy;
SQL
GENEALOGY_SETUP_DATABASE="postgresql+psycopg://genealogy:$dbpass@127.0.0.1/genealogy" "$root/.venv/bin/python" -m backend.security --config /etc/genealogy/config.json --data-dir /var/lib/genealogy
unset dbpass
chown root:genealogy /etc/genealogy/config.json
chmod 640 /etc/genealogy/config.json
cat > /etc/systemd/system/genealogy.service <<UNIT
[Unit]
Description=Genealogy personal research
After=network.target postgresql.service
[Service]
User=genealogy
Group=genealogy
WorkingDirectory=$root
Environment=GENEALOGY_CONFIG=/etc/genealogy/config.json
ExecStart=$root/.venv/bin/python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 18510 --workers 1
Restart=on-failure
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/genealogy
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/nginx/sites-available/genealogy <<NGINX
server {
    listen $port;
    server_name _;
    client_max_body_size 26m;
    location / {
        proxy_pass http://127.0.0.1:18510;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 180s;
    }
}
NGINX
ln -s /etc/nginx/sites-available/genealogy /etc/nginx/sites-enabled/genealogy
nginx -t
systemctl daemon-reload
systemctl enable --now genealogy.service
systemctl enable --now nginx
systemctl reload nginx
for attempt in {1..30}; do
 if curl --fail --silent "http://127.0.0.1:$port/api/health" >/dev/null; then
  echo "Genealogy ready on your LXC's LAN address, port $port. Configure an off-container backup destination next."; exit 0
 fi
 sleep 1
done
echo 'Startup health check failed; inspect journalctl -u genealogy.service.' >&2
exit 1
