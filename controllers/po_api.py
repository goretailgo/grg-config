# -*- coding: utf-8 -*-
"""PO app REST API (replaces the n8n webhooks).

Auth:   Authorization: Bearer <odoo_api_key>
        X-Odoo-Database: <db>        (only needed when the server hosts several DBs)
        X-Company-Id: <company_id>   (optional; defaults to the user's current company)
"""
import json
import logging
import re

from odoo import http
from odoo.exceptions import AccessDenied, AccessError, MissingError, UserError, ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)

BASE = '/grg/api'
ROUTE = dict(type='http', auth='public', csrf=False, cors='*', save_session=False)


class AuthFailed(Exception):
    pass


class GrgPoApiController(http.Controller):

    # ------------------------------------------------------------ plumbing
    def _json(self, payload, status=200):
        return request.make_json_response(payload, status=status)

    def _auth_failed(self, reason=None):
        # same body as the n8n webhooks; detail goes in 'error'
        payload = {'message': 'Authentication Failed', 'status_code': 401, 'success': False}
        if reason:
            payload['error'] = reason
        return self._json(payload, status=401)

    def _bearer_user(self):
        header = request.httprequest.headers.get('Authorization') or ''
        m = re.match(r'^bearer\s+(.+)$', header.strip(), re.IGNORECASE)
        if not m:
            raise AuthFailed('Authorization header missing. Use "Authorization: Bearer <api_key>".')
        uid = request.env['res.users.apikeys']._check_credentials(scope='rpc', key=m.group(1).strip())
        if not uid:
            raise AuthFailed('Invalid or expired API key.')
        user = request.env['res.users'].sudo().browse(uid)
        if not user.active or not user._is_internal():
            raise AuthFailed('API key user is not an active internal user.')
        return uid

    def _company_id(self, data=None):
        data = data if isinstance(data, dict) else {}
        return (data.get('company_id') or request.params.get('company_id')
                or request.httprequest.headers.get('X-Company-Id') or None)

    def _json_body(self):
        raw = request.httprequest.get_data(as_text=True)
        if not raw or not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise UserError('Request body is not valid JSON.')

    def _api_env(self, data=None):
        """Switch request.env to the API-key user + requested company."""
        uid = self._bearer_user()
        request.update_env(user=uid)
        user = request.env.user
        cid = self._company_id(data)
        if cid:
            try:
                cid = int(cid)
            except (TypeError, ValueError):
                raise UserError('company_id must be a number.')
            if cid not in user.company_ids.ids:
                raise AccessError('You do not have access to company %s.' % cid)
        else:
            cid = user.company_id.id
        request.update_context(allowed_company_ids=[cid])
        return request.env['grg.po.api']

    def _run(self, fn, with_body=False, company_from_body=True):
        try:
            body = self._json_body() if with_body else None
            api = self._api_env(body if company_from_body else None)
            result = fn(api, body)
            status = result.pop('_status', 200) if isinstance(result, dict) else 200
            return self._json(result, status=status)
        except AuthFailed as e:
            return self._auth_failed(str(e))
        except AccessDenied:
            return self._auth_failed()
        except AccessError as e:  # subclass of UserError: must come first
            request.env.cr.rollback()
            return self._json({'success': False, 'error': str(e.args[0])}, status=403)
        except MissingError as e:
            request.env.cr.rollback()
            return self._json({'success': False, 'error': str(e.args[0])}, status=404)
        except (UserError, ValidationError) as e:
            request.env.cr.rollback()
            return self._json({'success': False, 'error': str(e.args[0])}, status=400)

    # -------------------------------------------------------------- routes
    # Authentication: validate the API key, return user / company / db
    @http.route(BASE + '/authenticate', methods=['GET', 'POST'], **ROUTE)
    def authenticate(self, **kw):
        return self._run(lambda api, body: api.grg_whoami())

    # POS categories
    @http.route(BASE + '/pos-categories', methods=['GET'], **ROUTE)
    def pos_categories(self, **kw):
        return self._run(lambda api, body: api.grg_pos_categories())

    # Paginated PO list: ?page=1&size=20[&search=...&state=purchase]
    @http.route(BASE + '/po', methods=['GET'], **ROUTE)
    def po_list(self, page=1, size=20, search=None, state=None, **kw):
        return self._run(lambda api, body: api.grg_po_list(page=page, size=size, search=search, state=state))

    # PO detail: ?id=<po_id>  (or /po/<id>)
    @http.route([BASE + '/po-detail', BASE + '/po/<int:po_id>'], methods=['GET'], **ROUTE)
    def po_detail(self, po_id=None, id=None, **kw):
        return self._run(lambda api, body: api.grg_po_detail(po_id or id))

    # Create PO: {gst_number|mobile_or_gst, party_name, bill_number, bill_date, transport{...}}
    @http.route(BASE + '/create-po', methods=['POST'], **ROUTE)
    def create_po(self, **kw):
        return self._run(lambda api, body: api.grg_create_po(body), with_body=True)

    # Check PO availability by bill number: {bill_number, gst_number?}
    @http.route(BASE + '/check-po-by-bill', methods=['GET', 'POST'], **ROUTE)
    def check_po_by_bill(self, **kw):
        def go(api, body):
            body = body or {}
            return api.grg_check_po_by_bill(
                bill_number=body.get('bill_number') or kw.get('bill_number'),
                gst_number=body.get('gst_number') or kw.get('gst_number'))
        return self._run(go, with_body=True)

    # Configuration object: GET = read, POST = merge-update (?replace=1 to overwrite)
    @http.route(BASE + '/configure-user', methods=['GET', 'POST'], **ROUTE)
    def configure_user(self, replace=None, **kw):
        if request.httprequest.method == 'GET':
            return self._run(lambda api, body: api.grg_get_config())
        replace = str(replace or '').lower() in ('1', 'true', 'yes')
        return self._run(lambda api, body: api.grg_update_config(body, replace=replace),
                         with_body=True, company_from_body=False)

    # Form template
    @http.route(BASE + '/get-template', methods=['GET'], **ROUTE)
    def get_template(self, **kw):
        return self._run(lambda api, body: api.grg_get_template())

    # Bulk save: PO + products + variants (n8n parser-variants-v2). Top-level po_id = add to that PO.
    @http.route(BASE + '/parser-variants-v2', methods=['POST'], **ROUTE)
    def parser_variants_v2(self, **kw):
        return self._run(lambda api, body: api.grg_save_po_variants(body), with_body=True)

    # Product autosuggest: ?q=kurti&limit=10[&strict=1&with_variants=1]  (company via X-Company-Id / company_id)
    @http.route(BASE + '/product-suggest', methods=['GET'], **ROUTE)
    def product_suggest(self, q='', limit=10, strict=None, with_variants=None, **kw):
        truthy = lambda v: str(v or '').lower() in ('1', 'true', 'yes')
        return self._run(lambda api, body: api.grg_product_suggest(
            q=q, limit=limit, strict=truthy(strict), with_variants=truthy(with_variants)))

    # Confirm PO: draft/sent -> purchase. Body {"po_id": 16} (or POST /grg/api/po/16/confirm)
    @http.route([BASE + '/confirm-po', BASE + '/po/<int:po_id>/confirm'], methods=['POST'], **ROUTE)
    def confirm_po(self, po_id=None, **kw):
        def go(api, body):
            body = body if isinstance(body, dict) else {}
            return api.grg_confirm_po(po_id or body.get('po_id') or kw.get('po_id'))
        return self._run(go, with_body=True)
