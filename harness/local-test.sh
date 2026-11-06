#!/bin/sh
set -eu
db="${1:-mcp10_t1}"
image="${ODOO_IMAGE:-odoo:10.0}"
extra="${EXTRA_INSTALL:-}"
root=$(cd "$(dirname "$0")/.." && pwd)
name="mcp10-test-$db"
docker rm -f "$name" >/dev/null 2>&1 || true
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT INT TERM
docker exec mcp10-db dropdb -U odoo --if-exists "$db"
args="--db_host mcp10-db --db_user odoo --db_password odoo -d $db --stop-after-init --without-demo=all"
if [ -n "$extra" ]; then
    docker run --rm --name "$name" --network mcp10 --memory 3g --cpus 4 -v "$root:/mnt/extra-addons:ro" \
        --entrypoint odoo "$image" $args -i "$extra" >/dev/null 2>&1
fi
docker run --rm --name "$name" --network mcp10 --memory 3g --cpus 4 -v "$root:/mnt/extra-addons:ro" --entrypoint odoo "$image" \
    $args -i kaitech_mcp_server --test-enable --load=base,web,kaitech_mcp_server --log-level=test 2>&1
