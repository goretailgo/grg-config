# -*- coding: utf-8 -*-


def post_init_hook(env):
    """On install, activate the standard domestic GST purchase taxes
    (5/12/18/28%) across all companies and push them to the front of
    the tax selection dropdown, so they're usable on Purchase Orders
    without manual setup. Deliberately excludes IGST (interstate),
    reverse-charge (RC), import-service, and any test/custom taxes."""
    taxes = env['account.tax'].with_context(active_test=False).search([
        ('type_tax_use', '=', 'purchase'),
        ('amount', 'in', [5, 12, 18, 28]),
        ('amount_type', '=', 'percent'),
        ('name', 'ilike', 'GST'),
        ('name', 'not ilike', 'IGST'),
        ('name', 'not ilike', 'RC'),
        ('name', 'not ilike', 'Test'),
    ])
    if taxes:
        taxes.write({'active': True, 'sequence': -10})
