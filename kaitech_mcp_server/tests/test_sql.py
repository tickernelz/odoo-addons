# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError

from .common import EndpointCase, new_key, new_user, set_setting

SQL_TOOLS = ['sql_list_tables', 'sql_select', 'sql_query', 'sql_insert', 'sql_update', 'sql_delete']


class TestSql(EndpointCase):

    def setUp(self):
        super(TestSql, self).setUp()
        with self.shared_env() as env:
            set_setting(env, 'sql_enabled', 'True')
            self.user = new_user(env, 'mcp_sql_user')
            key, self.token = new_key(env, self.user, {}, tables={
                'res_partner': {'select', 'insert', 'update'}, 'res_country': {'select'}})
            self.key_id = key.id
            plain, self.plain_token = new_key(env, self.user, {'res.partner': {'read'}})
            self.plain_id = plain.id
            self.partners = env['res.partner'].create({'name': 'SQL One', 'city': 'Bandung'})
            for index in range(25):
                self.partners |= env['res.partner'].create({'name': 'SQL Bulk %02d' % index, 'city': 'Medan'})

    def tool_names(self, token):
        return [tool['name'] for tool in self.legacy('tools/list', token=token)[1]['result']['tools']]

    def test_sql_tools_need_all_three_switches(self):
        self.assertEqual([name for name in self.tool_names(self.token) if name.startswith('sql_')],
                         ['sql_list_tables', 'sql_select', 'sql_query', 'sql_insert', 'sql_update'])
        self.assertFalse([name for name in self.tool_names(self.plain_token) if name.startswith('sql_')])
        result = self.call(self.plain_token, 'sql_select', {'table': 'res_partner'})
        self.assertIn('Unknown table', result['structuredContent']['error'])
        with self.shared_env() as env:
            set_setting(env, 'sql_enabled', 'False')
        self.assertFalse([name for name in self.tool_names(self.token) if name.startswith('sql_')])
        with self.shared_env() as env:
            set_setting(env, 'sql_enabled', 'True')
            env['kaitech.mcp.key'].browse(self.key_id).write({'allow_sql': False})
        self.assertFalse([name for name in self.tool_names(self.token) if name.startswith('sql_')])

    def test_settings_administrators_never_get_sql(self):
        with self.shared_env() as env:
            admin_like = new_user(env, 'mcp_sql_sysadmin', groups=('base.group_user', 'base.group_system'))
            with self.assertRaisesRegexp(ValidationError, 'Settings administrator'):
                new_key(env, admin_like, {}, tables={'res_partner': {'select'}})
            env.cr.execute("INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s)",
                           (env.ref('base.group_system').id, self.user.id))
        self.assertFalse([name for name in self.tool_names(self.token) if name.startswith('sql_')])
        result = self.call(self.token, 'sql_select', {'table': 'res_partner'})
        self.assertIn('Unknown table', result['structuredContent']['error'])

    def test_secret_looking_columns_are_hidden(self):
        with self.shared_env() as env:
            env.cr.execute("ALTER TABLE res_country ADD COLUMN api_token varchar, ADD COLUMN client_secret varchar")
        result = self.call(self.token, 'sql_list_tables', {'query': 'res_country'})
        names = [column['name'] for column in result['structuredContent']['tables'][0]['columns']]
        self.assertIn('code', names)
        self.assertFalse(set(['api_token', 'client_secret']) & set(names))
        result = self.call(self.token, 'sql_query', {'query': 'SELECT api_token FROM res_country'})
        self.assertIn('never readable', result['structuredContent']['error'])

    def test_allow_sql_needs_the_setting(self):
        with self.shared_env() as env:
            set_setting(env, 'sql_enabled', 'False')
            with self.assertRaisesRegexp(ValidationError, 'SQL through MCP is switched off'):
                new_key(env, self.user, {}, tables={'res_partner': {'select'}})

    def test_forbidden_tables_cannot_be_granted_or_used(self):
        with self.shared_env() as env:
            for table in ('res_users', 'ir_config_parameter', 'kaitech_mcp_key', 'res_groups_users_rel'):
                with self.assertRaises((ValidationError, ValueError)):
                    new_key(env, self.user, {}, tables={table: {'select'}})
            env.cr.execute("INSERT INTO kaitech_mcp_sql_table (key_id, table_name, can_select) VALUES (%s, 'res_users', true)",
                           (self.key_id,))
        result = self.call(self.token, 'sql_select', {'table': 'res_users', 'columns': ['login']})
        self.assertIn('Unknown table', result['structuredContent']['error'])
        result = self.call(self.token, 'sql_query', {'query': 'SELECT login FROM res_users'})
        self.assertIn('may not read table res_users', result['structuredContent']['error'])

    def test_select_with_structured_where_and_paging(self):
        result = self.call(self.token, 'sql_select', {
            'table': 'res_partner', 'columns': ['id', 'name'], 'order': 'name asc',
            'where': ['|', ['city', '=', 'Bandung'], ['name', 'ilike', 'bulk 0']], 'limit': 5})
        rows = result['structuredContent']['rows']
        self.assertEqual([row['name'] for row in rows], ['SQL Bulk 00', 'SQL Bulk 01', 'SQL Bulk 02', 'SQL Bulk 03', 'SQL Bulk 04'])
        self.assertTrue(result['structuredContent']['has_more'])
        call = self.last_call(self.key_id)
        self.assertEqual((call['tool'], call['model'], call['record_count']), ('sql_select', 'res_partner', 5))
        for bad in ([['name', '=', {'x': 1}]], [['name', 'between', 1]], [['no_column', '=', 1]],
                    [['signup_token', '=', 'x']], ['|', ['name', '=', 'a']]):
            result = self.call(self.token, 'sql_select', {'table': 'res_partner', 'where': bad})
            self.assertTrue(result['isError'], bad)
        result = self.call(self.token, 'sql_select', {'table': 'res_partner', 'order': 'name; DROP TABLE res_partner'})
        self.assertTrue(result['isError'])

    def test_update_needs_where_and_respects_the_row_limit(self):
        for where in ([], None, ['&']):
            arguments = {'table': 'res_partner', 'values': {'city': 'X'}, 'where': where}
            result = self.call(self.token, 'sql_update', arguments)
            self.assertTrue(result['isError'], where)
        result = self.call(self.token, 'sql_update', {'table': 'res_partner', 'values': {'city': 'Padang'},
                                                      'where': [['city', '=', 'Medan']]})
        self.assertIn('matches 25 rows', result['structuredContent']['error'])
        with self.shared_env() as env:
            env.cr.execute("SELECT count(*) FROM res_partner WHERE city = 'Padang'")
            self.assertEqual(env.cr.fetchone()[0], 0)
        result = self.call(self.token, 'sql_update', {'table': 'res_partner', 'values': {'city': 'Padang'},
                                                      'where': [['name', 'in', ['SQL Bulk 01', 'SQL Bulk 02']]]})
        self.assertEqual(result['structuredContent']['updated'], 2)
        self.assertIn('UPDATE "res_partner" SET "city" = %s', self.statement()['statement'])
        with self.shared_env() as env:
            env.cr.execute("SELECT write_uid FROM res_partner WHERE city = 'Padang'")
            self.assertEqual(set(row[0] for row in env.cr.fetchall()), set([self.user.id]))

    def statement(self):
        with self.shared_env() as env:
            env.cr.execute("SELECT statement, dry_run, record_ids FROM kaitech_mcp_call WHERE key_id = %s ORDER BY id DESC LIMIT 1",
                           (self.key_id,))
            return env.cr.dictfetchone()

    def test_dry_run_reports_and_undoes(self):
        result = self.call(self.token, 'sql_update', {'table': 'res_partner', 'values': {'city': 'Ambon'},
                                                      'where': [['id', '=', self.partners[0].id]], 'dry_run': True})
        self.assertEqual(result['structuredContent'], {'updated': 1, 'keys': [self.partners[0].id], 'dry_run': True})
        with self.shared_env() as env:
            env.cr.execute("SELECT city FROM res_partner WHERE id = %s", (self.partners[0].id,))
            self.assertEqual(env.cr.fetchone()[0], 'Bandung')
        audit = self.statement()
        self.assertTrue(audit['dry_run'])
        self.assertEqual(audit['record_ids'], str(self.partners[0].id))
        self.assertNotIn('Ambon', audit['statement'])

    def test_delete_needs_the_delete_switch_and_is_bounded(self):
        self.assertNotIn('sql_delete', self.tool_names(self.token))
        with self.shared_env() as env:
            set_setting(env, 'delete_enabled', 'True')
            env['kaitech.mcp.key'].browse(self.key_id).sql_table_ids.filtered(
                lambda row: row.table_name == 'res_partner').write({'can_delete': True})
            victim = env['res.partner'].create({'name': 'SQL Victim'})
        self.assertIn('sql_delete', self.tool_names(self.token))
        result = self.call(self.token, 'sql_delete', {'table': 'res_partner', 'where': [['city', '=', 'Medan']]})
        self.assertIn('matches 25 rows', result['structuredContent']['error'])
        result = self.call(self.token, 'sql_delete', {'table': 'res_partner', 'where': [['id', '=', victim.id]]})
        self.assertEqual(result['structuredContent']['keys'], [victim.id])
        self.assertEqual(self.statement()['statement'], 'DELETE FROM "res_partner" WHERE "id" = %s RETURNING "id"')

    def test_insert_fills_audit_columns(self):
        result = self.call(self.token, 'sql_insert', {'table': 'res_partner', 'rows': [
            {'name': 'SQL Inserted', 'active': True, 'display_name': 'SQL Inserted'}]})
        self.assertFalse(result['isError'], result)
        new_id = result['structuredContent']['keys'][0]
        with self.shared_env() as env:
            env.cr.execute("SELECT create_uid, write_uid, create_date IS NOT NULL FROM res_partner WHERE id = %s", (new_id,))
            self.assertEqual(env.cr.fetchone(), (self.user.id, self.user.id, True))
        result = self.call(self.token, 'sql_insert', {'table': 'res_partner', 'rows': [{'id': 1, 'name': 'x'}]})
        self.assertTrue(result['isError'])
        result = self.call(self.token, 'sql_insert', {'table': 'res_country', 'rows': [{'name': 'x'}]})
        self.assertIn('may not insert', result['structuredContent']['error'])

    def test_database_errors_come_back_as_tool_errors(self):
        result = self.call(self.token, 'sql_update', {'table': 'res_partner', 'values': {'color': 'not a number'},
                                                      'where': [['id', '=', self.partners[0].id]]})
        self.assertTrue(result['isError'])
        self.assertIn('database refused', result['structuredContent']['error'])
        self.assertEqual(self.last_call(self.key_id)['result'], 'error')

    def test_query_reads_allowed_tables_only(self):
        result = self.call(self.token, 'sql_query', {'query': (
            "SELECT c.code, count(p.id) AS partners FROM res_partner p JOIN res_country c ON c.id = p.country_id "
            "WHERE p.name LIKE 'SQL %' GROUP BY c.code ORDER BY c.code")})
        self.assertFalse(result['isError'], result)
        self.assertEqual(result['structuredContent']['columns'], ['code', 'partners'])
        result = self.call(self.token, 'sql_query', {'query': "SELECT lower(name) AS n FROM res_partner WHERE city = 'Medan'",
                                                     'limit': 3})
        self.assertEqual(result['structuredContent']['returned'], 3)
        self.assertTrue(result['structuredContent']['has_more'])
        self.assertIn('lower(name)', self.statement()['statement'])

    def test_query_refuses_what_could_escape(self):
        refused = (
            "SELECT p FROM res_partner p",
            "SELECT name FROM res_partner; DELETE FROM res_partner",
            "DELETE FROM res_partner",
            "WITH gone AS (DELETE FROM res_partner RETURNING id) SELECT id FROM gone",
            "SELECT query_to_xml('select login from res_users', true, false, '')",
            "SELECT name FROM res_partner FOR UPDATE",
            "SELECT relname FROM pg_class",
            "SELECT pg_sleep(10)",
            "SELECT name FROM res_partner -- comment",
            "SELECT signup_token FROM res_partner",
            "SELECT set_config('transaction_read_only', 'off', true)",
            "SELECT login FROM res_partner p JOIN res_users u ON u.partner_id = p.id",
            "SELECT nextval('res_partner_id_seq')",
        )
        for query in refused:
            result = self.call(self.token, 'sql_query', {'query': query})
            self.assertTrue(result['isError'], query)
        with self.shared_env() as env:
            env.cr.execute("SELECT count(*) FROM res_partner WHERE name LIKE 'SQL Bulk%'")
            self.assertEqual(env.cr.fetchone()[0], 25)

    def test_settings_add_forbidden_columns(self):
        query = {'query': "SELECT ref FROM res_partner WHERE ref IS NOT NULL"}
        self.assertFalse(self.call(self.token, 'sql_query', query)['isError'])
        with self.shared_env() as env:
            set_setting(env, 'extra_forbidden_fields', 'res.partner.ref')
        self.assertIn('never readable', self.call(self.token, 'sql_query', query)['structuredContent']['error'])
        star = self.call(self.token, 'sql_query', {'query': 'SELECT * FROM res_partner'})
        self.assertIn('never readable', star['structuredContent']['error'])
        result = self.call(self.token, 'sql_select', {'table': 'res_partner', 'columns': ['ref']})
        self.assertIn('forbidden column', result['structuredContent']['error'])

    def test_query_time_limit(self):
        with self.shared_env() as env:
            set_setting(env, 'sql_statement_timeout_ms', '200')
        result = self.call(self.token, 'sql_query', {'query': (
            "SELECT count(a.id) AS n FROM res_partner a, res_partner b, res_partner c, res_partner d, res_partner e")})
        self.assertTrue(result['isError'])
        self.assertIn('timeout', result['structuredContent']['error'])

    def test_list_tables_hides_forbidden_columns(self):
        result = self.call(self.token, 'sql_list_tables', {})
        tables = dict((row['table'], row) for row in result['structuredContent']['tables'])
        self.assertEqual(sorted(tables), ['res_country', 'res_partner'])
        self.assertEqual(tables['res_partner']['primary_key'], ['id'])
        self.assertEqual(tables['res_country']['can'], {'select': True, 'insert': False, 'update': False, 'delete': False})
        names = [column['name'] for column in tables['res_partner']['columns']]
        self.assertIn('name', names)
        self.assertNotIn('signup_token', names)
