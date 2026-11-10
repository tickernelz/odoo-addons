{
    'name': 'POS Bank Card Programmes',
    'summary': 'Bank card programmes in the point of sale: BIN detection, discount, shared quota, bank claims',
    'version': '19.0.1.1.0',
    'category': 'Point of Sale',
    'author': 'tickernelz',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'account'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/pos_bank_card_claim_data.xml',
        'views/pos_bank_card_program_views.xml',
        'views/pos_payment_method_views.xml',
        'views/pos_bank_card_usage_views.xml',
        'views/pos_bank_card_claim_views.xml',
        'views/pos_bank_card_outstanding_views.xml',
        'wizard/pos_bank_card_claim_wizard_views.xml',
        'views/pos_bank_card_menus.xml',
    ],
    'demo': [
        'demo/demo.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_bank_card_program/static/src/language/language_dialog.js',
            'pos_bank_card_program/static/src/language/language_dialog.xml',
            'pos_bank_card_program/static/src/language/navbar.js',
            'pos_bank_card_program/static/src/language/navbar.xml',
        ],
    },
    'installable': True,
    'application': False,
}
