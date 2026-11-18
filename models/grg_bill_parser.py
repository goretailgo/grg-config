# -*- coding: utf-8 -*-
"""Bill parser (port of the n8n 'GoRetailGo - Bill Parser (Gemini) v8' webhook parse-bill-v8).

  Validate Input            -> _grg_bill_pages()            (Python, shop_id now OPTIONAL)
  Build Gemini Request      -> bill_parser/prompt.txt       (same prompt, per page)
  Gemini Extract            -> _grg_gemini_extract()        (Gemini REST API, pages in parallel)
  Parse and Validate Output -> bill_parser/parse_and_validate_output.js (the n8n code, unchanged, run by Node)
"""
import base64
import hashlib
import json
import logging
import os
import random
import shutil
import string
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

PARSER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bill_parser')
ALLOWED_MIME = ('application/pdf', 'image/jpeg', 'image/jpg', 'image/png', 'image/webp')
EXT_MIME = {'.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}
DEFAULT_MODEL = 'gemini-flash-lite-latest'
DEFAULT_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class GrgBillParser(models.AbstractModel):
    _name = 'grg.bill.parser'
    _description = 'GoRetailGo Bill Parser (Gemini)'

    # ------------------------------------------------------------ settings
    @api.model
    def _grg_param(self, key, default=None):
        return self.env['ir.config_parameter'].sudo().get_param('grg_config.' + key) or default

    # ---------------------------------------------------- 1. validate input
    @api.model
    def _grg_sniff_mime(self, data, declared, filename):
        head = data[:12]
        hexs = head.hex().upper()
        if hexs.startswith('89504E47'):
            return 'image/png'
        if hexs.startswith('FFD8FF'):
            return 'image/jpeg'
        if hexs.startswith('25504446'):
            return 'application/pdf'
        if hexs.startswith('52494646') and head[8:12] == b'WEBP':
            return 'image/webp'
        if declared in ALLOWED_MIME:
            return declared
        fname = (filename or '').lower()
        for ext, mime in EXT_MIME.items():
            if fname.endswith(ext):
                return mime
        return None

    @api.model
    def _grg_bill_pages(self, files, shop_id=None):
        """files: [(field_name, file_name, declared_mime, bytes)] in upload order.
        Returns (prep_list, pages) or (error_dict, None)."""
        if not files:
            return {'valid': False, 'error': 'NO_FILE',
                    'message': 'No file was received. Send one or more bill page images/PDFs as '
                               'multipart/form-data file fields (e.g. bill_file, bill_file_2, ...).'}, None
        pages = []
        for field, fname, declared, data in files:
            mime = self._grg_sniff_mime(data, declared, fname)
            if not mime:
                return {'valid': False, 'error': 'UNSUPPORTED_FILE_TYPE',
                        'message': 'Unsupported file type in field "%s": %s%s. Allowed: PDF, JPEG, PNG, WEBP. '
                                   'First bytes: %s' % (field, declared, ' (%s)' % fname if fname else '',
                                                        data[:12].hex().upper() or '(empty)')}, None
            pages.append({'file_name': fname, 'mime_type': mime, 'data': data})
        # drop byte-identical files (same photo attached twice)
        seen, unique = set(), []
        for p in pages:
            fp = hashlib.sha1(p['data']).hexdigest()
            if fp in seen:
                continue
            seen.add(fp)
            unique.append(p)
        dropped = len(pages) - len(unique)
        if not unique:
            return {'valid': False, 'error': 'NO_FILE', 'message': 'No usable file pages were found in the request.'}, None

        request_id = 'req_%d_%s' % (int(time.time() * 1000),
                                    ''.join(random.choices(string.ascii_lowercase + string.digits, k=8)))
        received_at = _iso_now()
        prep = [{
            'valid': True,
            'shop_id': shop_id or None,
            'request_id': request_id,
            'received_at': received_at,
            'page_index': i + 1,
            'total_pages': len(unique),
            'duplicate_files_dropped': dropped,
            'file_key': 'data',
            'mime_type': p['mime_type'],
        } for i, p in enumerate(unique)]
        return prep, unique

    # ------------------------------------------------- 2+3. Gemini per page
    @api.model
    def _grg_gemini_extract(self, prep, pages):
        """Returns a list of n8n-style Gemini outputs ({'content': {...}} or {'error': '...'}), one per page."""
        key = self._grg_param('gemini_api_key')
        if not key:
            return [{'error': 'Gemini API key is not configured (Settings > GoRetailGo > Gemini API Key).'}] * len(pages)
        model = self._grg_param('gemini_model', DEFAULT_MODEL).replace('models/', '')
        base = self._grg_param('gemini_base_url', DEFAULT_BASE_URL).rstrip('/')
        timeout = int(self._grg_param('gemini_timeout', '120'))
        with open(os.path.join(PARSER_DIR, 'prompt.txt'), encoding='utf-8') as f:
            template = f.read()
        url = '%s/models/%s:generateContent' % (base, model)

        def one(i):
            meta, page = prep[i], pages[i]
            prompt = template.replace('{{PAGE_INDEX}}', str(meta['page_index'])).replace(
                '{{TOTAL_PAGES}}', str(meta['total_pages']))
            body = {'contents': [{'role': 'user', 'parts': [
                {'text': prompt},
                {'inline_data': {'mime_type': page['mime_type'], 'data': base64.b64encode(page['data']).decode()}},
            ]}]}
            try:
                r = requests.post(url, params={'key': key}, json=body, timeout=timeout)
                data = r.json() if r.content else {}
                if r.status_code >= 400 or 'error' in data:
                    err = (data.get('error') or {}).get('message') if isinstance(data.get('error'), dict) else data.get('error')
                    return {'error': err or 'Gemini HTTP %s' % r.status_code}
                cand = (data.get('candidates') or [{}])[0]
                content = cand.get('content') or {}
                text = ''.join(p.get('text', '') for p in content.get('parts') or [])
                if not text:
                    return {'error': 'Gemini returned no text (finishReason: %s)' % cand.get('finishReason')}
                return {'content': content, 'finishReason': cand.get('finishReason')}
            except requests.RequestException as e:
                return {'error': 'Gemini request failed: %s' % e}
            except ValueError:
                return {'error': 'Gemini returned a non-JSON response'}

        workers = max(1, min(len(pages), int(self._grg_param('gemini_parallel', '4'))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, range(len(pages))))

    # --------------------------------------------- 4. parse + validate (JS)
    @api.model
    def _grg_run_parser_js(self, prep, responses):
        node = self._grg_param('node_bin') or shutil.which('node') or '/usr/bin/node'
        if not os.path.exists(node) and not shutil.which(node):
            return {'success': False, 'error': 'PARSER_RUNTIME_MISSING',
                    'message': 'Node.js is not installed on the Odoo server (needed by the bill parser).'}
        payload = json.dumps({'pages': [{'json': r, 'pairedItem': i} for i, r in enumerate(responses)], 'prep': prep})
        try:
            proc = subprocess.run([node, os.path.join(PARSER_DIR, 'run_parse.js')], input=payload.encode(),
                                  capture_output=True, timeout=int(self._grg_param('parser_timeout', '60')))
        except subprocess.TimeoutExpired:
            return {'success': False, 'error': 'PARSER_TIMEOUT', 'message': 'Bill parser took too long.'}
        if proc.returncode != 0:
            _logger.error('grg bill parser failed: %s', proc.stderr.decode(errors='replace')[-2000:])
            return {'success': False, 'error': 'PARSER_ERROR',
                    'message': (proc.stderr.decode(errors='replace').strip().splitlines() or ['unknown'])[-1][:500]}
        return json.loads(proc.stdout.decode())

    # ------------------------------------------------------------ pipeline
    @api.model
    def grg_parse_bill(self, files, shop_id=None):
        prep, pages = self._grg_bill_pages(files, shop_id=shop_id)
        if pages is None:
            return dict(prep, _status=400)
        responses = self._grg_gemini_extract(prep, pages)
        failed = next((r for r in responses if r.get('error')), None)
        if failed:
            return {'_status': 502, 'success': False, 'error': 'GEMINI_API_ERROR', 'message': failed['error'],
                    'request_id': prep[0]['request_id'], 'shop_id': prep[0]['shop_id']}
        result = self._grg_run_parser_js(prep, responses)
        if not result.get('success'):
            result['_status'] = 500 if result.get('error') in ('PARSER_RUNTIME_MISSING', 'PARSER_TIMEOUT', 'PARSER_ERROR') else 409
        return result
