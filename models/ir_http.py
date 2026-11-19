# -*- coding: utf-8 -*-
import json
import logging
import time

from odoo import SUPERUSER_ID, api, fields, models
from odoo.http import request

_logger = logging.getLogger(__name__)

SKIP_HEADERS = {'cookie', 'x-openerp-session-id'}


def _mask(value):
    value = str(value or '')
    parts = value.split(' ', 1)
    if len(parts) == 2 and parts[0].lower() in ('bearer', 'basic', 'token'):
        tok = parts[1].strip()
        return '%s %s' % (parts[0], (tok[:4] + '…' + tok[-4:]) if len(tok) > 10 else '***')
    return '***'


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

    # ------------------------------------------------------------------ API request log
    @classmethod
    def _grg_log_wanted(cls):
        try:
            if request.httprequest.method == 'OPTIONS':
                return None
            enabled, prefixes, max_body = request.env['grg.api.log'].sudo()._grg_log_settings()
            path = request.httprequest.path
            if enabled and path.startswith(prefixes):
                return max_body
        except Exception:  # noqa: BLE001 - logging must never break a request
            pass
        return None

    @classmethod
    def _authenticate(cls, endpoint):
        # first hook of a dispatched request: start the timer (also covers auth failures)
        try:
            if cls._grg_log_wanted() is not None:
                request._grg_log_t0 = time.time()
        except Exception:  # noqa: BLE001
            pass
        return super()._authenticate(endpoint)

    @classmethod
    def _post_dispatch(cls, response):
        super()._post_dispatch(response)
        cls._grg_write_api_log(response)

    @classmethod
    def _handle_error(cls, exception):
        response = super()._handle_error(exception)
        cls._grg_write_api_log(response, exception)
        return response

    @classmethod
    def _grg_write_api_log(cls, response, exception=None):
        t0 = getattr(request, '_grg_log_t0', None)
        if t0 is None:
            return
        request._grg_log_t0 = None  # once per request (retries / error after post_dispatch)
        try:
            max_body = cls._grg_log_wanted() or 100000
            hreq = request.httprequest

            def clip(text):
                if text is None:
                    return None
                return text if len(text) <= max_body else text[:max_body] + '\n… [truncated, %d chars]' % len(text)

            headers = {k: (_mask(v) if k.lower() in ('authorization', 'proxy-authorization') else v)
                       for k, v in hreq.headers.items() if k.lower() not in SKIP_HEADERS}

            # request body: JSON/text as-is, multipart -> fields + file names/sizes
            req_body, req_size = None, hreq.content_length or 0
            ctype = (hreq.mimetype or '').lower()
            if ctype.startswith('multipart/') or ctype == 'application/x-www-form-urlencoded':
                form = {k: hreq.form.getlist(k) if len(hreq.form.getlist(k)) > 1 else hreq.form.get(k) for k in hreq.form}
                files = []
                for k in hreq.files:
                    for f in hreq.files.getlist(k):
                        try:
                            pos = f.stream.tell()
                            f.stream.seek(0, 2)
                            size = f.stream.tell()
                            f.stream.seek(pos)
                        except Exception:  # noqa: BLE001
                            size = None
                        files.append({'field': k, 'filename': f.filename, 'content_type': f.mimetype, 'size': size})
                req_body = json.dumps({'form': form, 'files': files}, ensure_ascii=False, indent=2)
            else:
                try:
                    raw = hreq.get_data(cache=True)
                    req_body = raw.decode('utf-8', errors='replace') if raw else None
                except Exception:  # noqa: BLE001
                    req_body = None

            status, resp_body, resp_ctype, resp_size = 500, None, None, None
            if response is not None and not hasattr(response, 'get_data') and hasattr(response, 'get_response'):
                response = response.get_response()   # werkzeug HTTPException returned by _handle_error
            if response is not None and hasattr(response, 'get_data'):
                status = getattr(response, 'status_code', 500)
                resp_ctype = response.headers.get('Content-Type') if hasattr(response, 'headers') else None
                if getattr(response, 'direct_passthrough', False):
                    resp_body = '<streamed response>'
                else:
                    data = response.get_data()
                    resp_size = len(data)
                    if resp_ctype and any(t in resp_ctype for t in ('json', 'text', 'xml', 'javascript')):
                        resp_body = data.decode('utf-8', errors='replace')
                    else:
                        resp_body = '<binary %d bytes %s>' % (len(data), resp_ctype or '')

            env = request.env
            uid = env.uid if env is not None else None
            user = env.user if uid else None
            vals = {
                'request_time': fields.Datetime.now(),
                'method': hreq.method,
                'path': hreq.path,
                'query_string': hreq.query_string.decode('utf-8', errors='replace') or False,
                'status_code': status,
                'duration_ms': round((time.time() - t0) * 1000, 1),
                'user_id': uid if user and not user._is_public() else False,
                'company_id': env.company.id if user and not user._is_public() else False,
                'ip': hreq.remote_addr,
                'user_agent': (hreq.user_agent.string or '')[:500],
                'request_headers': json.dumps(headers, ensure_ascii=False, indent=2),
                'request_body': clip(req_body),
                'request_size': req_size,
                'response_content_type': resp_ctype,
                'response_body': clip(resp_body),
                'response_size': resp_size,
                'error_message': ('%s: %s' % (type(exception).__name__, exception))[:2000] if exception else False,
            }
            with env.registry.cursor() as cr:   # own transaction: kept even if the API call rolled back
                api.Environment(cr, SUPERUSER_ID, {})['grg.api.log'].create(vals)
        except Exception:  # noqa: BLE001 - never break the API because of logging
            _logger.warning('grg_config: could not write API log', exc_info=True)
