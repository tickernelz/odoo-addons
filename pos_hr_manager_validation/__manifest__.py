{
    'name': 'POS Manager Validation',
    'summary': 'Sensitive point of sale actions wait for a manager validation PIN checked on the server, set per point of sale',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'author': 'tickernelz',
    'license': 'LGPL-3',
    'depends': ['pos_hr'],
    'data': [
        'security/ir.model.access.csv',
        'views/hr_employee_views.xml',
        'views/res_config_settings_views.xml',
        'views/pos_manager_validation_views.xml',
        'views/pos_session_views.xml',
        'views/pos_order_views.xml',
        'wizard/pos_manager_validation_wizard_views.xml',
        'wizard/pos_make_payment_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_hr_manager_validation/static/src/app/**/*',
        ],
        'web.assets_tests': [
            'pos_hr_manager_validation/static/tests/tours/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
