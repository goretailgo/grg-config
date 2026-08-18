{
    'name': 'GRG Config - GoRetailGo Customizations',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'GoRetailGo customer customizations bundle (POS, etc.)',
    'author': 'GoRetailGo',
    'depends': ['point_of_sale'],
    'assets': {
        'point_of_sale._assets_pos': [
            'grg_config/static/src/app/screens/product_screen/action_pad/action_pad.js',
            'grg_config/static/src/app/screens/product_screen/action_pad/action_pad.xml',
            'grg_config/static/src/app/screens/payment_screen/payment_screen.js',
            'grg_config/static/src/app/screens/receipt_screen/receipt_screen.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
