"""One Odoo 19 process, three databases (db_a and db_b with the module, db_c without), real HTTP."""
import asyncio
import hashlib
import json
import os
import secrets
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
SERVER_PROTOCOL = "2025-03-26"


def host_for(db):
    return "%s.example.test" % db if SCENARIO == "dbfilter" else None


def admin_rpc(db):
    common = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/common")
    uid = common.authenticate(db, "admin", "admin", {})
    models = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/object", allow_none=True)

    def call(model, method, *args, **kwargs):
        return models.execute_kw(db, uid, "admin", model, method, list(args), kwargs)

    return call


def xmlid(call, name):
    module, _dot, record = name.partition(".")
    found = call("ir.model.data", "search_read", [("module", "=", module), ("name", "=", record)], fields=["res_id"])
    return found[0]["res_id"]


def provision(db):
    """A dedicated user, a marker partner, and a token an administrator issued for that user; return the raw token."""
    call = admin_rpc(db)
    groups = [xmlid(call, name) for name in ("base.group_user", "base.group_partner_manager")]
    login = "mcp_%s_%s_%s" % (SCENARIO, db, RUN)
    user_id = call("res.users", "create", {"name": login, "login": login, "group_ids": [(6, 0, groups)]})
    call("res.partner", "create", {"name": "Marker %s %s" % (db, RUN)})
    raw = secrets.token_urlsafe(32)
    call("mcp.token", "create", {
        "name": "harness %s" % db, "user_id": user_id, "token_type": "pat", "scope": "write",
        "token": hashlib.sha256(raw.encode()).hexdigest(),
    })
    return raw


@pytest.fixture(scope="session")
def tokens():
    return {"db_a": provision("db_a"), "db_b": provision("db_b")}


def post(db, body, token=None, headers=None, cookies=None, path=None):
    sent = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        sent["Authorization"] = "Bearer %s" % token
    if host_for(db):
        sent["Host"] = host_for(db)
    sent.update(headers or {})
    return requests.post(URL + (path or "/mcp/%s" % db), data=json.dumps(body), headers=sent, cookies=cookies,
                         timeout=60)


def rpc(db, method, params=None, token=None, **options):
    return post(db, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, token, **options)


def tool(db, token, name, arguments):
    response = rpc(db, "tools/call", {"name": name, "arguments": arguments}, token=token)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    return result["isError"], json.loads(result["content"][0]["text"]) if not result["isError"] else result


def search_markers(db, token, cookies=None):
    response = rpc(db, "tools/call", {"name": "odoo_search_read", "arguments": {
        "model": "res.partner", "domain": [["name", "like", "Marker %"]], "fields": ["name"], "limit": 200}},
        token=token, cookies=cookies)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    return [row["name"] for row in json.loads(result["content"][0]["text"])["records"]]


@pytest.mark.open
@pytest.mark.dbfilter
@pytest.mark.list_db_off
def test_each_token_opens_only_its_own_database(tokens):
    assert rpc("db_a", "tools/list", token=tokens["db_a"]).status_code == 200
    assert rpc("db_b", "tools/list", token=tokens["db_b"]).status_code == 200
    assert rpc("db_b", "tools/list", token=tokens["db_a"]).status_code == 401
    assert rpc("db_a", "tools/list", token=tokens["db_b"]).status_code == 401


@pytest.mark.open
@pytest.mark.list_db_off
def test_absent_databases_answer_one_identical_404(tokens):
    answers = [rpc(db, "tools/list", token=tokens["db_a"]) for db in ("db_c", "nope_%s" % RUN, "postgres")]
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
        assert rpc("db_a", "tools/list", token=tokens["db_a"]).status_code == 200
        assert rpc("db_c", "tools/list", token=tokens["db_a"]).status_code == 404
    after = len(os.listdir(sessions)) if os.path.isdir(sessions) else 0
    assert after == before


