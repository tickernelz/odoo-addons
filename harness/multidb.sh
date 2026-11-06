#!/bin/sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
name=${HARNESS_NAME:-mcpmultidb}
odoo_image=${ODOO_IMAGE:-odoo:10.0}
scenarios=${*:-open dbfilter list_db_off not_server_wide}
net=$name-net
db=$name-db
odoo=$name-odoo
data=$name-data

cleanup() {
    docker rm -f "$odoo" "$db" >/dev/null 2>&1 || true
    docker volume rm "$data" >/dev/null 2>&1 || true
    docker network rm "$net" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

docker build -q -f "$root/harness/Dockerfile.odoo" --build-arg ODOO_IMAGE="$odoo_image" -t "$name-odoo-image" "$root" >/dev/null
docker build -q -f "$root/harness/Dockerfile.pytest" -t "$name-pytest-image" "$root" >/dev/null
docker network create "$net" >/dev/null
docker volume create "$data" >/dev/null
docker run -d --name "$db" --network "$net" --memory 1g -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo \
    -e POSTGRES_DB=postgres postgres:9.6 >/dev/null
until docker exec "$db" pg_isready -U odoo -q; do sleep 1; done
sleep 2

odoo_run() {
    docker run --rm --network "$net" --memory 2g -v "$data:/var/lib/odoo" --entrypoint odoo "$name-odoo-image" \
        --db_host "$db" --db_user odoo --db_password odoo --without-demo=all --stop-after-init "$@"
}
odoo_run -d db_a -i kaitech_mcp_server >/dev/null 2>&1
odoo_run -d db_b -i kaitech_mcp_server >/dev/null 2>&1
odoo_run -d db_c -i base >/dev/null 2>&1

start_odoo() {
    docker rm -f "$odoo" >/dev/null 2>&1 || true
    docker run -d --name "$odoo" --network "$net" --network-alias odoo --memory 2g -v "$data:/var/lib/odoo" \
        --entrypoint odoo "$name-odoo-image" --db_host "$db" --db_user odoo --db_password odoo \
        --workers=2 --max-cron-threads=0 --limit-time-real=120 "$@" >/dev/null
    for _ in $(seq 1 60); do
        if docker run --rm --network "$net" "$name-pytest-image" python -c \
            "import requests,sys; sys.exit(0 if requests.post('http://odoo:8069/web/webclient/version_info', json={}, timeout=2).ok else 1)" \
            >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    docker logs "$odoo" | tail -40
    return 1
}

status=0
for scenario in $scenarios; do
    case "$scenario" in
        open) start_odoo --load=base,web,kaitech_mcp_server ;;
        perf) start_odoo --load=base,web,kaitech_mcp_server ;;
        dbfilter) start_odoo --load=base,web,kaitech_mcp_server '--db-filter=^%d$' ;;
        list_db_off) start_odoo --load=base,web,kaitech_mcp_server --no-database-list ;;
        not_server_wide) start_odoo --load=base,web ;;
        *) echo "unknown scenario $scenario"; exit 2 ;;
    esac
    echo "== scenario $scenario"
    if ! docker run --rm --network "$net" -v "$data:/odoo-data:ro" -e SCENARIO="$scenario" \
        -e ODOO_URL=http://odoo:8069 "$name-pytest-image" pytest -q -p no:cacheprovider -m "$scenario or always" \
        test_multidb.py; then
        status=1
        docker logs "$odoo" 2>&1 | grep -E "ERROR|Traceback" | tail -20 || true
    fi
done
exit $status
