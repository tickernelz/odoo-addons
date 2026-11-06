#!/bin/sh
set -eu
odoo_bin="${ODOO_BIN:-odoo}"
db="${TEST_DB:-mcp_test}"
extra="${EXTRA_INSTALL:-}"
addons="${ADDONS_PATH:-}"
log="${TEST_LOG:-odoo-tests.log}"
connection="--db_host ${DB_HOST:-postgres} --db_user ${DB_USER:-odoo} --db_password ${DB_PASSWORD:-odoo}"
paths=""
if [ -n "$addons" ]; then
    paths="--addons-path=$addons"
fi
for _ in $(seq 1 60); do
    if python -c "import psycopg2; psycopg2.connect(host='${DB_HOST:-postgres}', user='${DB_USER:-odoo}', password='${DB_PASSWORD:-odoo}', dbname='postgres')" 2>/dev/null; then
        break
    fi
    sleep 1
done
started=$(date +%s)
if [ -n "$extra" ]; then
    $odoo_bin $paths $connection -d "$db" -i "$extra" --without-demo=all --stop-after-init --log-level=warn > install.log 2>&1 \
        || { tail -50 install.log; exit 1; }
fi
installed=$(date +%s)
$odoo_bin $paths $connection -d "$db" -i kaitech_mcp_server --test-enable --without-demo=all --stop-after-init \
    --load=base,web,kaitech_mcp_server --log-level=test > "$log" 2>&1 || true
finished=$(date +%s)
python - "$log" <<'EOF'
import re
import sys

text = open(sys.argv[1]).read()
ran = sum(int(count) for count in re.findall(r"kaitech_mcp_server\.tests\.\S+: Ran (\d+) tests", text))
failed = re.findall(r"(?:FAIL|ERROR): (test\S+ \(odoo\.addons\.kaitech_mcp_server\.\S+\))", text)
broken = re.findall(r"ERROR \S+ odoo\.modules\.(?:module|loading): (?:Module kaitech_mcp_server:|(?!Module )).*", text)
others = re.findall(r"(?:FAIL|ERROR): (test\S+ \((?!odoo\.addons\.kaitech_mcp_server\.)\S+\))", text)
for name in others:
    print("  not this module, not counted: " + name)
tracebacks = text.count("Traceback (most recent call last)")
print("module tests ran: %s, failed: %s, module errors: %s, tracebacks: %s" % (ran, len(failed), len(broken), tracebacks))
for name in failed:
    print("  " + name)
for line in broken[:20]:
    print("  " + line)
if ran == 0 or failed or broken:
    start = text.find("Traceback (most recent call last)")
    if start >= 0:
        print(text[max(0, text.rfind("\n", 0, start - 300)):start + 3000])
    else:
        print(text[-3000:])
    sys.exit(1)
EOF
echo "extra install: $((installed - started)) s, module install + tests: $((finished - installed)) s"
