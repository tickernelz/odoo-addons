import struct
from pathlib import Path

from odoo.modules.module import get_module_path
from odoo.tests.common import TransactionCase


class TestModuleIcon(TransactionCase):

    def test_icon_files_exist_and_png_is_at_least_128(self):
        description = Path(get_module_path('pos_checkout_extras')) / 'static' / 'description'
        self.assertTrue((description / 'icon.svg').is_file())
        self.assertTrue((description / 'index.html').is_file())
        data = (description / 'icon.png').read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        width, height = struct.unpack('>II', data[16:24])
        self.assertGreaterEqual(width, 128)
        self.assertGreaterEqual(height, 128)
