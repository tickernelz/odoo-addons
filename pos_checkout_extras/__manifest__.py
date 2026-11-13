{
    'name': 'POS Checkout Extras',
    'summary': 'Point of sale checkout: required customer, payment reference, analytic accounts per counter',
    'version': '19.0.1.1.0',
    'category': 'Point of Sale',
    'author': 'tickernelz',
    'website': 'https://github.com/tickernelz/odoo-addons',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'views/res_config_settings_views.xml',
        'views/pos_config_views.xml',
        'views/pos_order_views.xml',
        'views/pos_payment_views.xml',
        'views/pos_analytic_sync_log_views.xml',
        'wizard/pos_analytic_sync_wizard_views.xml',
        'wizard/pos_analytic_coverage_wizard_views.xml',
        'report/pos_analytic_coverage_report.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_checkout_extras/static/src/app/**/*',
        ],
        'web.assets_tests': [
            'pos_checkout_extras/static/tests/tours/**/*',
        ],
    },
    'pre_init_hook': 'pre_init_hook',
    'installable': True,
    'application': False,
}
