#!/bin/sh
set -eu
odoo_bin="${ODOO_BIN:-odoo}"
db="$1"
shift
$odoo_bin "$@" -d "$db" -i kaitech_mcp --without-demo=all --stop-after-init --log-level=warn >/tmp/install.log 2>&1 \
    || { cat /tmp/install.log >&2; exit 1; }
$odoo_bin i18n export "$@" -d "$db" -o /tmp/kaitech_mcp.pot kaitech_mcp >/tmp/export.log 2>&1 \
    || { cat /tmp/export.log >&2; exit 1; }
cat /tmp/kaitech_mcp.pot
