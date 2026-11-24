# -*- coding: utf-8 -*-
"""Size fields in the companies' saved PO templates get keyboardType "size" (was "default")."""
import json

from odoo.addons.grg_config.models.res_company import grg_fill_keyboard_types, grg_set_size_keyboard


def migrate(cr, version):
    cr.execute("SELECT id, grg_po_template FROM res_company WHERE grg_po_template IS NOT NULL")
    for cid, raw in cr.fetchall():
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        changed = grg_set_size_keyboard(data)
        changed = grg_fill_keyboard_types(data) or changed
        if changed:
            cr.execute("UPDATE res_company SET grg_po_template = %s WHERE id = %s",
                       (json.dumps(data, indent=2, ensure_ascii=False), cid))
