from .common import McpCase, marker, new_token, new_user


class TestBatch(McpCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.writer = new_user(env, 'mcp_batch_writer')
            _token, self.writer_raw = new_token(env, self.writer, scope='write')

    def batch(self, steps, **arguments):
        return self.call(self.writer_raw, 'odoo_batch', dict(arguments, steps=steps))

    def count(self, *names):
        with self.shared_env() as env:
            return env['res.partner'].search_count([('name', 'in', list(names))])

    def test_steps_use_earlier_results_in_one_transaction(self):
        parent, child = marker(), marker()
        result = self.batch([
            {'op': 'create', 'model': 'res.partner', 'values': {'name': parent, 'is_company': True}},
            {'op': 'create', 'model': 'res.partner', 'values': {'name': child, 'parent_id': {'ref': '0.id'}}},
            {'op': 'write', 'model': 'res.partner', 'ids': [{'ref': '1.id'}], 'values': {'city': 'Bandung'}},
        ])
        self.assertFalse(result['isError'], result)
        self.assertEqual(result['structuredContent']['steps'], 3)
        with self.shared_env() as env:
            record = env['res.partner'].search([('name', '=', child)])
            self.assertEqual((record.parent_id.name, record.city), (parent, 'Bandung'))

    def test_a_failing_third_step_keeps_neither_earlier_step(self):
        first, renamed = marker(), marker()
        result = self.batch([
            {'op': 'create', 'model': 'res.partner', 'values': {'name': first}},
            {'op': 'write', 'model': 'res.partner', 'ids': [{'ref': '0.id'}], 'values': {'name': renamed}},
            {'op': 'write', 'model': 'res.partner', 'ids': [{'ref': '0.id'}], 'values': {'no_such_field': 1}},
        ])
        self.assertTrue(result['isError'])
        self.assertIn('Step 2 (write res.partner) failed', result['structuredContent']['error'])
        self.assertEqual(self.count(first, renamed), 0)

    def test_dry_run_reports_and_keeps_nothing(self):
        name = marker()
        result = self.batch([{'op': 'create', 'model': 'res.partner', 'values': {'name': name}}], dry_run=True)
        answer = result['structuredContent']
        self.assertTrue(answer['dry_run'])
        partners = next(row for row in answer['changes']['rows'] if row['table'] == 'res_partner')
        self.assertEqual((partners['model'], partners['inserted']), ('res.partner', 1))
        self.assertEqual(self.count(name), 0)
        self.assertTrue(self.last_call(self.writer)['dry_run'])

    def test_steps_keep_the_access_rights_of_the_token_user(self):
        name = marker()
        result = self.batch([
            {'op': 'create', 'model': 'res.partner', 'values': {'name': name}},
            {'op': 'create', 'model': 'ir.config_parameter', 'values': {'key': name, 'value': '1'}},
        ])
        error = result['structuredContent']['error']
        self.assertIn('Step 1 (create ir.config_parameter) failed', error)
        self.assertIn('You are not allowed to create', error)
        self.assertEqual(self.count(name), 0)
        with self.shared_env() as env:
            self.assertFalse(env['ir.config_parameter'].sudo().get_param(name))

    def test_steps_keep_the_scope_of_the_token(self):
        name = marker()
        result = self.batch([
            {'op': 'create', 'model': 'res.partner', 'values': {'name': name}},
            {'op': 'unlink', 'model': 'res.partner', 'ids': [{'ref': '0.id'}]},
        ])
        self.assertIn("requires 'admin' scope", result['structuredContent']['error'])
        self.assertEqual(self.count(name), 0)

    def test_steps_keep_the_model_limits_of_the_token(self):
        with self.shared_env() as env:
            token = env['mcp.token'].search([('user_id', '=', self.writer.id)])
            token.denied_model_ids = env['ir.model']._get('res.partner')
        result = self.batch([{'op': 'create', 'model': 'res.partner', 'values': {'name': marker()}}])
        self.assertIn('Step 0 (create res.partner) failed', result['structuredContent']['error'])
        self.assertIn('denied for this token', result['structuredContent']['error'])

    def test_refused_shapes(self):
        too_many = [{'op': 'create', 'model': 'res.partner', 'values': {'name': marker()}}] * 51
        self.assertIn('at most 50', self.batch(too_many)['structuredContent']['error'])
        forward = [{'op': 'write', 'model': 'res.partner', 'ids': [{'ref': '0.id'}], 'values': {}}]
        self.assertIn('earlier step number', self.batch(forward)['structuredContent']['error'])
        nested = [{'op': 'create', 'model': 'res.partner', 'values': {'name': 'x'}, 'dry_run': True}]
        self.assertIn('belongs to the whole batch', self.batch(nested)['structuredContent']['error'])
        other = [{'op': 'odoo_sql', 'model': 'res.partner'}]
        self.assertIn("'op' must be one of", self.batch(other)['structuredContent']['error'])
