{
    'name': 'GRG Config - GoRetailGo Customizations',
    'version': '19.0.1.3.0',
    'category': 'Point of Sale',
    'summary': 'GoRetailGo customer customizations bundle (POS, branding, tax defaults, settings)',
    'author': 'GoRetailGo',
    'depends': ['point_of_sale', 'web', 'account'],
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'grg_config/static/src/css/brand_theme.css',
            'grg_config/static/src/js/theme_toggle.js',
        ],
        'point_of_sale._assets_pos': [
            'grg_config/static/src/css/brand_theme.css',
            'grg_config/static/src/js/theme_toggle.js',
            'grg_config/static/src/app/screens/product_screen/action_pad/action_pad.js',
            'grg_config/static/src/app/screens/product_screen/action_pad/action_pad.xml',
            'grg_config/static/src/app/screens/payment_screen/payment_screen.js',
            'grg_config/static/src/app/screens/receipt_screen/receipt_screen.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
