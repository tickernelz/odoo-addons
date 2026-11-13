#!/bin/sh
set -eu
odoo_bin="${ODOO_BIN:-odoo}"
db="${TEST_DB:-mcp_test}"
addons="${ADDONS_PATH:-}"
log="${TEST_LOG:-odoo-tests.log}"
connection="--db_host ${DB_HOST:-postgres} --db_user ${DB_USER:-odoo} --db_password ${DB_PASSWORD:-odoo}"
paths=""
if [ -n "$addons" ]; then
    paths="--addons-path=$addons"
fi
for _ in $(seq 1 60); do
    if python3 -c "import psycopg2; psycopg2.connect(host='${DB_HOST:-postgres}', user='${DB_USER:-odoo}', password='${DB_PASSWORD:-odoo}', dbname='postgres')" 2>/dev/null; then
        break
    fi
    sleep 1
done
started=$(date +%s)
$odoo_bin $paths $connection -d "$db" -i kaitech_mcp_server,pos_bank_card_program,pos_checkout_extras,pos_hr_manager_validation,kai_pos_promotion,pos_xendit_qris --test-enable --test-tags /kaitech_mcp_server,/pos_bank_card_program,/pos_checkout_extras,/pos_hr_manager_validation,/kai_pos_promotion,/pos_xendit_qris --without-demo=all \
    --stop-after-init --load=base,web,kaitech_mcp_server > "$log" 2>&1 || true
finished=$(date +%s)
python3 - "$log" <<'EOF'
import re
import sys

text = open(sys.argv[1]).read()
results = re.findall(r"tests\.result: (\d+) failed, (\d+) error\(s\) of (\d+) tests", text)
broken = re.findall(r"ERROR \S+ odoo\.modules\.(?:module|loading|registry): .*", text)
failed = re.findall(r"(?:FAIL|ERROR): (Test\S+)", text)
print("tests.result: %r" % (results[-1] if results else None,))
for name in failed:
    print("  " + name)
for line in broken[:20]:
    print("  " + line)
if not results or results[-1][0] != '0' or results[-1][1] != '0' or int(results[-1][2]) == 0 or broken:
    sys.exit(1)
EOF
echo "module install + tests: $((finished - started)) s"
