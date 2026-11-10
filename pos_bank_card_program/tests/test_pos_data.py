from odoo.tests import tagged

from .common import BankCardCommon


@tagged('post_install', '-at_install')
class TestPosData(BankCardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env['pos.config'].create({'name': 'Outlet 1'})

    def test_models_registered_for_pos_loading(self):
        models = self.env['pos.session']._load_pos_data_models(self.config)
        self.assertIn('pos.bank.card.program', models)
        self.assertIn('pos.bank.card.bin.range', models)

    def test_active_programmes_and_ranges_load(self):
        draft = self.env['pos.bank.card.program'].create({
            'name': 'Draft Programme',
            'bank_id': self.bank_b.id,
        })
        rows = self.env['pos.bank.card.program']._load_pos_data_search_read({}, self.config)
        loaded_ids = [row['id'] for row in rows]
        self.assertIn(self.program.id, loaded_ids)
        self.assertNotIn(draft.id, loaded_ids)
        programme_row = [row for row in rows if row['id'] == self.program.id][0]
        self.assertEqual(programme_row['discount_percent'], 15.0)
        range_rows = self.env['pos.bank.card.bin.range']._load_pos_data_search_read({}, self.config)
        loaded_bins = [row['bin_from'] for row in range_rows]
        self.assertIn('900100', loaded_bins)