@pytest.mark.dbfilter
def test_dbfilter_follows_the_host(tokens):
    other_host = {"Host": "db_a.example.test"}
    assert rpc("db_b", "tools/list", token=tokens["db_b"], headers=other_host).status_code == 404
    assert rpc("db_a", "tools/list", token=tokens["db_a"], headers=other_host).status_code == 200


@pytest.mark.not_server_wide
def test_without_server_wide_the_database_header_selects_the_database(tokens):
    for db in ("db_a", "db_b"):
        assert rpc(db, "tools/list", token=tokens[db]).status_code == 404
        answer = rpc(db, "tools/list", token=tokens[db], path="/mcp", headers={"X-Odoo-Database": db})
        assert answer.status_code == 200, answer.text
        names = search_markers_by_header(db, tokens[db])
        assert names and all(name.startswith("Marker %s " % db) for name in names)


def search_markers_by_header(db, token):
    response = rpc(db, "tools/call", {"name": "odoo_search_read", "arguments": {
        "model": "res.partner", "domain": [["name", "like", "Marker %"]], "fields": ["name"]}},
        token=token, path="/mcp", headers={"X-Odoo-Database": db})
    assert response.status_code == 200, response.text
    return [row["name"] for row in json.loads(response.json()["result"]["content"][0]["text"])["records"]]


@pytest.mark.open
def test_explore_and_call_method_answer_per_database(tokens):
    switch_tools(True)
    try:
        explore_and_call_each_database(tokens)
    finally:
        switch_tools(False)


def switch_tools(on):
    for db in ("db_a", "db_b"):
        call = admin_rpc(db)
        token_id = call("mcp.token", "search", [("name", "=", "harness %s" % db)], order="id desc", limit=1)[0]
        call("mcp.token", "write", [token_id], {"allow_explore": on, "scope": "admin" if on else "write"})


def explore_and_call_each_database(tokens):
    for db in ("db_a", "db_b"):
        failed, module = tool(db, tokens[db], "describe_module", {"module": "kaitech_mcp_server"})
        assert failed is False, module
        assert "mcp.token" in module["models_defined"]
        failed, partner = tool(db, tokens[db], "odoo_search_read", {
            "model": "res.partner", "domain": [["name", "=", "Marker %s %s" % (db, RUN)]], "fields": ["name"]})
        partner_id = partner["records"][0]["id"]
        failed, found = tool(db, tokens[db], "odoo_call_method", {
            "model": "res.partner", "method": "address_get", "ids": [partner_id], "args": [["contact"]],
            "dry_run": True})
        assert failed is False, found
        assert found == {"result": {"contact": partner_id}, "dry_run": True}
    other = rpc("db_b", "tools/call", {"name": "describe_module", "arguments": {"module": "base"}}, token=tokens["db_a"])
    assert other.status_code == 401


async def sdk_session(db, token, mode):
    client = httpx2.AsyncClient(headers={"Authorization": "Bearer %s" % token}, timeout=60)
    transport = streamable_http_client("%s/mcp/%s" % (URL, db), http_client=client)
    async with Client(transport, mode=mode) as session:
        tools = await session.list_tools()
        result = await session.call_tool("odoo_search_read", {
            "model": "res.partner", "domain": [["name", "like", "Marker %"]], "fields": ["name"]})
        return session.protocol_version, [tool.name for tool in tools.tools], result


@pytest.mark.open
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_official_sdk_client_in_both_protocol_eras(tokens, mode):
    version, names, result = asyncio.run(sdk_session("db_a", tokens["db_a"], mode))
    assert version == SERVER_PROTOCOL
    assert "odoo_search_read" in names and "sql_select" not in names and "describe_model" not in names
    assert result.is_error is False
    rows = json.loads(result.content[0].text)["records"]
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
            answer = rpc("db_a", "tools/call", {"name": "odoo_search_read", "arguments": {
                "model": "res.partner", "domain": domain, "fields": ["name", "phone", "email"], "limit": size}},
                token=tokens["db_a"])
            mcp_times.append((time.perf_counter() - started) * 1000)
            assert answer.status_code == 200
            assert len(json.loads(answer.json()["result"]["content"][0]["text"])["records"]) == size
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
