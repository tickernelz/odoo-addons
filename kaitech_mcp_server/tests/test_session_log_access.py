from odoo.exceptions import AccessError
from odoo.service.model import call_kw
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.kaitech_mcp_server.services.mcp_protocol import process_message

@tagged("post_install", "-at_install")
class TestSessionLogAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(cls.env, login="zz_mcp_log_a", groups="base.group_user")
        cls.user_b = new_test_user(cls.env, login="zz_mcp_log_b", groups="base.group_user")
        cls.admin = new_test_user(
            cls.env, login="zz_mcp_log_admin", groups="base.group_user,base.group_system"
        )
        cls.session_a = cls._session(cls.user_a, "ZZ-MCP-LOG-A")
        cls.session_b = cls._session(cls.user_b, "ZZ-MCP-LOG-B")
        cls.log_a = cls._log(cls.session_a, cls.user_a, "odoo_search_read")
        cls.log_b = cls._log(cls.session_b, cls.user_b, "odoo_search_read")

    @classmethod
    def _session(cls, user, session_id):
        return cls.env["mcp.session"].create(
            {"session_id": session_id, "user_id": user.id, "transport": "http"}
        )

    @classmethod
    def _log(cls, session, user, tool_name):
        return cls.env["mcp.session.log"].create(
            {"session_id": session.id, "user_id": user.id, "tool_name": tool_name}
        )

    def _forged_values(self):
        return {
            "session_id": self.session_a.id,
            "user_id": self.user_b.id,
            "tool_name": "odoo_unlink",
            "is_error": False,
        }

    def test_internal_user_cannot_create_a_log_row_over_rpc(self):
        with self.assertRaises(AccessError):
            call_kw(
                self.env["mcp.session.log"].with_user(self.user_a),
                "create",
                [[self._forged_values()]],
                {},
            )

    def test_internal_user_cannot_rewrite_the_actor_column_over_rpc(self):
        with self.assertRaises(AccessError):
            call_kw(
                self.log_a.with_user(self.user_a),
                "write",
                [self.log_a.ids, {"user_id": self.user_b.id}],
                {},
            )

    def test_internal_user_cannot_erase_a_failure_over_rpc(self):
        with self.assertRaises(AccessError):
            call_kw(
                self.log_a.with_user(self.user_a),
                "write",
                [self.log_a.ids, {"is_error": False, "error_message": False}],
                {},
            )

    def test_internal_user_cannot_forge_a_log_row_through_the_mcp_create_tool(self):
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "odoo_create",
                "arguments": {"model": "mcp.session.log", "values": self._forged_values()},
            },
        }
        response = process_message(
            self.env(user=self.user_a), message,
            session_db_id=self.session_a.id, transport="http", scope="write",
        )
        self.assertTrue(response["result"]["isError"])
        forged = self.env["mcp.session.log"].sudo().search([("tool_name", "=", "odoo_unlink")])
        self.assertFalse(forged)

    def test_internal_user_reads_only_own_session_logs(self):
        logs = self.log_a | self.log_b
        domain = [("id", "in", logs.ids)]
        self.assertEqual(
            self.env["mcp.session.log"].with_user(self.user_a).search(domain), self.log_a
        )
        self.assertEqual(
            self.env["mcp.session.log"].with_user(self.user_b).search(domain), self.log_b
        )
        self.assertEqual(
            self.env["mcp.session.log"].with_user(self.admin).search(domain), logs
        )

    def test_the_mcp_writer_still_logs_the_acting_user(self):
        message = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "odoo_count", "arguments": {"model": "res.partner"}},
        }
        response = process_message(
            self.env(user=self.user_a), message,
            session_db_id=self.session_a.id, transport="http", scope="read",
        )
        self.assertFalse(response["result"]["isError"])
        rows = self.env["mcp.session.log"].sudo().search(
            [("session_id", "=", self.session_a.id), ("tool_name", "=", "odoo_count")]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.user_id, self.user_a)
        self.assertFalse(rows.is_error)
        self.assertEqual(rows.transport, "http")
