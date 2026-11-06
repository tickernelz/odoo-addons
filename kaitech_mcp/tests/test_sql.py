from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.sql_db import db_connect

from .. import sql, tools
from .common import EndpointCase, marker, new_key, new_user, set_setting

SQL_TOOLS = ['sql_list_tables', 'sql_select', 'sql_query', 'sql_insert', 'sql_update', 'sql_delete']


class TestSql(EndpointCase):

    def setUp(self):
        super().setUp()
        self.mark = marker()
        self.bandung, self.medan, self.padang, self.ambon = ('%s %s' % (self.mark, city) for city in ('Bandung', 'Medan', 'Padang', 'Ambon'))
        with self.shared_env() as env:
            set_setting(env, 'sql_enabled', 'True')
            self.user = new_user(env, 'mcp_sql_user')
            key, self.token = new_key(env, self.user, {}, tables={
                'res_partner': {'select', 'insert', 'update'}, 'res_country': {'select'}})
            self.key_id = key.id
            plain, self.plain_token = new_key(env, self.user, {'res.partner': {'read'}})
            self.plain_id = plain.id
            self.partners = env['res.partner'].create({'name': '%s One' % self.mark, 'city': self.bandung})
            for index in range(25):
                self.partners |= env['res.partner'].create({'name': '%s Bulk %02d' % (self.mark, index), 'city': self.medan})

    def tool_names(self, token):
        return [tool['name'] for tool in self.legacy('tools/list', token=token)[1]['result']['tools']]

    def test_sql_tools_need_all_three_switches(self):
        self.assertEqual([name for name in self.tool_names(self.token) if name.startswith('sql_')],
                         ['sql_list_tables', 'sql_select', 'sql_query', 'sql_insert', 'sql_update'])
        self.assertFalse([name for name in self.tool_names(self.plain_token) if name.startswith('sql_')])
        result = self.call(self.plain_token, 'sql_select', {'table': 'res_partner'})
        self.assertIn('There is no tool named sql_select', result['structuredContent']['error'])
        with self.shared_env() as env:
            set_setting(env, 'sql_enabled', 'False')
        self.assertFalse([name for name in self.tool_names(self.token) if name.startswith('sql_')])
        with self.shared_env() as env:
            set_setting(env, 'sql_enabled', 'True')
            env['kaitech.mcp.key'].browse(self.key_id).write({'allow_sql': False})
        self.assertFalse([name for name in self.tool_names(self.token) if name.startswith('sql_')])

    def test_closed_sql_tools_run_no_statement(self):
        arguments = {
            'sql_list_tables': {}, 'sql_select': {'table': 'res_country', 'columns': ['code']},
            'sql_query': {'query': 'SELECT 1 AS n'}, 'sql_insert': {'table': 'res_country', 'rows': [{'code': 'QQ'}]},
            'sql_update': {'table': 'res_country', 'values': {'code': 'QQ'}, 'where': [['id', '=', 1]]},
            'sql_delete': {'table': 'res_country', 'where': [['id', '=', 1]]},
        }
        refused = {}
        for token in (self.plain_token, self.token):
            if token == self.token:
                with self.shared_env() as env:
                    set_setting(env, 'sql_enabled', 'False')
            for tool, values in arguments.items():
                with patch.object(sql.Run, '__init__', side_effect=AssertionError('sql.Run was built')):
                    result = self.call(token, tool, values)
                refused[(token == self.token, tool)] = result['structuredContent'].get('error', '')
        self.assertEqual({call: error for call, error in refused.items() if 'There is no tool named' not in error}, {})

    def test_settings_administrators_never_get_sql(self):
        env = self.env
        admin_like = new_user(env, 'mcp_sql_sysadmin', groups=('base.group_user', 'base.group_system'))
        with self.assertRaisesRegex(ValidationError, 'Settings administrator'):
            new_key(env, admin_like, {}, tables={'res_partner': {'select'}})
        with self.shared_env() as env:
            env.cr.execute("INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s)",
                           (env.ref('base.group_system').id, self.user.id))
        self.assertFalse([name for name in self.tool_names(self.token) if name.startswith('sql_')])
        result = self.call(self.token, 'sql_select', {'table': 'res_partner'})
        self.assertIn('There is no tool named sql_select', result['structuredContent']['error'])

    def test_secret_looking_columns_are_hidden(self):
        with self.shared_env() as env:
            env.cr.execute("ALTER TABLE res_country ADD COLUMN api_token varchar, ADD COLUMN client_secret varchar")
        result = self.call(self.token, 'sql_list_tables', {'query': 'res_country'})
        names = [column['name'] for column in result['structuredContent']['tables'][0]['columns']]
        self.assertIn('code', names)
        self.assertFalse(set(['api_token', 'client_secret']) & set(names))
        result = self.call(self.token, 'sql_query', {'query': 'SELECT api_token FROM res_country'})
        self.assertIn('never readable', result['structuredContent']['error'])
        leaks = (
            "SELECT x FROM (SELECT api_token AS x FROM res_country) s",
            "SELECT code FROM res_country WHERE api_token IS NOT NULL",
            "SELECT code FROM res_country ORDER BY client_secret",
            "SELECT count(*) FROM res_country GROUP BY api_token",
            "SELECT p.name FROM res_partner p JOIN res_country c ON c.api_token = p.name",
            "SELECT max(length(api_token)) FROM res_country",
        )
        for query in leaks:
            result = self.call(self.token, 'sql_query', {'query': query})
            self.assertTrue(result['isError'], query)
            self.assertIn('never readable', result['structuredContent']['error'], query)
        for query in ("SELECT count(*) AS n FROM res_country WHERE code = 'ID'",
                      "SELECT p.name, c.code FROM res_partner p JOIN res_country c ON c.id = p.country_id"):
            result = self.call(self.token, 'sql_query', {'query': query})
            self.assertFalse(result['isError'], (query, result['structuredContent']))

    def test_allow_sql_needs_the_setting(self):
        set_setting(self.env, 'sql_enabled', 'False')
        with self.assertRaisesRegex(ValidationError, 'SQL through MCP is switched off'):
            new_key(self.env, self.user, {}, tables={'res_partner': {'select'}})

    def test_forbidden_tables_cannot_be_granted_or_used(self):
        for table in ('res_users', 'ir_config_parameter', 'kaitech_mcp_key', 'res_groups_users_rel'):
            with self.assertRaises(ValidationError):
                new_key(self.env, self.user, {}, tables={table: {'select'}})
        with self.shared_env() as env:
            env.cr.execute("INSERT INTO kaitech_mcp_sql_table (key_id, table_name, can_select) VALUES (%s, 'res_users', true)",
                           (self.key_id,))
        result = self.call(self.token, 'sql_select', {'table': 'res_users', 'columns': ['login']})
        self.assertIn('Unknown table', result['structuredContent']['error'])
        result = self.call(self.token, 'sql_query', {'query': 'SELECT login FROM res_users'})
        self.assertIn('may not read table res_users', result['structuredContent']['error'])

    def test_select_with_structured_where_and_paging(self):
        result = self.call(self.token, 'sql_select', {
            'table': 'res_partner', 'columns': ['id', 'name'], 'order': 'name asc',
            'where': ['|', ['city', '=', self.bandung], ['name', 'ilike', '%s bulk 0' % self.mark]], 'limit': 5})
        rows = result['structuredContent']['rows']
        self.assertEqual([row['name'] for row in rows], ['%s Bulk %02d' % (self.mark, index) for index in range(5)])
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
            self.assertIn('where is required', result['structuredContent']['error'])
        result = self.call(self.token, 'sql_update', {'table': 'res_partner', 'values': {'city': self.padang},
                                                      'where': [['city', '=', self.medan]]})
        self.assertIn('matches more than 20 rows', result['structuredContent']['error'])
        with self.shared_env() as env:
            env.cr.execute("SELECT count(*) FROM res_partner WHERE city = %s", (self.padang,))
            self.assertEqual(env.cr.fetchone()[0], 0)
        result = self.call(self.token, 'sql_update', {'table': 'res_partner', 'values': {'city': self.padang},
                                                      'where': [['name', 'in', ['%s Bulk 01' % self.mark, '%s Bulk 02' % self.mark]]]})
        self.assertEqual(result['structuredContent']['updated'], 2)
        self.assertIn('UPDATE "res_partner" SET "city" = %s', self.statement()['statement'])
        with self.shared_env() as env:
            env.cr.execute("SELECT write_uid FROM res_partner WHERE city = %s", (self.padang,))
            self.assertEqual(set(row[0] for row in env.cr.fetchall()), set([self.user.id]))

    def test_over_limit_writes_are_refused_before_touching_a_row(self):
        with self.shared_env() as env:
            set_setting(env, 'delete_enabled', 'True')
            env['kaitech.mcp.key'].browse(self.key_id).sql_table_ids.filtered(
                lambda row: row.table_name == 'res_partner').write({'can_delete': True})
            env.cr.execute("""
                CREATE SEQUENCE mcp_test_touched;
                CREATE FUNCTION mcp_test_touch() RETURNS trigger LANGUAGE plpgsql AS
                    $$ BEGIN PERFORM nextval('mcp_test_touched'); RETURN COALESCE(NEW, OLD); END $$;
                CREATE TRIGGER mcp_test_touch BEFORE UPDATE OR DELETE ON res_partner
                    FOR EACH ROW EXECUTE FUNCTION mcp_test_touch();""")
        for tool, arguments in (('sql_update', {'values': {'city': self.padang}}), ('sql_update', {'values': {'city': self.padang}, 'dry_run': True}),
                                ('sql_delete', {}), ('sql_delete', {'dry_run': True})):
            result = self.call(self.token, tool, dict(arguments, table='res_partner', where=[['city', '=', self.medan]]))
            self.assertIn('Nothing was changed', result['structuredContent']['error'], (tool, arguments))
        with self.shared_env() as env:
            env.cr.execute("SELECT is_called FROM mcp_test_touched")
            self.assertFalse(env.cr.fetchone()[0])

    def test_audit_columns_are_never_written_by_the_caller(self):
        forged = (('create_uid', 1), ('write_uid', 1), ('create_date', '2001-01-01 00:00:00'),
                  ('write_date', '2001-01-01 00:00:00'))
        for column, value in forged:
            result = self.call(self.token, 'sql_insert', {'table': 'res_partner', 'rows': [{'name': self.mark, column: value}]})
            self.assertIn('filled in by MCP', result['structuredContent']['error'], column)
            result = self.call(self.token, 'sql_update', {'table': 'res_partner', 'values': {'city': self.ambon, column: value},
                                                          'where': [['id', '=', self.partners[0].id]]})
            self.assertIn('filled in by MCP', result['structuredContent']['error'], column)
        with self.shared_env() as env:
            env.cr.execute("SELECT count(*) FROM res_partner WHERE name = %s OR city = %s OR (id IN %s AND "
                           "(create_date < '2002-01-01' OR write_date < '2002-01-01'))",
                           (self.mark, self.ambon, tuple(self.partners.ids)))
            self.assertEqual(env.cr.fetchone()[0], 0)

    def test_time_limit_applies_to_sql_tools_only(self):
        with self.shared_env() as env:
            set_setting(env, 'sql_statement_timeout_ms', '100')
            _key, token = new_key(env, self.user, {'res.partner': {'read'}}, tables={'res_partner': {'select'}})
        count_records = tools._count_records

        def slow_count(env, grant, arguments):
            env.cr.execute("SELECT pg_sleep(0.3)")
            return count_records(env, grant, arguments)

        select = sql.Run.select

        def slow_select(run, arguments):
            run.cr.execute("SELECT pg_sleep(0.3)")
            return select(run, arguments)

        with patch.object(tools, '_count_records', slow_count):
            result = self.call(token, 'count_records', {'model': 'res.partner'})
        self.assertFalse(result['isError'], result)
        with patch.object(sql.Run, 'select', slow_select):
            result = self.call(token, 'sql_select', {'table': 'res_partner', 'columns': ['id'], 'limit': 1})
        self.assertIn('timeout', result['structuredContent']['error'])

    def statement(self):
        with self.shared_env() as env:
            env.cr.execute("SELECT statement, dry_run, record_ids FROM kaitech_mcp_call WHERE key_id = %s ORDER BY id DESC LIMIT 1",
                           (self.key_id,))
            return env.cr.dictfetchone()

    def test_dry_run_reports_and_undoes(self):
        result = self.call(self.token, 'sql_update', {'table': 'res_partner', 'values': {'city': self.ambon},
                                                      'where': [['id', '=', self.partners[0].id]], 'dry_run': True})
        self.assertEqual(result['structuredContent'], {'updated': 1, 'keys': [self.partners[0].id], 'dry_run': True})
        with self.shared_env() as env:
            env.cr.execute("SELECT city FROM res_partner WHERE id = %s", (self.partners[0].id,))
            self.assertEqual(env.cr.fetchone()[0], self.bandung)
        audit = self.statement()
        self.assertTrue(audit['dry_run'])
        self.assertEqual(audit['record_ids'], str(self.partners[0].id))
        self.assertNotIn(self.ambon, audit['statement'])

    def test_delete_needs_the_delete_switch_and_is_bounded(self):
        self.assertNotIn('sql_delete', self.tool_names(self.token))
        with self.shared_env() as env:
            set_setting(env, 'delete_enabled', 'True')
            env['kaitech.mcp.key'].browse(self.key_id).sql_table_ids.filtered(
                lambda row: row.table_name == 'res_partner').write({'can_delete': True})
            victim = env['res.partner'].create({'name': '%s Victim' % self.mark})
        self.assertIn('sql_delete', self.tool_names(self.token))
        result = self.call(self.token, 'sql_delete', {'table': 'res_partner', 'where': [['city', '=', self.medan]]})
        self.assertIn('matches more than 20 rows', result['structuredContent']['error'])
        result = self.call(self.token, 'sql_delete', {'table': 'res_partner', 'where': [['id', '=', victim.id]]})
        self.assertEqual(result['structuredContent']['keys'], [victim.id])
        self.assertEqual(self.statement()['statement'], 'DELETE FROM "res_partner" WHERE "id" = %s RETURNING "id"')

    def test_insert_fills_audit_columns(self):
        with self.shared_env() as env:
            env.cr.execute("CREATE TABLE mcp_test_note (id serial PRIMARY KEY, name varchar NOT NULL, create_uid integer, "
                           "create_date timestamp, write_uid integer, write_date timestamp)")
            _key, token = new_key(env, self.user, {}, tables={'mcp_test_note': {'insert'}})
        result = self.call(token, 'sql_insert', {'table': 'mcp_test_note', 'rows': [{'name': self.mark}]})
        self.assertFalse(result['isError'], result)
        new_id = result['structuredContent']['keys'][0]
        with self.shared_env() as env:
            env.cr.execute("SELECT create_uid, write_uid, create_date IS NOT NULL, write_date IS NOT NULL "
                           "FROM mcp_test_note WHERE id = %s", (new_id,))
            self.assertEqual(env.cr.fetchone(), (self.user.id, self.user.id, True, True))
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

    def test_column_aliases_cannot_rename_a_hidden_column(self):
        self.maxDiff = None
        with self.shared_env() as env:
            env.cr.execute("ALTER TABLE res_country ADD COLUMN api_token varchar")
            env.cr.execute("UPDATE res_country SET api_token = 'LEAKED-' || code")
            env.cr.execute("CREATE VIEW mcp_country_tags AS SELECT code, api_token AS tag FROM res_country")
            env.cr.execute("SELECT attname FROM pg_attribute WHERE attrelid = 'res_country'::regclass "
                           "AND attnum > 0 AND NOT attisdropped ORDER BY attnum")
            columns = [row[0] for row in env.cr.fetchall()]
        renamed = ', '.join('a%d' % index for index in range(1, len(columns) + 1))
        last = 'a%d' % len(columns)
        swapped = ', '.join('name' if column == 'api_token' else 'shown' if column == 'name' else column for column in columns)
        refused = (
            "SELECT x.%s FROM res_country AS x(%s)" % (last, renamed),
            "SELECT x.name FROM res_country AS x(%s)" % swapped,
            "SELECT x.name FROM res_country x(%s) JOIN res_partner p ON p.country_id = x.id" % swapped,
            "WITH x(%s) AS MATERIALIZED (SELECT * FROM res_country) SELECT x.%s FROM x" % (renamed, last),
            "WITH x(%s) AS MATERIALIZED (SELECT * FROM res_country) SELECT count(*) AS n FROM x WHERE x.%s LIKE 'LEAKED-I%%'" % (renamed, last),
            "SELECT s.%s FROM (SELECT * FROM res_country OFFSET 0) s(%s)" % (last, renamed),
            "SELECT * FROM res_country UNION ALL SELECT * FROM res_country",
            "SELECT count(*) AS n FROM res_country a JOIN res_country b USING (api_token)",
            "SELECT a.code FROM res_country a NATURAL JOIN res_country b",
            "SELECT tag FROM mcp_country_tags",
        )
        answers = {query: self.call(self.token, 'sql_query', {'query': query})['structuredContent'] for query in refused}
        self.assertEqual({query: answer.get('error') or answer['rows'][:1] for query, answer in answers.items()
                          if 'api_token is never readable' not in answer.get('error', '')}, {})
        allowed = (
            "SELECT x.n FROM res_country AS x(i, n) WHERE x.i = 1",
            "WITH x AS MATERIALIZED (SELECT code FROM res_country) SELECT x.code FROM x",
            "SELECT s.c FROM (SELECT code FROM res_country OFFSET 0) s(c)",
            "SELECT code FROM res_country UNION ALL SELECT code FROM res_country",
            "SELECT c.code FROM res_partner p JOIN res_country c ON c.id = p.country_id",
        )
        answers = {query: self.call(self.token, 'sql_query', {'query': query})['structuredContent'] for query in allowed}
        self.assertEqual({query: answer for query, answer in answers.items() if 'error' in answer or 'LEAKED-' in str(answer)}, {})

    def test_quotes_cannot_hide_a_second_statement(self):
        with self.shared_env() as env:
            env.cr.execute("SELECT id, phone_code FROM res_country WHERE code IN ('ID', 'MY', 'SG') ORDER BY code")
            countries = env.cr.fetchall()
            database = env.cr.dbname
        smuggled = [
            "SELECT 1 AS \"'\"; COMMIT; UPDATE res_country SET phone_code = 4545 WHERE id = %d; SELECT 1 AS \"'\"" % countries[0][0],
            "SELECT 1 AS \"'\") AS q; COMMIT; UPDATE res_country SET phone_code = 4242 WHERE id = %d; "
            "SELECT 1 FROM (SELECT 1 AS \"'\"" % countries[1][0],
            "SELECT E'\\'' AS a) AS q; COMMIT; UPDATE res_country SET phone_code = 4343 WHERE id = %d; "
            "SELECT 1 FROM (SELECT 'x'" % countries[2][0],
            "SELECT $q$'$q$ AS a) AS q; COMMIT; UPDATE res_country SET phone_code = 4646 WHERE id = %d; "
            "SELECT 1 FROM (SELECT $q$'$q$ AS b" % countries[0][0],
            "SELECT E'\\'' AS a) AS q; COMMIT; UPDATE res_country SET phone_code = 4747 WHERE id = %d; "
            "SELECT 1 FROM (SELECT E'\\'' AS b" % countries[1][0],
            "SELECT 'open AS a",
            "SELECT code FROM res_country) AS q, (SELECT 1",
        ]
        answers = {query: self.call(self.token, 'sql_query', {'query': query}) for query in smuggled}
        self.assertEqual({query: answer['structuredContent'] for query, answer in answers.items() if not answer['isError']}, {})
        with db_connect(database).cursor() as cr:
            cr.execute("SELECT id, phone_code FROM res_country WHERE id IN %s ORDER BY code", (tuple(row[0] for row in countries),))
            self.assertEqual(cr.fetchall(), countries)
        hidden_call = "SELECT \"'\".code FROM res_country \"'\" WHERE \"'\".code = 'x' || pg_backend_pid() || 'y'"
        result = self.call(self.token, 'sql_query', {'query': hidden_call})
        self.assertIn('pg_backend_pid is not allowed', result['structuredContent'].get('error', ''), result['structuredContent'])
        allowed = (
            "SELECT code AS \"semi;colon\", 'it''s; -- fine' AS s FROM res_country WHERE code = 'ID'",
            "SELECT \"'\".code FROM res_country \"'\" WHERE \"'\".code = 'ID'",
            "SELECT 1 AS \"a\"\"b\" FROM res_country WHERE code LIKE 'I%' LIMIT 1",
        )
        answers = {query: self.call(self.token, 'sql_query', {'query': query})['structuredContent'] for query in allowed}
        self.assertEqual({query: answer for query, answer in answers.items() if 'error' in answer}, {})

    def test_query_reads_allowed_tables_only(self):
        result = self.call(self.token, 'sql_query', {'query': (
            "SELECT c.code, count(p.id) AS partners FROM res_partner p JOIN res_country c ON c.id = p.country_id "
            "WHERE p.name LIKE '%s %%' GROUP BY c.code ORDER BY c.code" % self.mark)})
        self.assertFalse(result['isError'], result)
        self.assertEqual(result['structuredContent']['columns'], ['code', 'partners'])
        result = self.call(self.token, 'sql_query', {'query': "SELECT lower(name) AS n FROM res_partner WHERE city = '%s'" % self.medan,
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
            env.cr.execute("SELECT count(*) FROM res_partner WHERE name LIKE %s", ('%s Bulk%%' % self.mark,))
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
