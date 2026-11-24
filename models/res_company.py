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
                "keyboardType": "default"
            },
            {
                "id": "bill_date",
                "label": "Bill Date",
                "type": "date",
                "required": True,
                "value": "2026-05-09",
                "keyboardType": "default"
            },
            {
                "id": "party_name",
                "label": "Party Name",
                "type": "text",
                "required": True,
                "value": "ZOYA FASHION",
                "keyboardType": "default"
            },
            {
                "id": "mobile_or_gst",
                "label": "Mobile Number or GST Number",
                "type": "text",
                "required": True,
                "value": "36CSZPS3344A3ZB",
                "helperText": "Enter mobile number OR GST number — either one is accepted.",
                "keyboardType": "default"
            }
        ],
        "products": {
            "title": "Products",
            "repeatable": True,
            "fields": [
                {
                    "id": "product_name",
                    "label": "Product Name",
                    "type": "text",
                    "required": True,
                    "value": "Cotton Kurti",
                    "helperText": "Start typing to pick an existing product.",
                    "keyboardType": "product"
                },
                {
                    "id": "category_name",
                    "label": "Category",
                    "type": "text",
                    "required": True,
                    "value": "Shirts",
                    "keyboardType": "default"
                },
                {
                    "id": "hsn_code",
                    "label": "HSN Code",
                    "type": "text",
                    "required": False,
                    "value": "6204",
                    "keyboardType": "numeric"
                },
                {
                    "id": "gst_percent",
                    "label": "GST %",
                    "type": "select",
                    "required": True,
                    "options": [
                        5,
                        12,
                        18,
                        28
                    ],
                    "value": 5,
                    "keyboardType": "numeric"
                },
                {
                    "id": "cost_price",
                    "label": "Cost Price",
                    "type": "number",
                    "required": True,
                    "value": 300,
                    "keyboardType": "numeric"
                },
                {
                    "id": "selling_price",
                    "label": "Selling Price",
                    "type": "number",
                    "required": False,
                    "value": 799,
                    "keyboardType": "numeric"
                }
            ],
            "variants": {
                "title": "Variants",
                "repeatable": True,
                "attributes": [
                    {
                        "name": "Size",
                        "label": "Size",
                        "type": "select",
                        "required": True,
                        "options": [
                            "S",
                            "M",
                            "L",
                            "XL",
                            "XXL"
                        ],
                        "value": "M",
                        "keyboardType": "size"
                    },
                    {
                        "name": "Colour",
                        "label": "Colour",
                        "type": "text",
                        "required": False,
                        "value": "Red",
                        "keyboardType": "default"
                    }
                ],
                "fields": [
                    {
                        "id": "quantity",
                        "label": "Quantity",
                        "type": "number",
                        "required": True,
                        "value": 2,
                        "keyboardType": "numeric"
                    },
                    {
                        "id": "cost_price",
                        "label": "Variant Cost Price",
                        "type": "number",
                        "required": False,
                        "helperText": "Leave empty to use the product cost price.",
                        "keyboardType": "numeric"
                    },
                    {
                        "id": "selling_price",
                        "label": "Variant Selling Price",
                        "type": "number",
                        "required": False,
                        "helperText": "Leave empty to use the product selling price.",
                        "keyboardType": "numeric"
                    }
                ]
            }
        }
    }
}


KEYBOARD_NUMERIC_IDS = {'hsn_code', 'quantity', 'cost_price', 'selling_price', 'mrp', 'gst_percent'}


def _grg_is_size_field(field):
    return str(field.get('id') or field.get('name') or '').strip().lower() == 'size'


def grg_set_size_keyboard(data):
    """Size fields that still have the generic "default" keyboard get "size". Returns True if changed."""
    changed = False

    def walk(node):
        nonlocal changed
        if isinstance(node, dict):
            for key in ('fields', 'attributes'):
                for f in node.get(key) or []:
                    if isinstance(f, dict) and _grg_is_size_field(f) and f.get('keyboardType') in (None, 'default'):
                        f['keyboardType'] = 'size'
                        changed = True
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return changed


def _grg_keyboard_type(field):
    """FE keyboard for a template field: product (product picker), size, numeric, or default."""
    fid = str(field.get('id') or '')
    if fid == 'product_name':
        return 'product'
    if _grg_is_size_field(field):
        return 'size'
    options = field.get('options')
    if (field.get('type') == 'number' or fid in KEYBOARD_NUMERIC_IDS
            or (field.get('type') == 'select' and isinstance(options, list) and options
                and all(isinstance(o, (int, float)) and not isinstance(o, bool) for o in options))):
        return 'numeric'
    return 'default'


def grg_fill_keyboard_types(data):
    """Adds "keyboardType" to every template field that has none (never overwrites). Returns True if changed."""
    changed = False

    def walk(node):
        nonlocal changed
        if isinstance(node, dict):
            for key in ('fields', 'attributes'):
                for f in node.get(key) or []:
                    if isinstance(f, dict) and 'keyboardType' not in f and ('id' in f or 'name' in f):
                        f['keyboardType'] = _grg_keyboard_type(f)
                        changed = True
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return changed


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

    grg_api_log_count = fields.Integer(string="API Logs", compute='_compute_grg_api_log_count')

    def _compute_grg_api_log_count(self):
        for company in self:
            company.grg_api_log_count = 0   # the viewer widget loads the logs itself

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
        data = _load_dict(self.grg_po_template, DEFAULT_PO_TEMPLATE)
        grg_fill_keyboard_types(data)
        return data

    def grg_set_po_config(self, data):
        self.ensure_one()
        self.grg_po_config = _dump(data)

    # ---------- UI buttons ----------
    def action_grg_reset_po_config(self):
        self.write({'grg_po_config': _dump(DEFAULT_PO_CONFIG)})

    def action_grg_reset_po_template(self):
        self.write({'grg_po_template': _dump(DEFAULT_PO_TEMPLATE)})
