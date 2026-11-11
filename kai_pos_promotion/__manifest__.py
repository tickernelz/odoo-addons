{
    'name': 'POS Promotions',
    'summary': 'Tiered promotions, exclusive groups and happy hours for the point of sale, on top of Discount & Loyalty',
    'version': '19.0.1.0.1',
    'category': 'Point of Sale',
    'author': 'tickernelz',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'pos_loyalty', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/product_data.xml',
        'data/ir_cron_data.xml',
        'views/kai_pos_promo_views.xml',
        'views/loyalty_program_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'kai_pos_promotion/static/src/app/**/*',
        ],
        'point_of_sale.customer_display_assets': [
            'kai_pos_promotion/static/src/customer_display/**/*',
        ],
        'web.assets_tests': [
            'kai_pos_promotion/static/tests/tours/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
