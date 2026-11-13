{
    'name': 'POS QRIS Xendit',
    'summary': 'Dynamic QRIS through Xendit in the point of sale, and a statement memo per payment for bank reconciliation',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'author': 'tickernelz',
    'website': 'https://github.com/tickernelz/odoo-addons',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'account'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/pos_payment_method_views.xml',
        'views/pos_xendit_qris_request_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_xendit_qris/static/src/app/**/*',
        ],
        'web.assets_tests': [
            'pos_xendit_qris/static/tests/tours/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
