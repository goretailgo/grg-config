# -*- coding: utf-8 -*-
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        result = super().session_info()
        IrConfigSudo = self.env['ir.config_parameter'].sudo()
        result['grg_quick_upi_enabled'] = IrConfigSudo.get_param(
            'grg_config.quick_upi_enabled', 'true') == 'true'
        result['grg_theme_enabled'] = IrConfigSudo.get_param(
            'grg_config.theme_enabled', 'true') == 'true'
        return result
