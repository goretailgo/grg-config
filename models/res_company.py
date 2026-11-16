# -*- coding: utf-8 -*-
import copy
import json

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

DEFAULT_PO_CONFIG = {
    "config_dev_rp1": {
        "padding": 2000000000000,
    },
    "config_user_rp1": {
        "discount_percentage": "50% + 25%",
        "mrp_roundof_by": 10,
        "profit_percentage_global": 50,
    },
}

DEFAULT_PO_TEMPLATE = {
    "form": {
        "title": "Bill Information",
        "fields": [
            {
                "id": "party_bill_number",
                "label": "Party Bill Number",
                "type": "text",
                "required": True,
                "value": "TI-02036",
            },
            {
                "id": "bill_date",
                "label": "Bill Date",
                "type": "date",
                "required": True,
                "value": "2026-05-09",
            },
            {
                "id": "party_name",
                "label": "Party Name",
                "type": "text",
                "required": True,
                "value": "ZOYA FASHION",
            },
            {
                "id": "mobile_or_gst",
                "label": "Mobile Number or GST Number",
                "type": "text",
                "required": True,
                "value": "36CSZPS3344A3ZB",
                "helperText": "Enter mobile number OR GST number — either one is accepted.",
            },
        ],
    },
}


def _dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def _load_dict(raw, default):
    if not raw:
        return copy.deepcopy(default)
    try:
        data = json.loads(raw)
    except ValueError:
        return copy.deepcopy(default)
    return data if isinstance(data, dict) else copy.deepcopy(default)


class ResCompany(models.Model):
    _inherit = 'res.company'

    grg_po_config = fields.Text(
        string="Configuration Object",
        default=lambda self: _dump(DEFAULT_PO_CONFIG),
        help="JSON returned by the PO app's configuration API.")
    grg_po_template = fields.Text(
        string="Template",
        default=lambda self: _dump(DEFAULT_PO_TEMPLATE),
        help="JSON form template returned by the PO app's get-template API.")
    grg_po_discount_from_pos = fields.Boolean(
        string="Discount % from POS",
        default=True,
        help="When the Khatri POS discount fields exist on the company's POS, the API overrides "
             "config_user_rp1.discount_percentage with the live POS discount (e.g. \"50% + 25%\").")

    @api.constrains('grg_po_config', 'grg_po_template')
    def _check_grg_po_json(self):
        for company in self:
            for fname, label in (('grg_po_config', _("Configuration Object")),
                                 ('grg_po_template', _("Template"))):
                raw = company[fname]
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except ValueError as e:
                    raise ValidationError(_("%(label)s is not valid JSON: %(err)s", label=label, err=e))
                if not isinstance(data, dict):
                    raise ValidationError(_("%(label)s must be a JSON object ({...}).", label=label))

    # ---------- helpers used by the API ----------
    def grg_get_po_config(self):
        self.ensure_one()
        return _load_dict(self.grg_po_config, DEFAULT_PO_CONFIG)

    def grg_get_po_template(self):
        self.ensure_one()
        return _load_dict(self.grg_po_template, DEFAULT_PO_TEMPLATE)

    def grg_set_po_config(self, data):
        self.ensure_one()
        self.grg_po_config = _dump(data)

    # ---------- UI buttons ----------
    def action_grg_reset_po_config(self):
        self.write({'grg_po_config': _dump(DEFAULT_PO_CONFIG)})

    def action_grg_reset_po_template(self):
        self.write({'grg_po_template': _dump(DEFAULT_PO_TEMPLATE)})
