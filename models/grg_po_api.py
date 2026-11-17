# -*- coding: utf-8 -*-
"""Business logic for the PO app APIs (replaces the n8n Dynamic Selector flows).

Every method runs as the API-key user, in the company chosen by the controller,
so Odoo access rights, record rules and multi-company rules all apply.
"""
import html
import json
import math
import re
import time
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.tools import SQL, html_escape

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


def _n(x):
    """2.0 -> 2 (n8n/JS returned whole numbers as ints)."""
    return int(x) if isinstance(x, float) and x.is_integer() else x


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

    # ------------------------------------------- bulk PO + products + variants
    @api.model
    def grg_save_po_variants(self, body):
        """Port of the n8n 'AI Complete Workflow (variants) - BULK - v2' (parser-variants-v2).

        No po_id  -> create a new PO (duplicate bill check first).
        po_id     -> add products to that existing PO (same variant = qty increased).
        Runs in one DB transaction: any error rolls everything back.
        """
        t0 = time.time()
        body = body if isinstance(body, dict) else {}
        company = self.env.company
        env = self.with_context(tracking_disable=True, mail_create_nolog=True, mail_notrack=True).env
        lc = lambda s: str(s).strip().lower()

        def need(v, n):
            if v is None or str(v).strip() == '':
                raise UserError(_('%s is required', n))

        def num(v, n):
            try:
                return float(v)
            except (TypeError, ValueError):
                raise UserError(_('%s must be a number', n))

        # ---------------- mode + header fields
        add_po_id = None
        if body.get('po_id') not in (None, ''):
            try:
                add_po_id = int(str(body['po_id']).strip())
            except ValueError:
                add_po_id = 0
            if add_po_id <= 0:
                raise UserError(_('po_id must be a positive integer'))
        mode = 'add' if add_po_id else 'create'

        bill_number = str(body['bill_number']).strip() if body.get('bill_number') is not None else ''
        bill_date = str(body.get('bill_date') or '').strip()
        party_name = str(body.get('party_name') or '').strip()
        if mode == 'create':
            kind, party_key = self._grg_parse_party_key(body)
            need(party_name, 'party_name')
            need(bill_number, 'bill_number')
            need(bill_date, 'bill_date')
        if bill_date and not DATE_RE.match(bill_date):
            raise UserError(_('bill_date must be in YYYY-MM-DD format e.g. 2026-04-10'))

        notes = 'Bill Number - %s<br/>' % (body.get('bill_number') if body.get('bill_number') is not None else '')
        transport = body.get('transport')
        if isinstance(transport, dict) and transport:
            notes += (
                'Transport Name - %s<br/>Bill Date - %s<br/>Transport Builty Number - %s<br/>'
                'Transport Fare - %s<br/>Parcel Quantity - %s<br/>' % (
                    transport.get('name') or '', bill_date, transport.get('builty_number') or '',
                    transport.get('fare') or '', transport.get('parcel_quantity') or ''))

        raw_products = body.get('products')
        if not isinstance(raw_products, list) or not raw_products:
            raise UserError(_('products must be a non-empty array'))

        # ---------------- normalise products / variants
        products = []
        for pi, p in enumerate(raw_products):
            if not isinstance(p, dict):
                raise UserError(_('products[%s] must be an object', pi))
            need(p.get('product_name'), 'products[%s].product_name' % pi)
            need(p.get('cost_price'), 'products[%s].cost_price' % pi)
            top_cost = num(p['cost_price'], 'products[%s].cost_price' % pi)
            top_sell = num(p['selling_price'], 'products[%s].selling_price' % pi) if p.get('selling_price') is not None else None
            variants = p.get('variants') if isinstance(p.get('variants'), list) and p.get('variants') else None
            if not variants:
                if not p.get('quantity'):
                    raise UserError(_("products[%s]: quantity is required (or provide a non-empty 'variants' array)", pi))
                variants = [{'quantity': p['quantity'], 'attributes': []}]
            norm = []
            for vi, v in enumerate(variants):
                v = v if isinstance(v, dict) else {}
                qty = num(v.get('quantity') or 0, 'products[%s].variants[%s].quantity' % (pi, vi))
                if qty <= 0:
                    raise UserError(_('products[%s].variants[%s].quantity must be a positive number', pi, vi))
                attrs = []
                for ai, a in enumerate(v.get('attributes') if isinstance(v.get('attributes'), list) else []):
                    a = a if isinstance(a, dict) else {}
                    need(a.get('name'), 'products[%s].variants[%s].attributes[%s].name' % (pi, vi, ai))
                    need(a.get('value'), 'products[%s].variants[%s].attributes[%s].value' % (pi, vi, ai))
                    attrs.append({'name': str(a['name']).strip(), 'value': str(a['value']).strip()})
                norm.append({
                    'quantity': qty,
                    'cost': num(v['cost_price'], 'cost_price') if v.get('cost_price') is not None else top_cost,
                    'selling': num(v['selling_price'], 'selling_price') if v.get('selling_price') is not None else top_sell,
                    'attributes': attrs,
                })
            products.append({
                'name': str(p['product_name']).strip(),
                'category_name': p.get('category_name'),
                'pos_category_id': p.get('pos_category_id'),
                'gst': float(p.get('gst_percent') or 5),
                'hsn': p.get('hsn_code'),
                'cost': top_cost,
                'selling': top_sell,
                'price_list': p.get('price_list'),
                'variants': norm,
                'has_attrs': any(v['attributes'] for v in norm),
            })

        # ---------------- duplicate bill (create) / target PO (add)
        PO = env['purchase.order']
        target_po = PO
        if mode == 'create':
            dups = PO.search([('partner_ref', '=ilike', bill_number), ('state', '!=', 'cancel'),
                              ('company_id', '=', company.id)], order='id')
            if dups:
                return {
                    '_status': 409,
                    'success': False,
                    'duplicate_bill': True,
                    'error': 'Bill already exists: bill number "%s" is already used in %s' % (
                        bill_number, ', '.join(dups.mapped('name'))),
                    'bill_number': bill_number,
                    'existing_po': [{
                        'po_id': d.id, 'po_name': d.name, 'state': d.state,
                        'partner_id': d.partner_id.id or None, 'partner_name': d.partner_id.display_name or None,
                        'amount_total': d.amount_total,
                    } for d in dups],
                    'stats': {'duration_ms': int((time.time() - t0) * 1000)},
                }
        else:
            target_po = PO.search([('id', '=', add_po_id), ('company_id', '=', company.id)], limit=1)
            if not target_po:
                raise MissingError(_('Purchase order %s not found', add_po_id))
            if target_po.state in ('cancel', 'done'):
                raise UserError(_("Purchase order %s is '%s'; products cannot be added", target_po.name, target_po.state))

        try:
            # ---------------- taxes per GST %
            Tax = env['account.tax']
            tax_by_gst = {}
            for g in {p['gst'] for p in products}:
                cands = Tax.search([('amount', '=', g), ('type_tax_use', '=', 'purchase'),
                                    *Tax._check_company_domain(company)], order='sequence, id')
                best = cands.filtered(lambda t: t.company_id == company)[:1] or cands[:1]
                tax_by_gst[g] = best.id or False

            # ---------------- partner
            is_new_party = False
            if mode == 'add':
                partner = target_po.partner_id
            else:
                partner = self._grg_find_partner(kind, party_key)
                if not partner:
                    vals = {'name': party_name, 'company_type': 'company', 'supplier_rank': 1,
                            'country_id': company.country_id.id or False}
                    vals['vat' if kind == 'gst' else 'phone'] = party_key
                    partner = env['res.partner'].create(vals)
                    is_new_party = True

            # ---------------- attributes + values (find or create)
            Attr = env['product.attribute']
            AttrVal = env['product.attribute.value']
            attr_names = list(dict.fromkeys(a['name'] for p in products for v in p['variants'] for a in v['attributes']))
            attr_by_name = {}
            for a in Attr.search([('name', 'in', attr_names)], order='id') if attr_names else Attr:
                attr_by_name.setdefault(a.name, a)
            for n in attr_names:
                if n not in attr_by_name:
                    attr_by_name[n] = Attr.create({'name': n, 'create_variant': 'always'})
            val_by_key = {}
            wanted = {}
            for p in products:
                for v in p['variants']:
                    for a in v['attributes']:
                        wanted[(attr_by_name[a['name']].id, a['value'])] = True
            if wanted:
                found = AttrVal.search([('attribute_id', 'in', list({k[0] for k in wanted})),
                                        ('name', 'in', list({k[1] for k in wanted}))], order='id')
                for f in found:
                    val_by_key.setdefault((f.attribute_id.id, f.name), f)
                for (aid, name) in wanted:
                    if (aid, name) not in val_by_key:
                        val_by_key[(aid, name)] = AttrVal.create({'attribute_id': aid, 'name': name})
            for p in products:
                for v in p['variants']:
                    v['attr_ids'] = [attr_by_name[a['name']].id for a in v['attributes']]
                    v['value_ids'] = [val_by_key[(attr_by_name[a['name']].id, a['value'])].id for a in v['attributes']]

            # ---------------- templates: create new / extend existing
            Tmpl = env['product.template']
            names = list(dict.fromkeys(p['name'] for p in products))
            tmpl_by_name = {}
            for t in Tmpl.search([('name', 'in', names)], order='id'):
                tmpl_by_name.setdefault(t.name, t)
            groups = {}
            for p in products:
                groups.setdefault(p['name'], []).append(p)

            def needed_lines(plist):
                m = {}
                for p in plist:
                    for v in p['variants']:
                        for aid, vid in zip(v['attr_ids'], v['value_ids']):
                            m.setdefault(aid, [])
                            if vid not in m[aid]:
                                m[aid].append(vid)
                return m

            categories = env['pos.category'].search([])

            def resolve_category(p):
                if p['category_name']:
                    m = categories.filtered(lambda c: lc(c.name) == lc(p['category_name']))[:1]
                    if m:
                        return m.id
                if p['pos_category_id']:
                    m = categories.filtered(lambda c: str(c.id) == str(p['pos_category_id']).strip())[:1]
                    if m:
                        return m.id
                raise UserError(_(
                    "Category not found for \"%(name)s\". Pass 'category_name' or valid 'pos_category_id'. Available: [%(cats)s]",
                    name=p['name'], cats=', '.join('%s: %s' % (c.id, c.name) for c in categories)))

            tmpl_meta = {}
            for name, plist in groups.items():
                if name in tmpl_by_name:
                    t = tmpl_by_name[name]
                    tmpl_meta[name] = {'tmpl': t, 'is_new': False}
                    cmds = []
                    for aid, vids in needed_lines(plist).items():
                        line = t.attribute_line_ids.filtered(lambda l: l.attribute_id.id == aid)[:1]
                        if not line:
                            cmds.append((0, 0, {'attribute_id': aid, 'value_ids': [(6, 0, vids)]}))
                        else:
                            add = [x for x in vids if x not in line.value_ids.ids]
                            if add:
                                cmds.append((1, line.id, {'value_ids': [(4, x) for x in add]}))
                    if cmds:
                        t.write({'attribute_line_ids': cmds})
                    if not t.sale_ok:
                        t.sale_ok = True
                    continue
                p = plist[0]
                vals = {
                    'name': name,
                    'type': 'consu',
                    'is_storable': True,
                    'purchase_ok': True,
                    'sale_ok': True,
                    'available_in_pos': True,
                    'company_id': company.id,
                    'standard_price': p['cost'],
                    'pos_categ_ids': [(6, 0, [resolve_category(p)])],
                    'description': '<pre>%s</pre>' % html_escape(json.dumps(p['price_list'], indent=2, ensure_ascii=False)),
                    'tracking': 'none',
                    'attribute_line_ids': [(0, 0, {'attribute_id': aid, 'value_ids': [(6, 0, vids)]})
                                           for aid, vids in needed_lines(plist).items()],
                }
                if p['selling'] is not None:
                    vals['list_price'] = p['selling']
                if p['hsn'] not in (None, '') and 'l10n_in_hsn_code' in Tmpl._fields:
                    vals['l10n_in_hsn_code'] = str(p['hsn'])
                tmpl_meta[name] = {'tmpl': Tmpl.create(vals), 'is_new': True}

            # ---------------- match requested variants
            for p in products:
                t = tmpl_meta[p['name']]['tmpl']
                cands = t.with_context(active_test=True).product_variant_ids
                if not cands:
                    raise UserError(_('No product.product for template %s (%s)', t.id, p['name']))
                ptav_by_val = {x.product_attribute_value_id.id: x for x in t.attribute_line_ids.product_template_value_ids}
                for v in p['variants']:
                    v['ptav_by_val'] = ptav_by_val
                    if not v['value_ids']:
                        v['variant'] = cands[0]
                        continue
                    want = [ptav_by_val.get(x) for x in v['value_ids']]
                    match = None
                    if all(want):
                        want_ids = sorted(x.id for x in want)
                        match = next((c for c in cands if sorted(c.product_template_attribute_value_ids.ids) == want_ids), None)
                    if not match:
                        raise UserError(
                            'Variant not found for "%s" (template %s) with [%s]. Template may have extra attribute '
                            'lines not sent in the request.' % (
                                p['name'], t.id, ', '.join('%s=%s' % (a['name'], a['value']) for a in v['attributes'])))
                    v['variant'] = match

            # ---------------- variant barcode / cost + Size price_extra
            for p in products:
                touch = p['has_attrs'] or tmpl_meta[p['name']]['is_new']
                for v in p['variants']:
                    var = v['variant']
                    v['barcode'] = var.barcode or None
                    if touch:
                        bc = '3000000000000' + str(var.id)
                        vals = {'standard_price': v['cost']}
                        if var.barcode != bc:
                            vals['barcode'] = bc
                        var.write(vals)
                        v['barcode'] = bc
                    for a, val_id in zip(v['attributes'], v['value_ids']):
                        if lc(a['name']) != 'size':
                            continue
                        ptav = v['ptav_by_val'].get(val_id)
                        base, sell = p['selling'], v['selling']
                        extra = 0 if base is None or sell is None else sell - base
                        if ptav and ptav.price_extra != extra:
                            ptav.price_extra = extra

            # ---------------- PO lines (same variant aggregated)
            date_planned = fields.Datetime.to_string(
                datetime.combine(fields.Date.context_today(self) + timedelta(days=1), datetime.min.time()))
            line_by_variant = {}
            for p in products:
                for v in p['variants']:
                    vid = v['variant'].id
                    tax_id = tax_by_gst.get(p['gst'])
                    prev = line_by_variant.get(vid)
                    line_by_variant[vid] = {
                        'product_id': vid,
                        'product_qty': (prev['product_qty'] if prev else 0) + v['quantity'],
                        'price_unit': v['cost'],
                        'date_planned': date_planned,
                        'tax_ids': [(6, 0, [tax_id])] if tax_id else [(6, 0, [])],
                    }
            lines_added = lines_updated = 0
            if mode == 'create':
                po = PO.create({
                    'partner_id': partner.id,
                    'partner_ref': str(body.get('bill_number')),
                    'note': notes,
                    'company_id': company.id,
                    'order_line': [(0, 0, l) for l in line_by_variant.values()],
                })
                lines_added = len(line_by_variant)
            else:
                po = target_po
                existing = {}
                for l in po.order_line.filtered(lambda l: not l.display_type).sorted('id'):
                    if l.product_id and l.product_id.id not in existing:
                        existing[l.product_id.id] = l
                cmds = []
                for l in line_by_variant.values():
                    ex = existing.get(l['product_id'])
                    if ex:
                        cmds.append((1, ex.id, {'product_qty': ex.product_qty + l['product_qty']}))
                        lines_updated += 1
                    else:
                        cmds.append((0, 0, l))
                        lines_added += 1
                po.write({'order_line': cmds})
        except AccessError as e:
            raise AccessError(_(
                "%(err)s\n\nThis API creates products, variants, attributes and vendors: give the API-key user "
                "the 'Products / Create' and 'Contact Creation' rights (or Inventory / Administrator).", err=e.args[0]))

        line_id_by_variant = {}
        for l in po.order_line.sorted('id'):
            if l.product_id and l.product_id.id not in line_id_by_variant:
                line_id_by_variant[l.product_id.id] = l.id

        return {
            'success': True,
            'mode': mode,
            'po_id': po.id,
            'po_name': po.name,
            'amount_total': po.amount_total,
            'partner_id': partner.id,
            'partner_name': partner.display_name if mode == 'add' else (party_name if is_new_party else partner.name),
            'is_new_party': is_new_party,
            'bill_number': (target_po.partner_ref or None) if mode == 'add' else body.get('bill_number'),
            'lines_added': lines_added,
            'lines_updated': lines_updated,
            'products': [{
                'product_name': p['name'],
                'product_tmpl_id': tmpl_meta[p['name']]['tmpl'].id,
                'is_new_template': tmpl_meta[p['name']]['is_new'],
                'variants': [{
                    'variant_id': v['variant'].id,
                    'barcode': v['barcode'],
                    'attributes': v['attributes'],
                    'quantity': _n(v['quantity']),
                    'cost_price': _n(v['cost']),
                    'selling_price': _n(v['selling']),
                    'po_line_id': line_id_by_variant.get(v['variant'].id),
                } for v in p['variants']],
            } for p in products],
            'stats': {'duration_ms': int((time.time() - t0) * 1000)},
            'message': ('PO %s updated: %s line(s) added, %s line(s) quantity increased.' % (po.name, lines_added, lines_updated))
            if mode == 'add' else ('PO %s created with %s line(s).' % (po.name, len(line_by_variant))),
        }

    # ------------------------------------------------------ product autosuggest
    @api.model
    def grg_product_suggest(self, q='', limit=10, strict=False, with_variants=False):
        """Fast product autosuggest for the PO app, scoped to the selected company.

        Match order: exact barcode / internal reference (variant) -> name starts with -> name contains
        (every word must match). strict=True: only products owned by the company (excludes shared ones).
        Field names match the parser-variants-v2 request so the app can pre-fill from a suggestion.
        """
        t0 = time.time()
        company = self.env.company
        limit = min(max(_int(limit, 10), 1), 50)
        q = re.sub(r'\s+', ' ', str(q or '')).strip()
        Tmpl = self.env['product.template'].with_context(active_test=True)
        scope = [('company_id', '=', company.id)] if strict else []

        found, matched_by = [], {}

        def take(records, how):
            for r in records:
                if r.id not in matched_by and len(found) < limit:
                    found.append(r)
                    matched_by[r.id] = how

        if not q:
            take(Tmpl.search(scope, limit=limit, order='id desc'), 'recent')
        else:
            # 1) exact barcode / internal reference on variants (indexed)
            Prod = self.env['product.product']
            prods = Prod.search(scope + ['|', ('barcode', '=', q), ('default_code', '=ilike', q)], limit=limit)
            take(prods.product_tmpl_id, 'barcode_or_code')
            # 2) name starts with q
            if len(found) < limit:
                take(Tmpl.search(scope + [('name', '=ilike', q.replace('%', r'\%').replace('_', r'\_') + '%'),
                                          ('id', 'not in', list(matched_by))],
                                 limit=limit - len(found), order='name, id'), 'name_prefix')
            # 3) every word contained in name (trigram index), or reference starts with q
            if len(found) < limit:
                words = [w for w in q.split(' ') if w]
                dom = scope + [('id', 'not in', list(matched_by))]
                name_dom = [('name', 'ilike', w) for w in words]
                dom += ['|', ('default_code', '=ilike', q + '%')] + (['&'] * (len(name_dom) - 1)) + name_dom
                take(Tmpl.search(dom, limit=limit - len(found), order='name, id'), 'contains')

        tmpls = Tmpl.browse([t.id for t in found])
        # prefetch everything we read in a few batched queries
        tmpls.fetch(['name', 'default_code', 'list_price', 'standard_price', 'categ_id', 'pos_categ_ids',
                     'uom_id', 'company_id', 'supplier_taxes_id', 'description', 'attribute_line_ids',
                     'product_variant_ids']
                    + (['l10n_in_hsn_code'] if 'l10n_in_hsn_code' in Tmpl._fields else []))
        has_hsn = 'l10n_in_hsn_code' in Tmpl._fields

        def price_list(desc):
            if not desc:
                return None
            m = re.search(r'<pre>(.*?)</pre>', str(desc), re.S)
            if not m:
                return None
            try:
                return json.loads(html.unescape(m.group(1)))
            except ValueError:
                return None

        out = []
        for t in tmpls:
            variants = t.product_variant_ids
            taxes = t.supplier_taxes_id._filter_taxes_by_company(company)
            pos_cat = t.pos_categ_ids[:1]
            item = {
                'product_tmpl_id': t.id,
                'product_name': t.name,
                'internal_reference': t.default_code or None,
                'barcode': variants[:1].barcode or None if len(variants) == 1 else None,
                # template cost is 0 when it has several variants -> use the first variant's cost
                'cost_price': _n(t.standard_price if len(variants) <= 1 else variants[:1].standard_price),
                'selling_price': _n(t.list_price),
                'gst_percent': _n(taxes[:1].amount) if taxes else None,
                'hsn_code': (t.l10n_in_hsn_code or None) if has_hsn else None,
                'category_name': pos_cat.name or None,
                'pos_category_id': pos_cat.id or None,
                'product_category': t.categ_id.name or None,
                'uom': t.uom_id.name or None,
                'company_id': t.company_id.id or None,
                'company_name': t.company_id.name or None,
                'variant_count': len(variants),
                'attributes': [{'name': l.attribute_id.name, 'values': l.value_ids.mapped('name')}
                               for l in t.attribute_line_ids],
                'price_list': price_list(t.description),
                'matched_by': matched_by[t.id],
            }
            if with_variants:
                item['variants'] = [{
                    'variant_id': v.id,
                    'barcode': v.barcode or None,
                    'internal_reference': v.default_code or None,
                    'attributes': [{'name': ptav.attribute_id.name, 'value': ptav.name}
                                   for ptav in v.product_template_attribute_value_ids],
                    'cost_price': _n(v.standard_price),
                    'selling_price': _n(v.lst_price),
                } for v in variants]
            out.append(item)
        return {
            'success': True,
            'query': q,
            'company_id': company.id,
            'strict': bool(strict),
            'count': len(out),
            'products': out,
            'stats': {'duration_ms': int((time.time() - t0) * 1000)},
        }
