# -*- coding: utf-8 -*-
"""Business logic for the PO app APIs (replaces the n8n Dynamic Selector flows).

Every method runs as the API-key user, in the company chosen by the controller,
so Odoo access rights, record rules and multi-company rules all apply.
"""
import math
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.tools import SQL

GSTIN_RE = re.compile(r'^[0-9A-Z]{15}$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# Khatri POS discount fields on pos.config (only used when that module is installed)
F_FIRST = 'khatri_first_discount'
F_SECOND = 'khatri_second_discount'
F_CASCADE = 'khatri_enable_stepped_discount'
F_SECOND_ENABLE = 'khatri_enable_second_discount'


def _m2o(rec):
    return {'id': rec.id or None, 'name': rec.display_name if rec else None}


def _dt(value):
    return fields.Datetime.to_string(value) if value else None


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class GrgPoApi(models.AbstractModel):
    _name = 'grg.po.api'
    _description = 'GoRetailGo PO App API'

    # ------------------------------------------------------------------ auth
    @api.model
    def grg_whoami(self):
        user, company = self.env.user, self.env.company
        return {
            'success': True,
            'message': 'Authentication success',
            'status_code': 200,
            'db': self.env.cr.dbname,
            'user': {'id': user.id, 'name': user.name, 'login': user.login},
            'company': {'id': company.id, 'name': company.name, 'gstin': company.vat or None},
            'allowed_companies': [{'id': c.id, 'name': c.name} for c in user.company_ids],
            'access': {
                'purchase_user': user.has_group('purchase.group_purchase_user'),
                'purchase_manager': user.has_group('purchase.group_purchase_manager'),
            },
        }

    # -------------------------------------------------------- pos categories
    @api.model
    def grg_pos_categories(self):
        cats = self.env['pos.category'].search([])
        return {
            'success': True,
            'count': len(cats),
            'categories': [{'id': c.id, 'name': c.name} for c in cats],
        }

    # --------------------------------------------------------------- PO list
    @api.model
    def grg_po_list(self, page=1, size=20, search=None, state=None):
        page = max(_int(page, 1), 1)
        size = min(max(_int(size, 20), 1), 100)
        domain = [('company_id', '=', self.env.company.id)]
        if state:
            domain.append(('state', '=', state))
        if search:
            domain += ['|', '|', ('name', 'ilike', search), ('partner_ref', 'ilike', search),
                       ('partner_id.name', 'ilike', search)]
        PO = self.env['purchase.order']
        total = PO.search_count(domain)
        pos = PO.search(domain, offset=(page - 1) * size, limit=size, order='id desc')
        return {
            'success': True,
            'pagination': {
                'page': page,
                'size': size,
                'total': total,
                'total_pages': math.ceil(total / size) if total else 0,
                'has_next': page * size < total,
                'has_prev': page > 1,
            },
            'count': len(pos),
            'purchase_orders': [{
                'id': po.id,
                'name': po.name,
                'partner_id': po.partner_id.id or None,
                'partner_name': po.partner_id.display_name or None,
                'partner_ref': po.partner_ref or None,
                'date_order': _dt(po.date_order),
                'amount_total': po.amount_total,
                'currency_id': po.currency_id.id or None,
                'currency_name': po.currency_id.name or None,
                'state': po.state,
                'user_id': po.user_id.id or None,
                'user_name': po.user_id.name or None,
            } for po in pos],
        }

    # ------------------------------------------------------------- PO detail
    @api.model
    def grg_po_detail(self, po_id):
        po_id = _int(po_id, 0)
        if po_id < 1:
            raise UserError(_('Missing or invalid id. Use ?id=<PO_ID> in the URL.'))
        po = self.env['purchase.order'].search(
            [('id', '=', po_id), ('company_id', '=', self.env.company.id)], limit=1)
        if not po:
            raise MissingError(_('Purchase order %s not found in company %s.', po_id, self.env.company.name))

        def tax_dict(t):
            return {
                'id': t.id, 'name': t.name, 'amount': t.amount, 'amount_type': t.amount_type,
                'description': t.description or None, 'type': t.type_tax_use or None,
            }

        def product_dict(p):
            if not p:
                return None
            return {
                'id': p.id,
                'name': p.name,
                'internal_reference': p.default_code or None,
                'barcode': p.barcode or None,
                'category': p.categ_id.name or None,
                'category_id': p.categ_id.id or None,
                'uom': p.uom_id.name or None,
                'list_price': p.list_price,
                'standard_price': p.standard_price,
                'type': p.type,
                'active': p.active,
                'description_purchase': p.description_purchase or None,
                'description': (p.description and str(p.description)) or None,
            }

        lines = po.order_line.filtered(lambda l: not l.display_type)
        return {
            'success': True,
            'purchase_order': {
                'id': po.id,
                'name': po.name,
                'vendor': _m2o(po.partner_id),
                'vendor_reference': po.partner_ref or None,
                'currency': po.currency_id.name,
                'state': po.state,
                'buyer': _m2o(po.user_id),
                'date_order': _dt(po.date_order),
                'date_planned': _dt(po.date_planned),
                'deliver_to': po.picking_type_id.display_name if 'picking_type_id' in po._fields and po.picking_type_id else None,
                'amount_untaxed': po.amount_untaxed,
                'amount_tax': po.amount_tax,
                'amount_total': po.amount_total,
                'notes': (po.note and str(po.note)) or None,
            },
            'products': [{
                'line_id': line.id,
                'product': product_dict(line.product_id),
                'description': line.name,
                'quantity': line.product_qty,
                'unit': line.product_uom_id.name or None,
                'unit_price': line.price_unit,
                'taxes': [tax_dict(t) for t in line.tax_ids],
                'subtotal': line.price_subtotal,
                'total': line.price_total,
            } for line in lines],
        }

    # ------------------------------------------------------------- create PO
    @api.model
    def _grg_parse_party_key(self, data):
        """Returns ('gst', 'GSTIN') or ('mobile', '9876543210')."""
        raw = data.get('gst_number') or data.get('mobile_or_gst') or data.get('mobile') or ''
        raw = str(raw).strip()
        if not raw:
            raise UserError(_('gst_number (or mobile_or_gst) is required'))
        if not re.search(r'[A-Za-z]', raw):
            digits = re.sub(r'\D', '', raw)
            if len(digits) == 12 and digits.startswith('91'):
                digits = digits[2:]
            elif len(digits) == 11 and digits.startswith('0'):
                digits = digits[1:]
            if len(digits) == 10 and digits[0] in '6789':
                return 'mobile', digits
        gst = re.sub(r'\s', '', raw).upper()
        if not GSTIN_RE.match(gst):
            raise UserError(_('Enter a valid 15-character GST number or a 10-digit mobile number.'))
        return 'gst', gst

    @api.model
    def _grg_find_partner(self, kind, value):
        Partner = self.env['res.partner']
        if kind == 'gst':
            return Partner.search([('vat', '=ilike', value)], limit=1, order='id')
        # phone numbers are stored in many formats ("+91 84357 88847", "08435788847", ...)
        self.env['res.partner'].flush_model(['phone'])
        self.env.cr.execute(SQL(
            "SELECT id FROM res_partner WHERE active AND phone IS NOT NULL "
            "AND right(regexp_replace(phone, '\\D', '', 'g'), 10) = %s ORDER BY id",
            value))
        ids = [r[0] for r in self.env.cr.fetchall()]
        return Partner.search([('id', 'in', ids)], limit=1, order='id') if ids else Partner

    @api.model
    def grg_create_po(self, data):
        data = data or {}
        kind, key = self._grg_parse_party_key(data)
        party_name = str(data.get('party_name') or '').strip()
        bill_number = str(data.get('bill_number') or '').strip()
        bill_date = str(data.get('bill_date') or '').strip()
        if not party_name:
            raise UserError(_('party_name is required'))
        if not bill_number:
            raise UserError(_('bill_number is required'))
        if not bill_date:
            raise UserError(_('bill_date is required'))
        if not DATE_RE.match(bill_date):
            raise UserError(_('bill_date must be in YYYY-MM-DD format e.g. 2026-04-10'))
        try:
            fields.Date.from_string(bill_date)
        except ValueError:
            raise UserError(_('bill_date is not a valid date'))

        notes = 'Bill Number - %s<br/>' % bill_number
        transport = data.get('transport')
        if isinstance(transport, dict) and transport:
            notes += (
                'Transport Name - %s<br/>Bill Date - %s<br/>Transport Builty Number - %s<br/>'
                'Transport Fare - %s<br/>Parcel Quantity - %s<br/>' % (
                    transport.get('name') or '', bill_date, transport.get('builty_number') or '',
                    transport.get('fare') or '', transport.get('parcel_quantity') or ''))

        company = self.env.company
        partner = self._grg_find_partner(kind, key)
        is_new = not partner
        if is_new:
            vals = {
                'name': party_name,
                'company_type': 'company',
                'supplier_rank': 1,
                'country_id': company.country_id.id or False,
            }
            vals['vat' if kind == 'gst' else 'phone'] = key
            try:
                partner = self.env['res.partner'].create(vals)
            except AccessError:
                raise AccessError(_(
                    "Vendor with %(kind)s %(key)s does not exist and user %(user)s is not allowed to create "
                    "contacts. Create the vendor in Odoo first, or give this user the 'Contact Creation' right.",
                    kind='GST' if kind == 'gst' else 'mobile', key=key, user=self.env.user.name))

        po = self.env['purchase.order'].create({
            'partner_id': partner.id,
            'partner_ref': bill_number,
            'note': notes,
            'company_id': company.id,
        })
        return {
            'success': True,
            'po_id': po.id,
            'po_name': po.name,
            'partner_id': partner.id,
            'partner_name': partner.name,
            'is_new_party': is_new,
            'party_matched_by': kind,
            'bill_number': bill_number,
            'company_id': company.id,
            'message': 'Purchase Order created successfully. PO ID: %s' % po.id,
        }

    # ------------------------------------------------------- check PO by bill
    @api.model
    def grg_check_po_by_bill(self, bill_number=None, gst_number=None):
        bill_number = str(bill_number or '').strip()
        if not bill_number:
            raise UserError(_('bill_number is required'))
        gst = str(gst_number).upper().strip() if gst_number else None
        domain = [
            ('partner_ref', '=ilike', bill_number),
            ('state', '!=', 'cancel'),
            ('company_id', '=', self.env.company.id),
        ]
        if gst:
            domain.append(('partner_id.vat', '=ilike', gst))
        pos = self.env['purchase.order'].search(domain, order='id desc')
        available = bool(pos)
        return {
            'success': True,
            'bill_number': bill_number,
            'gst_number': gst,
            'available': available,
            'status': 'available' if available else 'not available',
            'message': ('PO already exists for bill number %s' if available
                        else 'No PO found for bill number %s') % bill_number,
            'count': len(pos),
            'purchase_orders': [{
                'po_id': p.id,
                'po_name': p.name,
                'partner_id': p.partner_id.id or None,
                'partner_name': p.partner_id.display_name or None,
                'bill_number': p.partner_ref,
                'state': p.state,
                'date_order': _dt(p.date_order),
                'amount_total': p.amount_total,
            } for p in pos],
        }

    # --------------------------------------------------------- configuration
    @api.model
    def _grg_pos_discount_label(self):
        """'50% + 25%' from the Khatri POS discount fields, or False when not applicable."""
        company = self.env.company
        if not company.grg_po_discount_from_pos:
            return False
        PosConfig = self.env['pos.config']
        if F_FIRST not in PosConfig._fields:
            return False
        pos = PosConfig.sudo().search([('company_id', '=', company.id)], limit=1, order='id')
        if not pos:
            return False

        def pct(v):
            try:
                n = float(v or 0)
            except (TypeError, ValueError):
                return None
            if not n:
                return None
            return ('%d' % n if n == int(n) else ('%s' % n)) + '%'

        first = pct(pos[F_FIRST])
        second = pct(pos[F_SECOND]) if F_SECOND in pos._fields else None
        cascade_on = bool(pos[F_CASCADE]) if F_CASCADE in pos._fields else False
        second_on = bool(pos[F_SECOND_ENABLE]) if F_SECOND_ENABLE in pos._fields else False
        if first and cascade_on and second_on and second:
            return '%s + %s' % (first, second)
        if first and cascade_on:
            return first
        if second and second_on and cascade_on:
            return second
        return None

    @api.model
    def _grg_apply_odoo_overrides(self, config):
        label = self._grg_pos_discount_label()
        if label is not False:
            if not isinstance(config.get('config_user_rp1'), dict):
                config['config_user_rp1'] = {}
            config['config_user_rp1']['discount_percentage'] = label
        # n8n always put the Odoo company name inside the config object
        config['company_name'] = self.env.company.name
        return config

    @api.model
    def grg_get_config(self):
        company = self.env.company
        config = self._grg_apply_odoo_overrides(company.grg_get_po_config())
        return {
            'success': True,
            'found': True,
            'row_id': company.id,
            'company_id': company.id,
            'company_name': company.name,
            'configurationObject': config,
        }

    @api.model
    def grg_update_config(self, incoming, replace=False):
        if not isinstance(incoming, dict) or isinstance(incoming, bool):
            raise UserError(_('Request body must be a JSON object representing the configuration.'))
        if not incoming:
            raise UserError(_('Empty configuration payload. POST a JSON object like { "config_user_rp1": {...} }.'))
        user = self.env.user
        if not (user.has_group('purchase.group_purchase_user') or user.has_group('base.group_system')):
            raise AccessError(_('You need Purchase access to update the configuration.'))
        company = self.env.company
        if replace:
            final = dict(incoming)
        else:
            final = company.grg_get_po_config()
            for key, new_val in incoming.items():
                old_val = final.get(key)
                if isinstance(old_val, dict) and isinstance(new_val, dict):
                    final[key] = {**old_val, **new_val}
                else:
                    final[key] = new_val
        final = self._grg_apply_odoo_overrides(final)
        # only this one field is written, after the group check above
        company.sudo().grg_set_po_config(final)
        return {
            'success': True,
            'updated': True,
            'row_id': company.id,
            'company_id': company.id,
            'company_name': company.name,
            'configurationObject': final,
        }

    # -------------------------------------------------------------- template
    @api.model
    def grg_get_template(self):
        company = self.env.company
        return {
            'success': True,
            'company_id': company.id,
            'template': company.grg_get_po_template(),
        }
