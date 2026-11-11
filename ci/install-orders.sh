#!/bin/sh
set -eu
odoo_bin="${ODOO_BIN:-odoo}"
connection="--db_host ${DB_HOST:-postgres} --db_user ${DB_USER:-odoo} --db_password ${DB_PASSWORD:-odoo}"
paths=""
if [ -n "${ADDONS_PATH:-}" ]; then
    paths="--addons-path=$ADDONS_PATH"
fi
root=$(cd "$(dirname "$0")/.." && pwd)
run=0
for order in $(python3 "$root/ci/install_orders.py"); do
    run=$((run + 1))
    db="${TEST_DB_PREFIX:-install_order}_$run"
    echo "order $run: $order"
    for module in $(echo "$order" | tr ',' ' '); do
        log="/tmp/install-$run-$module.log"
        $odoo_bin $paths $connection -d "$db" -i "$module" --without-demo=all --stop-after-init --log-level=warn > "$log" 2>&1 \
            || { tail -40 "$log"; exit 1; }
        if grep -E "^[0-9-]+ [0-9:,]+ [0-9]+ (ERROR|CRITICAL) " "$log"; then
            exit 1
        fi
    done
    missing=$(python3 - "$db" "$order" <<'EOF'
import os
import sys

import psycopg2

connection = psycopg2.connect(host=os.environ.get("DB_HOST", "postgres"), user=os.environ.get("DB_USER", "odoo"),
                              password=os.environ.get("DB_PASSWORD", "odoo"), dbname=sys.argv[1])
cursor = connection.cursor()
cursor.execute("SELECT name FROM ir_module_module WHERE state = 'installed' AND name = ANY(%s)", [sys.argv[2].split(",")])
installed = {row[0] for row in cursor.fetchall()}
print(" ".join(sorted(set(sys.argv[2].split(",")) - installed)))
EOF
)
    if [ -n "$missing" ]; then
        echo "not installed after order $run: $missing"
        exit 1
    fi
    echo "order $run installed"
done
