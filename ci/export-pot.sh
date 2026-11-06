#!/bin/sh
set -eu
odoo_bin="${ODOO_BIN:-odoo}"
db="$1"
conf=$(mktemp)
cat > "$conf" <<EOF
[options]
addons_path = ${ADDONS_PATH}
db_host = ${DB_HOST:-postgres}
db_user = ${DB_USER:-odoo}
db_password = ${DB_PASSWORD:-odoo}
without_demo = True
EOF
$odoo_bin -c "$conf" -d "$db" -i kaitech_mcp --stop-after-init --log-level=warn >/tmp/install.log 2>&1 \
    || { cat /tmp/install.log >&2; exit 1; }
$odoo_bin --addons-path="$ADDONS_PATH" i18n export -c "$conf" -d "$db" -o /tmp/kaitech_mcp.pot kaitech_mcp >/tmp/export.log 2>&1 \
    || { cat /tmp/export.log >&2; exit 1; }
cat /tmp/kaitech_mcp.pot
