"""One Odoo 10 process, three databases (db_a and db_b with the module, db_c without), real HTTP."""
import asyncio
import json
import os
import statistics
import time
import uuid
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest
import requests
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = os.environ.get("ODOO_URL", "http://odoo:8069")
SCENARIO = os.environ.get("SCENARIO", "open")
RUN = uuid.uuid4().hex[:6]
MODERN = "2026-07-28"


def host_for(db):
    return "%s.example.test" % db if SCENARIO == "dbfilter" else None


def admin_rpc(db):
    common = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/common")
    uid = common.authenticate(db, "admin", "admin", {})
    models = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/object", allow_none=True)

    def call(model, method, *args, **kwargs):
        return models.execute_kw(db, uid, "admin", model, method, list(args), kwargs)

    return call


def provision(db):
    """A dedicated user, a marker partner, and a key made through the key wizard; return the key."""
    call = admin_rpc(db)
    groups = [call("ir.model.data", "xmlid_to_res_id", name) for name in ("base.group_user", "base.group_partner_manager")]
    login = "mcp_%s_%s_%s" % (SCENARIO, db, RUN)
    user_id = call("res.users", "create", {"name": login, "login": login, "groups_id": [(6, 0, groups)]})
    call("res.partner", "create", {"name": "Marker %s %s" % (db, RUN)})
    model_id = call("ir.model", "search", [("model", "=", "res.partner")])[0]
    wizard = call("kaitech.mcp.key.wizard", "create", {"name": "harness %s" % db, "user_id": user_id})
    call("kaitech.mcp.key.wizard", "action_next", [wizard])
    call("kaitech.mcp.key.wizard", "write", [wizard], {"line_ids": [(0, 0, {"model_id": model_id, "can_read": True})]})
    call("kaitech.mcp.key.wizard", "action_create", [wizard])
    found = call("kaitech.mcp.key.wizard", "read", [wizard], ["token", "key_id"])[0]
    token = found["token"]
    assert token.startswith("o10mcp_")
    if SCENARIO == "perf":
        call("kaitech.mcp.key", "write", [found["key_id"][0]], {"rate_per_minute": 100000})
    return token


@pytest.fixture(scope="session")
def tokens():
    return {"db_a": provision("db_a"), "db_b": provision("db_b")}


def post(db, body, token=None, headers=None, cookies=None):
    sent = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        sent["Authorization"] = "Bearer %s" % token
    if host_for(db):
        sent["Host"] = host_for(db)
    sent.update(headers or {})
    return requests.post("%s/mcp/%s" % (URL, db), data=json.dumps(body), headers=sent, cookies=cookies, timeout=60)


def modern(db, method, params=None, token=None, headers=None, cookies=None):
    params = dict(params or {})
    params["_meta"] = {"io.modelcontextprotocol/protocolVersion": MODERN,
                       "io.modelcontextprotocol/clientCapabilities": {}}
    sent = {"Mcp-Protocol-Version": MODERN, "Mcp-Method": method}
    if method == "tools/call":
        sent["Mcp-Name"] = params["name"]
    sent.update(headers or {})
    return post(db, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, token, sent, cookies)


def search_markers(db, token, cookies=None):
    response = modern(db, "tools/call", {"name": "search_records", "arguments": {
        "model": "res.partner", "domain": [["name", "like", "Marker %"]], "fields": ["name"], "limit": 200}},
        token=token, cookies=cookies)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    return [row["name"] for row in result["structuredContent"]["records"]]


@pytest.mark.open
@pytest.mark.dbfilter
@pytest.mark.list_db_off
def test_each_key_opens_only_its_own_database(tokens):
    assert modern("db_a", "tools/list", token=tokens["db_a"]).status_code == 200
    assert modern("db_b", "tools/list", token=tokens["db_b"]).status_code == 200
    assert modern("db_b", "tools/list", token=tokens["db_a"]).status_code == 401
    assert modern("db_a", "tools/list", token=tokens["db_b"]).status_code == 401


@pytest.mark.open
@pytest.mark.dbfilter
@pytest.mark.list_db_off
def test_absent_databases_answer_one_identical_404(tokens):
    answers = [modern(db, "server/discover", token=tokens["db_a"]) for db in ("db_c", "nope_%s" % RUN, "postgres")]
    assert [answer.status_code for answer in answers] == [404, 404, 404]
    assert len(set(answer.content for answer in answers)) == 1


