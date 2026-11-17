from .common import McpCase, marker, new_token, new_user


class TestReading(McpCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.reader = new_user(env, 'mcp_read_user')
            _token, self.reader_raw = new_token(env, self.reader, scope='read')
            self.name = marker()
            self.partner = env['res.partner'].create({'name': self.name, 'city': 'Bandung', 'comment': '<p>long</p>'})
            self.category = env['res.partner.category'].create({'name': self.name, 'partner_ids': [(4, self.partner.id)]})

    def read(self, model='res.partner', **arguments):
        record = self.partner if model == 'res.partner' else self.category
        result = self.call(self.reader_raw, 'odoo_search_read', dict(
            arguments, model=model, domain=[('id', '=', record.id)]))
        self.assertFalse(result['isError'], result)
        return result['structuredContent']['records'][0]

    def test_compact_reads_display_name_and_short_stored_fields(self):
        record = self.read(compact=True)
        self.assertEqual((record['display_name'], record['name'], record['city']), (self.name, self.name, 'Bandung'))
        for long_field in ('comment', 'image_1920', 'child_ids', 'message_ids'):
            self.assertNotIn(long_field, record)

    def test_without_compact_every_field_is_still_read(self):
        record = self.read('res.partner.category')
        self.assertEqual(record['partner_ids'], [self.partner.id])
        self.assertIn('child_ids', record)
        self.assertNotIn('partner_ids', self.read('res.partner.category', compact=True))

    def test_fields_given_win_over_compact(self):
        self.assertEqual(set(self.read(compact=True, fields=['comment'])), {'id', 'comment'})

    def test_read_group_without_fields_counts_each_group(self):
        result = self.call(self.reader_raw, 'odoo_read_group', {
            'model': 'res.partner', 'domain': [('name', '=', self.name)], 'groupby': ['city'],
        })
        self.assertFalse(result['isError'], result)
        self.assertEqual(result['structuredContent']['rows'], [{'city': 'Bandung', '__count': 1}])
