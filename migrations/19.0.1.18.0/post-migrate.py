# -*- coding: utf-8 -*-
"""Add "keyboardType" to every field of the companies' saved PO templates (existing values are kept)."""
import json

from odoo.addons.grg_config.models.res_company import grg_fill_keyboard_types


def migrate(cr, version):
    cr.execute("SELECT id, grg_po_template FROM res_company WHERE grg_po_template IS NOT NULL")
    for cid, raw in cr.fetchall():
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and grg_fill_keyboard_types(data):
            cr.execute("UPDATE res_company SET grg_po_template = %s WHERE id = %s",
                       (json.dumps(data, indent=2, ensure_ascii=False), cid))
