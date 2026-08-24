# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    grg_quick_upi_enabled = fields.Boolean(string="Quick UPI Pay Button", default=True)
    grg_theme_enabled = fields.Boolean(string="GoRetailGo Branding Theme", default=True)
    grg_gst_tax_autoactivate = fields.Boolean(string="Auto-activate GST Purchase Taxes", default=True)

    def get_values(self):
        res = super().get_values()
        IrConfigSudo = self.env['ir.config_parameter'].sudo()
        res.update(
            grg_quick_upi_enabled=IrConfigSudo.get_param('grg_config.quick_upi_enabled', 'true') == 'true',
            grg_theme_enabled=IrConfigSudo.get_param('grg_config.theme_enabled', 'true') == 'true',
            grg_gst_tax_autoactivate=IrConfigSudo.get_param('grg_config.gst_tax_autoactivate', 'true') == 'true',
        )
        return res

    def set_values(self):
        res = super().set_values()
        IrConfigSudo = self.env['ir.config_parameter'].sudo()
        IrConfigSudo.set_param('grg_config.quick_upi_enabled', 'true' if self.grg_quick_upi_enabled else 'false')
        IrConfigSudo.set_param('grg_config.theme_enabled', 'true' if self.grg_theme_enabled else 'false')
        IrConfigSudo.set_param('grg_config.gst_tax_autoactivate', 'true' if self.grg_gst_tax_autoactivate else 'false')
        if self.grg_gst_tax_autoactivate:
            self._grg_activate_gst_taxes()
        return res

    def _grg_activate_gst_taxes(self):
        taxes = self.env['account.tax'].with_context(active_test=False).search([
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