@pytest.mark.open
def test_forty_parallel_calls_never_cross_databases(tokens):
    jobs = ["db_a" if index % 2 == 0 else "db_b" for index in range(40)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        answers = list(pool.map(lambda db: (db, search_markers(db, tokens[db])), jobs))
    for db, names in answers:
        assert names, db
        assert all(name.startswith("Marker %s " % db) for name in names), (db, names)


@pytest.mark.open
def test_a_session_cookie_for_another_database_is_ignored(tokens):
    login = requests.post(URL + "/web/session/authenticate", json={"jsonrpc": "2.0", "method": "call", "params": {
        "db": "db_b", "login": "admin", "password": "admin"}}, timeout=30)
    cookies = login.cookies
    assert cookies.get("session_id")
    names = search_markers("db_a", tokens["db_a"], cookies=cookies)
    assert names and all(name.startswith("Marker db_a ") for name in names)


@pytest.mark.open
def test_mcp_calls_store_no_sessions(tokens):
    sessions = "/odoo-data/sessions"
    before = len(os.listdir(sessions)) if os.path.isdir(sessions) else 0
    for _ in range(20):
        assert modern("db_a", "tools/list", token=tokens["db_a"]).status_code == 200
        assert modern("db_c", "tools/list", token=tokens["db_a"]).status_code == 404
    after = len(os.listdir(sessions)) if os.path.isdir(sessions) else 0
    assert after == before


@pytest.mark.dbfilter
def test_dbfilter_follows_the_host(tokens):
    other_host = {"Host": "db_a.example.test"}
    assert modern("db_b", "tools/list", token=tokens["db_b"], headers=other_host).status_code == 404
    assert modern("db_a", "tools/list", token=tokens["db_a"], headers=other_host).status_code == 200


@pytest.mark.not_server_wide
def test_negative_control_without_server_wide_every_endpoint_is_404(tokens):
    for db in ("db_a", "db_b"):
        assert modern(db, "tools/list", token=tokens[db]).status_code == 404


async def sdk_session(db, token, mode):
    client = httpx2.AsyncClient(headers={"Authorization": "Bearer %s" % token}, timeout=60)
    transport = streamable_http_client("%s/mcp/%s" % (URL, db), http_client=client)
    async with Client(transport, mode=mode) as session:
        tools = await session.list_tools()
        result = await session.call_tool("search_records", {
            "model": "res.partner", "domain": [["name", "like", "Marker %"]], "fields": ["name"]})
        return session.protocol_version, [tool.name for tool in tools.tools], result


@pytest.mark.open
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_official_sdk_client_in_both_protocol_eras(tokens, mode):
    version, names, result = asyncio.run(sdk_session("db_a", tokens["db_a"], mode))
    assert version == (MODERN if mode == "auto" else "2025-11-25")
    assert names == ["list_models", "get_model_fields", "search_records", "count_records", "read_records"]
    assert result.is_error is False
    rows = result.structured_content["records"]
    assert rows and all(row["name"].startswith("Marker db_a ") for row in rows)


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


@pytest.mark.perf
def test_latency_against_plain_xmlrpc(tokens):
    call = admin_rpc("db_a")
    for index in range(200):
        call("res.partner", "create", {"name": "Perf %s %03d" % (RUN, index)})
    xmlrpc_uid = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/common").authenticate("db_a", "admin", "admin", {})
    models = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/object", allow_none=True)
    report = {}
    for size in (20, 200):
        domain = [["name", "like", "Perf %s %%" % RUN]]
        mcp_times, rpc_times = [], []
        for _ in range(60):
            started = time.perf_counter()
            answer = modern("db_a", "tools/call", {"name": "search_records", "arguments": {
                "model": "res.partner", "domain": domain, "fields": ["name", "phone", "email"], "limit": size}},
                token=tokens["db_a"])
            mcp_times.append((time.perf_counter() - started) * 1000)
            assert answer.status_code == 200 and len(answer.json()["result"]["structuredContent"]["records"]) == size
            started = time.perf_counter()
            rows = models.execute_kw("db_a", xmlrpc_uid, "admin", "res.partner", "search_read", [domain],
                                     {"fields": ["name", "phone", "email"], "limit": size})
            rpc_times.append((time.perf_counter() - started) * 1000)
            assert len(rows) == size
        report[size] = {
            "mcp_p50_ms": round(statistics.median(mcp_times), 1), "mcp_p95_ms": round(percentile(mcp_times, 0.95), 1),
            "xmlrpc_p50_ms": round(statistics.median(rpc_times), 1),
            "xmlrpc_p95_ms": round(percentile(rpc_times, 0.95), 1),
        }
        report[size]["overhead_p95_ms"] = round(report[size]["mcp_p95_ms"] - report[size]["xmlrpc_p95_ms"], 1)
    print("PERF " + json.dumps(report, sort_keys=True))
    with open("/tmp/perf.json", "w") as handle:
        json.dump(report, handle)
