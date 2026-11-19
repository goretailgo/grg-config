# -*- coding: utf-8 -*-
"""Request/response log for the custom REST APIs (grg_config and other GoRetailGo modules).

Written by ir.http (_post_dispatch / _handle_error) in its own cursor, so a log survives
even when the API call itself fails and rolls back.
"""
import json
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

DEFAULT_PREFIXES = '/grg/api/,/stock-transfer/api/,/pos-module/,/grg_app/'


class GrgApiLog(models.Model):
    _name = 'grg.api.log'
    _description = 'GoRetailGo API Log'
    _order = 'id desc'
    _rec_name = 'path'

    request_time = fields.Datetime(required=True, index=True, default=fields.Datetime.now)
    method = fields.Char(required=True)
    path = fields.Char(required=True, index=True)
    query_string = fields.Char()
    status_code = fields.Integer(index=True)
    status_class = fields.Selection([('2xx', '2xx'), ('3xx', '3xx'), ('4xx', '4xx'), ('5xx', '5xx')],
                                    compute='_compute_status_class', store=True, index=True)
    duration_ms = fields.Float(digits=(16, 1))
    user_id = fields.Many2one('res.users', index=True, ondelete='set null')
    company_id = fields.Many2one('res.company', index=True, ondelete='cascade')
    ip = fields.Char()
    user_agent = fields.Char()
    request_headers = fields.Text()
    request_body = fields.Text()
    request_size = fields.Integer()
    response_content_type = fields.Char()
    response_body = fields.Text()
    response_size = fields.Integer()
    error_message = fields.Text()

    @api.depends('status_code')
    def _compute_status_class(self):
        for rec in self:
            c = rec.status_code or 0
            rec.status_class = '%dxx' % (c // 100) if 200 <= c < 600 else False

    # ------------------------------------------------------------- settings
    @api.model
    def _grg_log_settings(self):
        ICP = self.env['ir.config_parameter'].sudo()
        enabled = ICP.get_param('grg_config.api_log_enabled', 'true') == 'true'
        prefixes = [p.strip() for p in (ICP.get_param('grg_config.api_log_prefixes') or DEFAULT_PREFIXES).split(',') if p.strip()]
        try:
            max_body = int(ICP.get_param('grg_config.api_log_max_body', '100000'))
        except ValueError:
            max_body = 100000
        return enabled, tuple(prefixes), max_body

    # --------------------------------------------------------------- purge
    @api.model
    def _grg_purge_old_logs(self):
        try:
            days = int(self.env['ir.config_parameter'].sudo().get_param('grg_config.api_log_days', '15'))
        except ValueError:
            days = 15
        if days > 0:
            limit = fields.Datetime.now() - timedelta(days=days)
            self.sudo().search([('request_time', '<', limit)]).unlink()

    # ------------------------------------------------------- UI helpers (RPC)
    @api.model
    def grg_log_endpoints(self, company_id):
        """Distinct paths seen for this company (for the endpoint filter)."""
        self.check_access('read')
        self.env.cr.execute(
            "SELECT path, count(*) FROM grg_api_log WHERE company_id = %s OR company_id IS NULL "
            "GROUP BY path ORDER BY count(*) DESC LIMIT 100", [company_id])
        return [{'path': p, 'count': c} for p, c in self.env.cr.fetchall()]

    @api.model
    def grg_log_clear(self, company_id):
        self.check_access('unlink')
        self.search(['|', ('company_id', '=', company_id), ('company_id', '=', False)]).unlink()
        return True
