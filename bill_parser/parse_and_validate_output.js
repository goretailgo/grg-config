// Bill Parser v8 - "Parse and Validate Output"
// Runs ONCE for ALL pages of the request (mode: Run Once for All Items).
//
// Pipeline:  parse model JSON (tolerant)  ->  normalise rows (list rows + size
// grids)  ->  drop duplicate pages/rows  ->  memo/challan + invoice pairing  ->
// header checks  ->  reconcile with printed totals  ->  rows to products  ->
// sizes/colours to variants.

// ------------------------------------------------------------------ config --
const CONFIG = {
  // false = keep the bill's own letter sizes (XXXL stays XXXL, 2XL stays 2XL).
  // true  = force one ladder (S, M, L, XL, XXL, 3XL, 4XL, 5XL).
  NORMALISE_ALPHA_SIZES: false,
  MAX_SIZES_PER_RANGE: 12,
  AMOUNT_TOLERANCE: 2, // rupees, for rounding differences

  // Decision 1 - a memo/challan AND its tax invoice sent in one request:
  // 'memo' = products from the memo/challan, header from the invoice. 'invoice' = products from the invoice.
  COMPANION_PRODUCTS_FROM: 'memo',
  // Decision 2 - rows with only a name (no design, colour or size), e.g. ten "KURTI" rows:
  // 'separate' = one product per row ("KURTI 1" .. "KURTI 10"). 'merge' = rows with the same name are grouped.
  NAME_ONLY_ROWS: 'separate',
  // Decision 3 - same design at two rates with no size printed:
  // 'one_product' = one product, one variant per rate, Size left blank to fill. 'two_products' = "X" and "X (2)".
  NO_SIZE_TWO_RATES: 'one_product',
  // Decision 4 - "24X38" / "16*22" are size ranges (24, 26 ... 38). false = kept as plain text.
  X_AND_STAR_ARE_RANGES: true,
  // Decision 5 - true = variant selling_price is filled with the printed MRP. false = MRP only in meta.mrp_by_size.
  MRP_AS_SELLING_PRICE: false
};

const pageInputs = $input.all();
const prepAll = $('Validate Input').all();
const prep = prepAll[0].json; // shop_id / request_id / received_at are the same on every page

const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
const MOBILE_RE = /^[6-9]\d{9}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// ----------------------------------------------------------------- helpers --
const toNum = (v) => {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  const cleaned = String(v).replace(/,/g, '').replace(/[^0-9.\-]/g, '');
  if (cleaned === '' || cleaned === '-' || cleaned === '.') return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
};
const toStr = (v) => {
  if (v === null || v === undefined) return null;
  const s = String(v).replace(/\s+/g, ' ').trim();
  if (s === '' || /^(-+|N\/?A|NIL|NULL|NONE)$/i.test(s)) return null;
  return s;
};
const round2 = (n) => Math.round(n * 100) / 100;
const near = (a, b, tol) => Math.abs(a - b) <= (tol === undefined ? CONFIG.AMOUNT_TOLERANCE : tol);
const alnum = (s) => (s || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
const plain = (s) => (s ? s.normalize('NFD').replace(/[̀-ͯ]/g, '') : s);
const sum = (arr, f) => arr.reduce((s, x) => s + (f(x) || 0), 0);
const inr = (n) => Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });

// GSTIN check digit (mod-36) - catches single misread characters.
const gstinChecksumOk = (g) => {
  const chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  let s = 0;
  for (let i = 0; i < 14; i++) {
    const v = chars.indexOf(g[i]);
    if (v < 0) return false;
    const p = v * (i % 2 === 0 ? 1 : 2);
    s += Math.floor(p / 36) + (p % 36);
  }
  return chars[(36 - (s % 36)) % 36] === g[14];
};

// Native Gemini/OpenAI nodes return plain text in a few possible shapes.
function extractRawText(response) {
  if (typeof response.text === 'string') return response.text;
  if (typeof response.content === 'string') return response.content;
  if (response.content && Array.isArray(response.content.parts)) {
    return response.content.parts.map((p) => p.text || '').join('');
  }
  if (typeof response.response === 'string') return response.response;
  if (response.candidates && response.candidates[0] && response.candidates[0].content && Array.isArray(response.candidates[0].content.parts)) {
    return response.candidates[0].content.parts.map((p) => p.text || '').join('');
  }
  if (Array.isArray(response.parts)) return response.parts.map((p) => p.text || '').join('');
  if (response.products && Array.isArray(response.products)) return response; // already an object
  return null;
}

// Tolerant JSON reader: strips fences/prose, fixes trailing commas, and if the
// model output was cut off mid-way it salvages every COMPLETE product row.
function parseModelJson(raw) {
  if (raw && typeof raw === 'object') return { data: raw, truncated: false };
  if (typeof raw !== 'string') return { data: null, truncated: false };
  let t = raw.replace(/```(?:json)?/gi, '').trim();
  const start = t.indexOf('{');
  if (start === -1) return { data: null, truncated: false };
  t = t.slice(start);
  const tryParse = (s) => { try { return JSON.parse(s); } catch (e) { return undefined; } };
  const end = t.lastIndexOf('}');
  if (end !== -1) {
    const whole = t.slice(0, end + 1);
    let d = tryParse(whole);
    if (d === undefined) d = tryParse(whole.replace(/,\s*([}\]])/g, '$1'));
    if (d !== undefined && d && typeof d === 'object' && !Array.isArray(d)) return { data: d, truncated: false };
  }
  // Salvage: cut after the last complete object inside the top-level "products" array.
  let depth = 0, inStr = false, esc = false, lastRowEnd = -1, arrOpen = -1;
  const prodKey = t.indexOf('"products"');
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (inStr) { if (esc) esc = false; else if (c === '\\') esc = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') { inStr = true; continue; }
    if (c === '{' || c === '[') { depth++; if (c === '[' && depth === 2 && prodKey !== -1 && i > prodKey && arrOpen === -1) arrOpen = i; }
    else if (c === '}' || c === ']') { depth--; if (c === '}' && depth === 2 && arrOpen !== -1) lastRowEnd = i; }
  }
  if (arrOpen !== -1) {
    const body = lastRowEnd !== -1 ? t.slice(0, lastRowEnd + 1) : t.slice(0, arrOpen + 1);
    const d = tryParse(body + ']}');
    if (d !== undefined) return { data: d, truncated: true };
  }
  return { data: null, truncated: false };
}

// ------------------------------------------------- item-text fallback split --
// Safety nets only, for when the model leaves code / colour / size glued in the name.
const COLOUR_WORDS = new Set(['RED', 'MAROON', 'PINK', 'BABY PINK', 'LIGHT PINK', 'DARK PINK', 'HOT PINK', 'RANI', 'RANI PINK', 'MAGENTA', 'PEACH', 'ORANGE', 'RUST', 'YELLOW', 'MUSTARD', 'LEMON', 'GOLD', 'GOLDEN', 'GREEN', 'LIGHT GREEN', 'DARK GREEN', 'PISTA', 'PISTA GREEN', 'MEHENDI', 'MEHNDI', 'OLIVE', 'BOTTLE GREEN', 'SEA GREEN', 'C GREEN', 'PARROT', 'PARROT GREEN', 'RAMA', 'RAMA GREEN', 'FIROZI', 'FIROZA', 'FEROZI', 'TURQUOISE', 'TEAL', 'AQUA', 'BLUE', 'LIGHT BLUE', 'DARK BLUE', 'SKY', 'SKY BLUE', 'NAVY', 'NAVY BLUE', 'N BLUE', 'ROYAL BLUE', 'R BLUE', 'PURPLE', 'LAVENDER', 'LAVEND', 'LILAC', 'VIOLET', 'WINE', 'MAUVE', 'ONION', 'GAJRI', 'GAJARI', 'BROWN', 'COFFEE', 'CHIKOO', 'BEIGE', 'CREAM', 'OFF WHITE', 'OFFWHITE', 'WHITE', 'BLACK', 'GREY', 'GRAY', 'SILVER', 'MULTI', 'MULTICOLOUR', 'MULTICOLOR', 'COPPER', 'KHAKI', 'CORAL', 'MINT', 'CHERRY', 'TOMATO', 'CARROT', 'FONE', 'FAWN', 'STONE']);
function splitLeadingCode(text) {
  if (!text) return null;
  const m = String(text).trim().match(/^(?:D\.?\s*NO\.?\s*[:\-]?\s*)?([A-Z]{0,4}[\-\/]?\d[\dA-Z\-\/]*?)\s*[\-–:]*\s+(.+)$/i);
  if (!m) return null;
  const code = m[1].toUpperCase();
  if ((code.match(/\d/g) || []).length < 3) return null; // too short to be a design code
  const rest = m[2].replace(/\s+/g, ' ').trim();
  if (!/[A-Za-z]{2,}/.test(rest)) return null;
  return COLOUR_WORDS.has(rest.toUpperCase()) ? { design_no: code, colour: rest, name: null } : { design_no: code, colour: null, name: rest };
}
const DITTO_RE = /^[\s"'`´‘’“”.,:;~\-–—_=]*$|^[\s\-–]*(DO|DITTO|SAME|S\.?A\.?A\.?)[\s\-–.]*$/i;
const CHARGE_RE = /\b(COURIER|FREIGHT|PACKING|FORWARDING|CARTAGE|POSTAGE|TRANSPORT|TRANSPORTATION|HAMALI|LOADING|INSURANCE|HANDLING|ROUND\s*OFF|ROUNDING)\b/i;

// ------------------------------------------------------------------- sizes --
const ALPHA_BASE = ['XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL']; // then 3XL, 4XL ... by index
const alphaIndex = (tok) => {
  let t = String(tok).toUpperCase().replace(/[\s.\-]/g, '');
  if (t === 'SMALL') t = 'S'; else if (t === 'MEDIUM') t = 'M'; else if (t === 'LARGE') t = 'L';
  else if (t === 'EXTRALARGE' || t === 'XLARGE') t = 'XL';
  let m;
  if ((m = t.match(/^(\d{1,2})XL$/))) { const n = Number(m[1]); return n >= 1 && n <= 10 ? 4 + n : -1; }
  if ((m = t.match(/^(X+)L$/))) return 4 + m[1].length;
  if ((m = t.match(/^(\d)XS$/))) return Number(m[1]) === 2 ? 0 : -1;
  return ALPHA_BASE.indexOf(t);
};
const cleanTok = (tok) => String(tok).toUpperCase().replace(/[\s.]/g, '');
// Label for a ladder position, in the notation the bill itself uses.
const alphaLabel = (idx, printedToks) => {
  if (!CONFIG.NORMALISE_ALPHA_SIZES) {
    const own = printedToks.map(cleanTok).find((p) => alphaIndex(p) === idx && /^(\d{1,2}X[SL]|X*[SL]|M)$/.test(p));
    if (own) return own;
    if (idx === 6 && printedToks.some((p) => /^2XL$/.test(cleanTok(p)))) return '2XL';
    if (idx >= 7 && printedToks.some((p) => /^X{3,}L$/.test(cleanTok(p)))) return 'X'.repeat(idx - 4) + 'L';
  }
  return idx <= 6 ? ALPHA_BASE[idx] : `${idx - 4}XL`;
};
const isNumTok = (t) => /^\d{1,3}$/.test(t);
const oneSize = (tok) => {
  const ai = alphaIndex(tok);
  if (ai >= 0) return { label: alphaLabel(ai, [tok]), key: 'A' + ai };
  const c = cleanTok(tok);
  if (isNumTok(c)) return { label: String(Number(c)), key: 'N' + Number(c) };
  return null;
};
const asPrinted = (s) => { const label = String(s).replace(/\s+/g, ' ').trim().toUpperCase(); return { label, key: 'R' + alnum(label) }; };
function dedupeSizes(items) {
  const seen = new Set();
  return items.filter((x) => (seen.has(x.key) ? false : (seen.add(x.key), true)));
}

// Returns { sizes:[{label,key}], kind:'single'|'list'|'range' } or { sizes:null, label }
// when the written size cannot be split (kept exactly as written).
function resolveSizes(printed, qty) {
  const original = String(printed).replace(/\s+/g, ' ').trim();
  let s = original.toUpperCase().replace(/^(SIZE|SZ)\s*[:.\-]?\s*/, '');
  const opaque = { sizes: null, label: original.toUpperCase() };
  if (!s) return opaque;
  // "24X38", "16*22": garment trade notation for the size range 24 to 38.
  const star = s.match(/^(\d{1,3})\s*[X*×]\s*(\d{1,3})$/);
  if (star) { if (!CONFIG.X_AND_STAR_ARE_RANGES) return opaque; s = `${star[1]}-${star[2]}`; }
  // Age / month sizes ("2-3Y", "6-12M", "3-4 YRS") are ONE size, not a range.
  if (/\d\s*(Y|YR|YRS|YEAR|YEARS|MTH|MTHS|MONTH|MONTHS|M)\b/.test(s)) return opaque;

  const rangeParts = s.split(/\s*(?:–|—|-|~|\/|\bTO\b)\s*/).filter((x) => x !== '');
  if (rangeParts.length === 1) {
    const listParts = s.split(/[\s,&+]+/).filter((x) => x !== '');
    if (listParts.length === 1) {
      const o = oneSize(listParts[0]);
      return o ? { sizes: [o], kind: 'single' } : opaque;
    }
    const whole = oneSize(s); // e.g. "EXTRA LARGE"
    if (whole) return { sizes: [whole], kind: 'single' };
    const items = listParts.map(oneSize);
    if (items.some((x) => !x)) return opaque;
    return { sizes: dedupeSizes(items), kind: 'list' };
  }
  if (rangeParts.length >= 3) { // explicit list written with - or /   (S/M/L/XL)
    const items = rangeParts.map(oneSize);
    if (items.some((x) => !x)) return opaque;
    return { sizes: dedupeSizes(items), kind: 'list' };
  }

  const [p1, p2] = rangeParts;
  const a1 = alphaIndex(p1), a2 = alphaIndex(p2);
  if (a1 >= 0 && a2 >= 0) {
    if (a2 <= a1 || a2 - a1 + 1 > CONFIG.MAX_SIZES_PER_RANGE) return opaque;
    const out = [];
    for (let i = a1; i <= a2; i++) out.push({ label: alphaLabel(i, [p1, p2]), key: 'A' + i });
    return { sizes: out, kind: 'range' };
  }
  const c1 = cleanTok(p1), c2 = cleanTok(p2);
  if (isNumTok(c1) && isNumTok(c2)) {
    const a = Number(c1), b = Number(c2);
    if (b <= a) return opaque;
    const sameParity = (a % 2) === (b % 2);
    // Candidate steps, most likely first. Garment number sizes run in 2s
    // (12,14,16 / 18,20,22 / 24..38), innerwear cm sizes in 5s (75,80,85),
    // small numbers (0-11: kids size no. / footwear) usually in 1s.
    let steps;
    if (a >= 60 && a % 5 === 0 && b % 5 === 0) steps = [5];
    else if (a >= 12) steps = sameParity ? (a % 2 === 0 ? [2] : [2, 1]) : [1];
    else steps = sameParity && a % 2 === 0 ? [2, 1] : (sameParity ? [1, 2] : [1]);
    const build = (st) => { const o = []; for (let v = a; v <= b; v += st) o.push({ label: String(v), key: 'N' + v }); return o; };
    const cands = steps.map(build).filter((c) => c.length >= 2 && c.length <= CONFIG.MAX_SIZES_PER_RANGE);
    if (cands.length === 0) return opaque;
    const fits = cands.filter((c) => qty && qty % c.length === 0);
    if (fits.length === 0) return { sizes: cands[0], kind: 'range' }; // caller keeps the range whole (uneven)
    return { sizes: fits[0], kind: 'range', ambiguous: fits.length > 1 };
  }
  return opaque;
}

// --------------------------------------------------- 1. parse every page ----
const globalNotes = [];
const note = (msg) => { if (!globalNotes.includes(msg)) globalNotes.push(msg); };
const pageResults = [];

const pairedIndex = (it, fallback) => {
  const p = it.pairedItem;
  if (p === undefined || p === null) return fallback;
  if (typeof p === 'number') return p;
  if (Array.isArray(p)) return p[0] && typeof p[0].item === 'number' ? p[0].item : fallback;
  return typeof p.item === 'number' ? p.item : fallback;
};

for (let i = 0; i < pageInputs.length; i++) {
  const response = pageInputs[i].json;
  const srcIdx = pairedIndex(pageInputs[i], i);
  const pagePrep = (prepAll[srcIdx] && prepAll[srcIdx].json) || (prepAll[i] && prepAll[i].json) || prep;
  const pageIndex = pagePrep.page_index || i + 1;
  const pageLabel = `page ${pageIndex}`;

  const { data: extracted, truncated } = parseModelJson(extractRawText(response));
  if (!extracted) {
    return [{
      json: {
        success: false,
        error: 'NO_PARSEABLE_RESULT',
        message: `Model did not return a parseable JSON extraction for ${pageLabel}.`,
        request_id: prep.request_id,
        shop_id: prep.shop_id
      }
    }];
  }
  if (truncated) note(`${pageLabel}: the model's answer was cut off - the last rows of this page may be missing. Re-check against the bill.`);

  // ---- header ----
  let gstin = extracted.gstin ? alnum(extracted.gstin) : null;
  const buyerGstin = extracted.buyer_gstin ? alnum(extracted.buyer_gstin) : null;
  const pageGstins = [...new Set((Array.isArray(extracted.all_gstins) ? extracted.all_gstins : []).map(alnum).filter((g) => GSTIN_RE.test(g)))];
  const sellerWasBuyer = !!(gstin && buyerGstin && gstin === buyerGstin);
  if (sellerWasBuyer) gstin = null;
  // Seller GSTIN left empty by the model: if the page shows exactly one GSTIN
  // other than the buyer's, that one is the seller's.
  if (!gstin && buyerGstin) {
    const others = pageGstins.filter((g) => g !== buyerGstin);
    if (others.length === 1) gstin = others[0];
  }
  if (sellerWasBuyer && !gstin) {
    note(`${pageLabel}: the GSTIN read as the seller's is the buyer's own GSTIN - ignored (the seller's mobile is used instead when printed).`);
  }
  if (gstin && !GSTIN_RE.test(gstin)) {
    note(`${pageLabel}: GSTIN "${gstin}" is not in valid GSTIN format - please verify.`);
  } else if (gstin && !gstinChecksumOk(gstin)) {
    note(`${pageLabel}: GSTIN "${gstin}" fails the GSTIN check-digit test - one character is probably misread.`);
  }
  let mobile = extracted.mobile_number ? String(extracted.mobile_number).replace(/\D/g, '') : null;
  if (mobile && mobile.length > 10) mobile = mobile.slice(-10); // drop +91 / 0 prefix
  if (mobile && !MOBILE_RE.test(mobile)) { note(`${pageLabel}: mobile "${mobile}" is not a valid 10-digit mobile number.`); mobile = null; }

  let bill_date = toStr(extracted.bill_date);
  if (bill_date && !DATE_RE.test(bill_date)) {
    note(`${pageLabel}: bill_date "${bill_date}" is not valid ISO format`);
    bill_date = null;
  } else if (bill_date) {
    const parsed = new Date(bill_date + 'T00:00:00');
    const tomorrow = new Date(Date.now() + 24 * 3600 * 1000);
    if (isNaN(parsed.getTime()) || parsed > tomorrow) {
      note(`${pageLabel}: bill_date "${bill_date}" is invalid or in the future`);
      bill_date = null;
    }
  }
  let billNo = toStr(extracted.party_bill_number);
  if (billNo) billNo = billNo.replace(/\s*([\/\-])\s*/g, '$1');

  const docType = String(toStr(extracted.document_type) || 'unknown').toLowerCase().replace(/[\s\-]+/g, '_');
  const handwritten = !!extracted.handwritten;
  const pageBillGst = toNum(extracted.bill_gst_percent);

  // ---- rows ----
  const rawProducts = Array.isArray(extracted.products) ? extracted.products : [];
  const rows = [];
  const charges = [];
  (Array.isArray(extracted.other_charges) ? extracted.other_charges : []).forEach((c) => {
    const amount = toNum(c && c.amount);
    if (amount) charges.push({ name: toStr(c.name) || 'Other charge', amount, gst_percent: toNum(c.gst_percent) });
  });

  rawProducts.forEach((p, idx) => {
    if (!p || typeof p !== 'object') return;
    const srNo = toNum(p.sr_no);
    const rowLabel = `${pageLabel} row ${srNo !== null ? srNo : idx + 1}`;
    let design = toStr(p.design_no);
    let name = toStr(p.product_name);
    const rawText = toStr(p.raw_text);
    let size = toStr(p.size);
    let attrsFromName = !!p.attributes_from_name;

    // colours: array preferred; a single string is split on "/" or ","
    let colours = Array.isArray(p.colours) ? p.colours : (toStr(p.colour) ? String(p.colour).split(/\s*[\/,]\s*/) : []);
    colours = [...new Set(colours.map((c) => (toStr(c) || '').toUpperCase().replace(/[\s\-–:.,]+$/, '')).filter(Boolean))];

    if (design) design = design.replace(/^(D\.?\s*NO\.?|DESIGN\s*NO\.?|ART\.?\s*NO\.?|STYLE\s*NO\.?)\s*[:\-]?\s*/i, '').replace(/[\s\-–:]+$/, '').trim() || null;
    if (name && DITTO_RE.test(name)) name = null;
    if (!design) {
      const sp = splitLeadingCode(name) || (!name ? splitLeadingCode(rawText) : null);
      if (sp) { design = sp.design_no; name = sp.name; if (sp.colour && colours.length === 0) { colours = [sp.colour.toUpperCase()]; attrsFromName = true; } }
    }
    // size left inside the description ("PLAZO 24X38")
    if (!size && name && CONFIG.X_AND_STAR_ARE_RANGES) {
      const m = name.match(/(?:^|\s)(\d{2})\s*[xX*×]\s*(\d{2})(?=\s|$)/);
      if (m) { size = `${m[1]}X${m[2]}`; name = name.replace(m[0], ' ').replace(/\s+/g, ' ').trim() || null; attrsFromName = true; }
    }
    if (name) name = name.replace(/[\s\-–:.,]+$/, '').trim() || null;
    if (design && name && alnum(name) === alnum(design)) name = null;

    // Ditto / blank row: same design and description as the row above.
    const prev = rows[rows.length - 1];
    const isDitto = !design && !name && (!rawText || DITTO_RE.test(rawText) || alnum(rawText) === alnum(size) || alnum(rawText) === alnum(colours.join('')));
    let carried = false;
    if (isDitto && prev && !p.cut_off) { design = prev.design_no; name = prev.product_name; carried = true; }
    else if (design && !name && prev && prev.product_name && (!rawText || alnum(rawText) === alnum(design))) {
      name = prev.product_name; // description printed once, blank on the rows below
    }
    if (!design && !name && rawText && !carried) name = rawText; // never lose the row's identity

    let quantity = toNum(p.quantity);
    const rate = toNum(p.rate !== undefined && p.rate !== null ? p.rate : p.cost_price);
    const amount = toNum(p.amount);
    const disc = toNum(p.discount_percent);
    const discAmt = toNum(p.discount_amount);
    const gst = toNum(p.gst_percent);
    const hsn = p.hsn_code !== undefined && p.hsn_code !== null ? (String(p.hsn_code).replace(/\D/g, '') || null) : null;

    // A courier / freight line that came back as a product row.
    if (!design && colours.length === 0 && !size && name && CHARGE_RE.test(name) && (quantity === null || quantity <= 1)) {
      const amt = amount !== null ? amount : rate;
      if (amt) charges.push({ name, amount: amt, gst_percent: gst });
      return;
    }

    // ---- size grid cells ----
    const rawCells = Array.isArray(p.size_cells) ? p.size_cells : (Array.isArray(p.cells) ? p.cells : []);
    let cells = rawCells.map((c) => {
      if (Array.isArray(c)) return { size: c[0], qty: c[1], rate: c[2], mrp: c[3] };
      if (!c || typeof c !== 'object') return null;
      return { size: c.s !== undefined ? c.s : c.size, qty: c.q !== undefined ? c.q : (c.qty !== undefined ? c.qty : c.pcs), rate: c.r !== undefined ? c.r : c.rate, mrp: c.m !== undefined ? c.m : c.mrp };
    }).filter(Boolean).map((c) => ({ size: toStr(c.size), qty: toNum(c.qty), rate: toNum(c.rate), mrp: toNum(c.mrp) }))
      .filter((c) => c.size !== null && c.qty !== null && c.qty > 0);

    let cost = rate;
    let taxable = quantity !== null && rate !== null ? round2(quantity * rate) : null;

    if (cells.length > 0) {
      const cellQty = sum(cells, (c) => c.qty);
      if (quantity === null) quantity = cellQty;
      else if (cellQty !== quantity) { note(`${rowLabel}: the size grid adds up to ${cellQty} pcs but the Qty column says ${quantity} - a size cell was missed or misread.`); quantity = cellQty; }
      const noRates = cells.every((c) => c.rate === null);
      const flat = rate !== null ? rate : (amount !== null && cellQty > 0 ? round2(amount / cellQty) : null);
      cells.forEach((c) => { c.cost = c.rate !== null ? c.rate : flat; });
      if (cells.some((c) => c.cost === null)) note(`${rowLabel}: a size has no rate - please enter the cost.`);
      const computed = round2(sum(cells, (c) => c.qty * (c.cost || 0)));
      taxable = computed;
      if (amount !== null && computed > 0 && !noRates) {
        const tol = Math.max(CONFIG.AMOUNT_TOLERANCE, computed * 0.002);
        if (near(computed, amount, tol)) taxable = amount;
        else if ((disc && near(computed * (1 - disc / 100), amount, tol)) || (discAmt && near(computed - discAmt, amount, tol))) {
          const f = amount / computed;
          cells.forEach((c) => { c.cost = round2(c.cost * f); });
          taxable = amount;
          note(`${rowLabel}: line discount applied - costs taken net of discount.`);
        } else if (gst && near(computed * (1 + gst / 100), amount, tol)) taxable = computed;
        else note(`${rowLabel}: the size-wise rates add up to ${inr(computed)} but the printed amount is ${inr(amount)} - a size or rate is under the wrong column, please check.`);
      } else if (amount !== null && noRates) taxable = amount;
      // size labels as printed in the grid header
      const seen = new Set();
      cells = cells.map((c) => { const s = oneSize(c.size) || asPrinted(c.size); return { ...c, label: s.label, key: s.key }; })
        .filter((c) => { if (seen.has(c.key)) { note(`${rowLabel}: size ${c.label} appears twice in the grid - please check.`); return false; } seen.add(c.key); return true; });
      cost = cells[0].cost;
      size = null;
    } else if (quantity !== null && rate !== null && amount !== null && quantity > 0) {
      // Row maths: qty x rate must explain the printed amount.
      const gross = quantity * rate;
      const tol = Math.max(CONFIG.AMOUNT_TOLERANCE, gross * 0.002);
      if (near(gross, amount, tol)) {
        taxable = amount;
      } else if ((disc && near(gross * (1 - disc / 100), amount, tol)) || (discAmt && near(gross - discAmt, amount, tol))) {
        cost = round2(amount / quantity); taxable = amount;
        note(`${rowLabel}: line discount applied - cost taken as net ${cost} (printed rate ${rate}).`);
      } else if (gst && near(gross * (1 + gst / 100), amount, tol)) {
        taxable = round2(gross); // amount column is tax-inclusive
      } else {
        note(`${rowLabel}: quantity x rate (${quantity} x ${rate} = ${round2(gross)}) does not match the printed amount ${amount} - one of them is misread, please check.`);
      }
    } else if (quantity !== null && rate === null && amount !== null && quantity > 0) {
      cost = round2(amount / quantity); taxable = amount; // rate column missing: derive it
    }

    if ((!design && !name) || quantity === null || cost === null) {
      note(`${rowLabel}: missing required data (name/quantity/rate) - read as ${JSON.stringify({ item: rawText || name || design, quantity, rate })}`);
    }
    const what = rawText || [design, name, colours.join('/')].filter(Boolean).join(' ');
    if (p.cut_off) note(`${rowLabel}: part of this row is hidden by the photo edge or binder (${what || 'item'}) - retake the photo with the whole table in frame.`);
    else if (p.unclear) note(`${rowLabel}: part of this row was hard to read - please verify (${what}).`);

    rows.push({
      page: pageIndex, sr_no: srNo, raw_text: rawText,
      design_no: design ? design.toUpperCase() : null,
      product_name: name,
      colours, size, cells, quantity, cost_price: cost, mrp: toNum(p.mrp), taxable, gst_percent: gst, hsn_code: hsn,
      attrs_from_name: attrsFromName, carried
    });
  });

  const rowCount = toNum(extracted.row_count);
  if (!truncated && rowCount !== null && rowCount !== rawProducts.length) {
    note(`${pageLabel}: the model counted ${rowCount} item rows but returned ${rawProducts.length} - a row may be missing or repeated.`);
  }
  if (extracted.review_reason) note(`${pageLabel}: ${extracted.review_reason}`);
  if (extracted.edge_cut && !rawProducts.some((p) => p && p.cut_off)) note(`${pageLabel}: part of the items table is hidden by the photo edge, a fold or the binder - check the rows near the edge or retake the photo.`);

  const t = extracted.totals && typeof extracted.totals === 'object' ? extracted.totals : {};
  const b = extracted.document_behind && typeof extracted.document_behind === 'object' ? extracted.document_behind : {};
  pageResults.push({
    page_index: pageIndex,
    doc_type: docType, handwritten,
    page_number: toNum(extracted.page_number), page_count: toNum(extracted.page_count), continues: !!extracted.continues_on_next_page,
    party_bill_number: billNo,
    bill_date,
    party_name: plain(toStr(extracted.party_name)),
    gstin, mobile, buyer_gstin: buyerGstin,
    rows, charges, bill_gst_percent: pageBillGst,
    totals: {
      total_quantity: toNum(t.total_quantity), items_total: toNum(t.items_total), bill_discount: toNum(t.bill_discount),
      taxable_total: toNum(t.taxable_total), tax_total: toNum(t.tax_total), round_off: toNum(t.round_off), grand_total: toNum(t.grand_total)
    },
    behind: { party_name: plain(toStr(b.party_name)), gstin: b.gstin ? alnum(b.gstin) : null, bill_number: toStr(b.bill_number), grand_total: toNum(b.grand_total) },
    needs_review: !!extracted.needs_review
  });
}
pageResults.sort((a, b) => a.page_index - b.page_index);

// ------------------------------------- 2. duplicate pages / duplicate rows --
// The same page reaching the parser twice (same photo attached twice, the page
// photographed twice, overlapping photos of a long bill) must never double the stock.
const rowSig = (r) => [r.sr_no === null ? '' : r.sr_no, r.design_no || '', alnum(r.product_name), alnum(r.colours.join('')), alnum(r.size), r.quantity, r.cost_price, r.taxable, r.cells.map((c) => `${c.key}:${c.qty}:${c.cost}`).join(',')].join('|');
let duplicatePagesRemoved = 0;
let duplicateRowsRemoved = 0;
const SLIP_TYPES = new Set(['cash_memo', 'delivery_challan', 'estimate', 'packing_slip', 'packing_list', 'proforma', 'challan']);
const isSlip = (p) => SLIP_TYPES.has(p.doc_type);
const isInvoice = (p) => p.doc_type === 'tax_invoice';
const slipAndInvoice = (a, b) => (isSlip(a) && isInvoice(b)) || (isInvoice(a) && isSlip(b)); // handled in step 3, not a duplicate page

let keptPages = [];
for (const pg of pageResults) {
  const sig = pg.rows.map(rowSig).join('\n');
  const twin = pg.rows.length > 0 ? keptPages.find((k) => !slipAndInvoice(k, pg) && k.rows.length === pg.rows.length && k.rows.map(rowSig).join('\n') === sig) : null;
  if (twin) {
    duplicatePagesRemoved++;
    note(`Page ${pg.page_index} is the same bill page as page ${twin.page_index} (identical rows) - counted once.`);
    ['party_bill_number', 'bill_date', 'party_name', 'gstin', 'mobile'].forEach((f) => { if (!twin[f] && pg[f]) twin[f] = pg[f]; });
    Object.keys(twin.totals).forEach((f) => { if (twin.totals[f] === null && pg.totals[f] !== null) twin.totals[f] = pg.totals[f]; });
    continue;
  }
  keptPages.push(pg);
}

// ------------------------- 3. memo / challan sent together with its invoice --
// A cash memo or delivery challan and the tax invoice for the same goods carry
// the same total. Their rows must not be added together.
const pagesTaxable = (pages) => {
  let v = null; pages.forEach((p) => { if (p.totals.taxable_total !== null) v = p.totals.taxable_total; else if (v === null && p.totals.items_total !== null) v = p.totals.items_total; });
  return v !== null ? v : round2(sum(pages.flatMap((p) => p.rows), (r) => r.taxable));
};
const pagesGrand = (pages) => { let v = null; pages.forEach((p) => { if (p.totals.grand_total !== null) v = p.totals.grand_total; }); return v; };

let companion = null;
let headerPages = keptPages;
{
  const slips = keptPages.filter((p) => isSlip(p) && p.rows.length > 0);
  const invoices = keptPages.filter((p) => isInvoice(p) && p.rows.length > 0);
  if (slips.length > 0 && invoices.length > 0) {
    const st = pagesTaxable(slips), it = pagesTaxable(invoices), sg = pagesGrand(slips), ig = pagesGrand(invoices);
    const sameBill = (st !== null && it !== null && near(st, it)) || (sg !== null && ig !== null && near(sg, ig));
    if (sameBill) {
      const useSlip = CONFIG.COMPANION_PRODUCTS_FROM !== 'invoice';
      const src = useSlip ? slips : invoices;
      const other = useSlip ? invoices : slips;
      const slipName = slips[0].doc_type === 'cash_memo' ? 'cash memo' : 'delivery challan / estimate';
      const sRows = slips.flatMap((p) => p.rows), iRows = invoices.flatMap((p) => p.rows);
      const sameRows = sRows.length === iRows.length && sRows.every((r, i) => r.quantity === iRows[i].quantity && r.cost_price === iRows[i].cost_price);
      if (sameRows && useSlip) { // identical rows: borrow GST % and HSN the slip does not print
        sRows.forEach((r, i) => { if (r.gst_percent === null) r.gst_percent = iRows[i].gst_percent; if (!r.hsn_code) r.hsn_code = iRows[i].hsn_code; });
        if (slips.every((p) => p.bill_gst_percent === null) && invoices.some((p) => p.bill_gst_percent !== null)) slips[0].bill_gst_percent = invoices.find((p) => p.bill_gst_percent !== null).bill_gst_percent;
      }
      const sq = sum(sRows, (r) => r.quantity), iq = sum(iRows, (r) => r.quantity);
      note(`A ${slipName} and a tax invoice with the same total (${inr(st !== null ? st : sg)}) were sent together - treated as one bill. Products are taken from the ${useSlip ? slipName : 'tax invoice'}; bill number, date, party and GSTIN from the tax invoice.`);
      if (!sameRows) note(`The ${slipName} and the tax invoice list different items: ${sq} pcs on the ${slipName}, ${iq} pcs on the tax invoice. Check which one matches the goods received.`);
      // totals the source document does not print are borrowed from the other one
      const srcLast = src[src.length - 1];
      other.forEach((p) => Object.keys(p.totals).forEach((f) => { if (f !== 'total_quantity' && srcLast.totals[f] === null && src.every((x) => x.totals[f] === null) && p.totals[f] !== null) srcLast.totals[f] = p.totals[f]; }));
      if (src.every((p) => p.charges.length === 0)) other.forEach((p) => { srcLast.charges.push(...p.charges); });
      companion = { products_from: useSlip ? slips[0].doc_type : 'tax_invoice', header_from: 'tax_invoice', rows_identical: sameRows, memo_pieces: sq, invoice_pieces: iq };
      headerPages = [...invoices, ...slips];
      keptPages = src;
    }
  }
}

// Row level: a serial number is printed only once on a bill. The same sr_no
// with identical content is the same printed row seen twice (model repeated it,
// or two overlapping photos of a long bill). When two pages are proven to
// overlap, a shared sr_no that was read DIFFERENTLY is also one row: keep the
// first reading and flag it.
const seenSig = new Map(); // row signature -> page
const seenSr = new Map();  // sr_no -> page
for (const pg of keptPages) {
  const overlapWith = new Set();
  for (const r of pg.rows) {
    if (r.sr_no === null) continue;
    const prev = seenSig.get(rowSig(r));
    if (prev !== undefined && prev !== pg.page_index) overlapWith.add(prev);
  }
  let same = 0;
  const misread = [];
  pg.rows = pg.rows.filter((r) => {
    if (r.sr_no === null) return true;
    const sig = rowSig(r);
    if (seenSig.has(sig)) { same++; return false; }
    const otherPage = seenSr.get(r.sr_no);
    if (otherPage !== undefined && otherPage !== pg.page_index && overlapWith.has(otherPage)) { misread.push(r.sr_no); return false; }
    seenSig.set(sig, pg.page_index);
    if (!seenSr.has(r.sr_no)) seenSr.set(r.sr_no, pg.page_index);
    return true;
  });
  duplicateRowsRemoved += same + misread.length;
  if (same > 0) note(`Page ${pg.page_index}: ${same} row(s) had already been read (same serial number and values) - counted once.`);
  if (misread.length > 0) note(`Page ${pg.page_index}: row(s) ${misread.join(', ')} overlap with an earlier photo but were read differently - kept the first reading, please verify those rows.`);
}

// ------------------------------------------- 4. cross-page header checks ----
// A page with no header info is a continuation page and inherits the bill
// identity. Pages that DO carry identity must agree; otherwise reject the batch.
const hasIdentity = (p) => p.party_bill_number || p.party_name || p.gstin || p.mobile;
const identityPool = companion ? headerPages.filter(isInvoice) : headerPages; // a memo's own number never clashes with its invoice
const pagesWithIdentity = identityPool.filter(hasIdentity);
const differs = (x, y) => x && y && alnum(x) !== alnum(y);
let conflict = null;
for (let i = 1; i < pagesWithIdentity.length && !conflict; i++) {
  const a = pagesWithIdentity[0];
  const b = pagesWithIdentity[i];
  const billClash = differs(a.party_bill_number, b.party_bill_number);
  const gstClash = a.gstin && b.gstin && GSTIN_RE.test(a.gstin) && GSTIN_RE.test(b.gstin) && a.gstin !== b.gstin;
  const comparableIds = (a.party_bill_number && b.party_bill_number) || (a.gstin && b.gstin);
  const nameClash = !comparableIds && differs(a.party_name, b.party_name);
  if (billClash || gstClash || nameClash) conflict = { page_a: a.page_index, page_b: b.page_index };
}
if (conflict) {
  return [{
    json: {
      success: false,
      error: 'MULTIPLE_BILLS_OR_VENDORS_DETECTED',
      message: `Page ${conflict.page_a} and page ${conflict.page_b} show different vendor details / bill numbers. Only pages from a single vendor's single bill can be parsed together - send each bill as a separate request.`,
      request_id: prep.request_id,
      shop_id: prep.shop_id
    }
  }];
}

const firstOf = (f) => { const p = headerPages.find((x) => x[f]); return p ? p[f] : null; };
let mergedGstin = firstOf('gstin');
const mergedMobile = firstOf('mobile');
let partyName = firstOf('party_name');

const allRows = keptPages.flatMap((p) => p.rows);
const allCharges = keptPages.flatMap((p) => p.charges);

// Printed totals are normally on the last page; take the last non-null value.
const printed = {};
['total_quantity', 'items_total', 'bill_discount', 'taxable_total', 'tax_total', 'round_off', 'grand_total'].forEach((f) => {
  printed[f] = null;
  keptPages.forEach((p) => { if (p.totals[f] !== null) printed[f] = p.totals[f]; });
});

// GST %: the row's own rate; otherwise the single rate written for the whole
// bill. Never worked out from the price.
const billGst = (() => { const v = [...new Set(keptPages.map((p) => p.bill_gst_percent).filter((x) => x !== null))]; return v.length === 1 ? v[0] : null; })();
const rowRates = [...new Set(allRows.map((r) => r.gst_percent).filter((x) => x !== null))];
const fillGst = billGst !== null ? billGst : (rowRates.length === 1 ? rowRates[0] : null);
if (fillGst !== null) allRows.forEach((r) => { if (r.gst_percent === null) r.gst_percent = fillGst; });
const noGstRows = allRows.filter((r) => r.gst_percent === null).length;
if (allRows.length > 0 && noGstRows === allRows.length) note('GST % is not printed on this document - left blank for every product.');
else if (noGstRows > 0) note(`GST % is not printed for ${noGstRows} row(s) and the bill has more than one rate - left blank for those products.`);
allCharges.forEach((c) => { if (c.gst_percent === null && fillGst !== null && rowRates.length <= 1) c.gst_percent = fillGst; });

const sumQty = round2(sum(allRows, (r) => r.quantity));
const sumTaxable = round2(sum(allRows, (r) => r.taxable));
const chargesTotal = round2(sum(allCharges, (c) => c.amount));
const computedTax = noGstRows === 0 && allRows.length > 0
  ? round2(sum(allRows, (r) => (r.taxable || 0) * r.gst_percent / 100) + sum(allCharges, (c) => c.amount * (c.gst_percent || 0) / 100))
  : null;
const computedGrand = computedTax !== null ? round2(sumTaxable + chargesTotal - (printed.bill_discount || 0) + computedTax) : null;

// A memo with no seller printed: the invoice lying under it names the seller,
// but only when it is the same bill (same final amount).
if (!partyName) {
  const topGrand = printed.grand_total !== null ? printed.grand_total : computedGrand;
  const b = keptPages.map((p) => p.behind).find((x) => x.party_name && x.grand_total !== null && topGrand !== null && near(x.grand_total, topGrand));
  if (b) {
    partyName = b.party_name;
    const buyer = firstOf('buyer_gstin');
    if (!mergedGstin && b.gstin && GSTIN_RE.test(b.gstin) && b.gstin !== buyer) mergedGstin = b.gstin;
    note(`No seller is printed on this document. Party "${partyName}" was taken from the bill lying under it, which has the same total (${inr(b.grand_total)}) - please verify.`);
  }
}

const mergedHeader = {
  party_bill_number: firstOf('party_bill_number'),
  bill_date: firstOf('bill_date'),
  party_name: partyName,
  // GSTIN preferred; falls back to the first mobile number; blank if neither.
  mobile_or_gst: (mergedGstin && GSTIN_RE.test(mergedGstin) ? mergedGstin : null) || mergedMobile || mergedGstin || null
};

let needsReview = keptPages.some((p) => p.needs_review) || headerPages.filter(hasIdentity).length === 0;
const reviewNotes = globalNotes; // same array: later notes are appended in order
['party_bill_number', 'bill_date', 'party_name', 'mobile_or_gst'].forEach((f) => {
  if (!mergedHeader[f]) note(`${f} could not be read from the bill`);
});
if (allRows.length === 0) note('No product line items were found on the bill.');
if (keptPages.some((p) => p.handwritten)) note('This bill is handwritten - digits were cross-checked with quantity x rate = amount; please look over the flagged rows.');

// ------------------------------------ 5. reconcile with the printed totals --
const pagesExpected = Math.max(0, ...keptPages.map((p) => p.page_count || 0));
const pageNumbers = new Set(keptPages.map((p) => p.page_number).filter((x) => x !== null));
const lastPage = keptPages[keptPages.length - 1];
const pagesMissing = (pagesExpected > 0 && (pageNumbers.size > 0 ? pageNumbers.size : keptPages.length) < pagesExpected) || (!!lastPage && lastPage.continues);
if (pagesMissing) note(`This bill continues on another page${pagesExpected ? ` (${pagesExpected} pages in all)` : ''} that was not sent - send every page together so the totals can be checked.`);

const recon = { quantity: 'not_printed', amount: 'not_printed', tax: 'not_printed', grand_total: 'not_printed' };
if (pagesMissing) {
  recon.quantity = recon.amount = recon.tax = recon.grand_total = 'incomplete';
} else if (allRows.length > 0) {
  if (printed.total_quantity !== null) {
    recon.quantity = near(sumQty, printed.total_quantity, 0.001) ? 'matched' : 'mismatch';
    if (recon.quantity === 'mismatch') note(`Quantity check failed: rows add up to ${sumQty} pcs but the bill prints a total of ${printed.total_quantity} pcs - a row is missing, repeated or misread.`);
  }
  const amtTol = Math.max(CONFIG.AMOUNT_TOLERANCE, sumTaxable * 0.0005);
  const adj = sumTaxable + chargesTotal - (printed.bill_discount || 0);
  const targets = [];
  if (printed.items_total !== null) targets.push([sumTaxable, printed.items_total]);
  if (printed.taxable_total !== null) { targets.push([sumTaxable, printed.taxable_total]); targets.push([adj, printed.taxable_total]); }
  if (targets.length > 0) {
    recon.amount = targets.some(([x, y]) => near(x, y, amtTol)) ? 'matched' : 'mismatch';
    if (recon.amount === 'mismatch') {
      const ref = printed.taxable_total !== null ? printed.taxable_total : printed.items_total;
      note(`Amount check failed: rows add up to ${sumTaxable} (before tax) but the bill prints ${ref} - a rate/quantity is misread, or a row is on the wrong size/rate line.`);
    }
  }
  if (printed.tax_total !== null && computedTax !== null) {
    recon.tax = near(computedTax, printed.tax_total, Math.max(CONFIG.AMOUNT_TOLERANCE, printed.tax_total * 0.002)) ? 'matched' : 'mismatch';
    if (recon.tax === 'mismatch' && recon.amount !== 'mismatch') note(`GST check failed: the rows at their GST % give ${inr(computedTax)} tax but the bill prints ${inr(printed.tax_total)} - a GST % or an amount is misread.`);
  }
  if (printed.grand_total !== null) {
    const cands = [];
    if (computedGrand !== null) cands.push(computedGrand);
    if (printed.tax_total !== null) cands.push(round2(adj + printed.tax_total + (printed.round_off || 0)), round2(adj + printed.tax_total));
    if (cands.length > 0) {
      recon.grand_total = cands.some((x) => near(x, printed.grand_total, Math.max(CONFIG.AMOUNT_TOLERANCE, 1.5))) ? 'matched' : 'mismatch';
      if (recon.grand_total === 'mismatch' && recon.amount !== 'mismatch') note(`Bill amount check failed: rows plus tax give ${inr(cands[0])} but the bill prints ${inr(printed.grand_total)}.`);
    } else if (recon.amount === 'not_printed' && near(sumTaxable + chargesTotal, printed.grand_total, amtTol)) {
      recon.grand_total = 'matched'; // challan / estimate with one untaxed total
    }
  }
}

// --------------------------------------------------- 6. rows -> products ----
const normName = (s) => (s || '').replace(/\s+/g, ' ').trim().toUpperCase();
const displayName = (design, name) => [design, name].filter(Boolean).join(' ');

// Group by design number when the bill prints one (one design = one product).
// Rows with only a name (no design, colour or size) are one product each.
// Rows with a name plus colour/size are grouped by the exact name.
const groups = [];
const groupIndex = {};
const nameOnlyCount = {};
allRows.forEach((r) => { r.has_attrs = r.colours.length > 0 || !!r.size || r.cells.length > 0; if (!r.design_no && !r.has_attrs) { const k = normName(r.product_name); nameOnlyCount[k] = (nameOnlyCount[k] || 0) + 1; } });
const nameOnlySeq = {};
allRows.forEach((r, idx) => {
  let key, label = null;
  if (r.design_no) key = 'D:' + alnum(r.design_no);
  else if (!r.has_attrs && CONFIG.NAME_ONLY_ROWS === 'separate') {
    key = 'R:' + idx;
    const k = normName(r.product_name);
    if (nameOnlyCount[k] > 1) { nameOnlySeq[k] = (nameOnlySeq[k] || 0) + 1; label = `${r.product_name || 'ITEM'} ${nameOnlySeq[k]}`; }
  } else key = 'N:' + normName(r.product_name);
  if (groupIndex[key] === undefined) { groupIndex[key] = groups.length; groups.push({ key, label, rows: [] }); }
  groups[groupIndex[key]].rows.push(r);
});
Object.keys(nameOnlySeq).forEach((k) => note(`"${k}" is printed on ${nameOnlySeq[k]} rows with no design number, colour or size - kept as ${nameOnlySeq[k]} separate products ("${k} 1" to "${k} ${nameOnlySeq[k]}"); rename or merge them as needed.`));

const sizeNotes = {};      // written range -> description (one note per distinct range)
const unevenNotes = [];
const colourNotes = [];

// One bill row -> one or more variants.
const rowToVariants = (r, pname) => {
  const colours = r.colours.length ? r.colours : [null];
  const k = colours.length;
  const joined = k > 1 ? colours.join(' / ') : colours[0];
  const out = [];
  const push = (colour, size, qty, cost, mrp, grid) => out.push({ colour, sizeLabel: size ? size.label : null, sizeKey: size ? size.key : '', qty, cost, mrp: mrp === undefined ? r.mrp : mrp, rows: [r.sr_no], grid: !!grid });
  const where = `"${pname}"${r.sr_no !== null ? ' row ' + r.sr_no : ''}`;

  // size grid: one variant per filled size cell
  if (r.cells.length > 0) {
    const ok = k === 1 || r.cells.every((c) => c.qty % k === 0);
    if (k > 1 && ok) colourNotes.push(`${where} lists ${k} colours (${joined}) for one quantity - split equally between the colours; verify.`);
    if (k > 1 && !ok) colourNotes.push(`${where} lists ${k} colours (${joined}) but the pieces do not divide equally - kept as one "${joined}" variant; split by colour manually.`);
    (ok ? colours : [joined]).forEach((colour) => r.cells.forEach((c) => push(colour, c, ok ? c.qty / k : c.qty, c.cost, c.mrp, true)));
    return out;
  }

  const q = r.quantity;
  const perColour = k > 1 && q !== null && q > 0 && q % k === 0 ? q / k : null;
  if (k > 1 && perColour === null) {
    colourNotes.push(`${where} lists ${k} colours (${joined}) but ${q} pcs do not divide equally - kept as one "${joined}" variant; split by colour manually.`);
    push(joined, r.size ? asPrinted(r.size) : null, q, r.cost_price);
    return out;
  }
  const cq = k > 1 ? perColour : q;
  const res = r.size ? resolveSizes(r.size, cq) : null;
  const n = res && res.sizes ? res.sizes.length : 0;
  if (k > 1) colourNotes.push(`${where} lists ${k} colours (${joined}) for ${q} pcs - split equally (${cq} pcs per colour${n > 1 && cq % n === 0 ? `, ${cq / n} per size` : ''}); verify.`);
  let unevenDone = false;
  colours.forEach((colour) => {
    if (!r.size) return push(colour, null, cq, r.cost_price);
    if (!res.sizes) return push(colour, asPrinted(res.label), cq, r.cost_price);
    if (n === 1) return push(colour, res.sizes[0], cq, r.cost_price);
    if (cq !== null && cq > 0 && cq % n === 0) {
      if (res.kind === 'range') sizeNotes[r.size.toUpperCase()] = `Size "${r.size}" was split into ${res.sizes.map((s) => s.label).join(', ')} with the quantity divided equally${res.ambiguous ? ' (step between sizes is a guess)' : ''}`;
      return res.sizes.forEach((s) => push(colour, s, cq / n, r.cost_price));
    }
    // Does not divide evenly -> keep the row whole, exactly as written. Nothing dropped.
    if (!unevenDone) unevenNotes.push(`${where} ${joined ? joined + ' ' : ''}${r.size} qty ${q} does not divide evenly across ${n} sizes (${res.sizes.map((s) => s.label).join(', ')}) - kept as one "${r.size}" variant; split per size manually.`);
    unevenDone = true;
    push(colour, asPrinted(r.size), cq, r.cost_price);
  });
  return out;
};

// Inside a group, rows are packed into "lots". A new lot (= separate product)
// starts when a row would put the SAME colour + size at a DIFFERENT cost into
// the current lot. When no size is written at all, two rates are two sizes the
// bill did not name: they stay in one product as variants with a blank Size.
const blankSizeTogether = CONFIG.NO_SIZE_TWO_RATES === 'one_product';
const entryKey = (v) => alnum(v.colour) + '|' + v.sizeKey + (v.sizeKey === '' && blankSizeTogether ? '|' + v.cost : '');
const lots = [];
const attrsFromNameLots = [];
const needSizeLots = [];
for (const g of groups) {
  const first = g.rows[0];
  const nameRow = g.rows.find((r) => r.product_name) || first;
  const gName = g.label || displayName(first.design_no, nameRow.product_name) || 'UNNAMED ITEM';
  const namesInGroup = new Set(g.rows.map((r) => normName(r.product_name)).filter(Boolean));
  if (first.design_no && namesInGroup.size > 1) note(`Design ${first.design_no} is printed with different descriptions (${[...namesInGroup].join(' / ')}) - kept as one product named "${gName}".`);

  const groupLots = [];
  let cur = null;
  for (const r of g.rows) {
    const vs = rowToVariants(r, gName);
    const clash = cur && vs.some((v) => { const e = cur.map.get(entryKey(v)); return e && e.cost !== v.cost; });
    if (!cur || clash) {
      cur = { group: g, map: new Map(), rows: [], repeats: [], description: nameRow.product_name };
      groupLots.push(cur);
    }
    cur.rows.push(r);
    const repeatsOf = new Set();
    for (const v of vs) {
      const k = entryKey(v);
      const e = cur.map.get(k);
      if (e) {
        e.rows.forEach((x) => { if (x !== null && x !== r.sr_no) repeatsOf.add(x); });
        e.qty = round2(e.qty + (v.qty || 0)); e.rows.push(...v.rows);
        if (e.mrp === null || e.mrp === undefined) e.mrp = v.mrp;
        if (!cur.repeats.some((x) => x.row === r)) cur.repeats.push({ row: r, of: repeatsOf });
      } else cur.map.set(k, { ...v });
    }
  }
  groupLots.forEach((lot, i) => {
    lot.name = groupLots.length > 1 ? `${gName}${i === 0 ? '' : ` (${i + 1})`}` : gName;
    lot.notes = [];
    lot.quiet = []; // shown on the product only; summarised once at bill level
    const entries = [...lot.map.values()];
    lot.hasAttrs = lot.rows.some((r) => r.has_attrs) || entries.length > 1;
    for (const rp of lot.repeats) {
      const r = rp.row;
      const what = [r.colours.join('/'), r.size].filter(Boolean).join(' ') || 'same item';
      const earlier = [...rp.of];
      lot.notes.push(`"${lot.name}": ${r.sr_no !== null ? 'row ' + r.sr_no : 'a row'} (${what}, qty ${r.quantity}) repeats ${earlier.length ? 'row ' + earlier.join(', ') : 'an earlier row'} at the same rate - quantities were added together.`);
    }
    // sizes that were never written
    const blanks = entries.filter((e) => e.sizeKey === '');
    lot.needsSize = entries.length > 1 && blanks.length > 0;
    if (lot.needsSize) {
      const rates = [...new Set(blanks.map((e) => e.cost))];
      lot.quiet.push(blanks.length === entries.length
        ? `"${lot.name}" is billed ${entries.length} times${rates.length > 1 ? ` at different rates (${rates.join(', ')})` : ''} with no size written - kept as one product with ${entries.length} variants; enter the Size of each before saving.`
        : `"${lot.name}": ${blanks.length} row(s) have no size written - enter the Size before saving.`);
      needSizeLots.push(lot.name);
    }
    // size grid: the same size should cost the same in every colour of a design
    const bySize = {};
    entries.filter((e) => e.grid).forEach((e) => { (bySize[e.sizeKey] = bySize[e.sizeKey] || new Set()).add(e.cost); });
    const shifted = Object.keys(bySize).filter((k) => bySize[k].size > 1);
    if (shifted.length > 0) lot.notes.push(`"${lot.name}": size ${shifted.map((k) => entries.find((e) => e.sizeKey === k).sizeLabel).join(', ')} has different rates in different colours - a row of the size grid may be read one column off; please check.`);
    if (lot.rows.some((r) => r.attrs_from_name)) attrsFromNameLots.push(lot.name);
    const gstSet = new Set(lot.rows.map((r) => r.gst_percent).filter((v) => v !== null));
    if (gstSet.size > 1) lot.notes.push(`"${lot.name}" has rows with different GST% (${[...gstSet].join(', ')}) - a product uses one GST; please confirm.`);
    lots.push(lot);
  });
  if (groupLots.length > 1) {
    note(`"${gName}" is billed at different rates for the same item/colour/size, so it was kept as ${groupLots.length} separate products (${groupLots.map((l) => `"${l.name}"`).join(', ')}) - rename them if they are different designs.`);
  }
}
Object.values(sizeNotes).forEach((m) => note(m + ' - verify against the goods received.'));
unevenNotes.forEach(note);
colourNotes.forEach(note);
lots.forEach((l) => l.notes.forEach(note));
if (attrsFromNameLots.length > 0) {
  note(`Colour/size were read from inside the item text (the bill has no separate colour/size column) for ${attrsFromNameLots.length} product(s): ${attrsFromNameLots.slice(0, 8).join(', ')}${attrsFromNameLots.length > 8 ? ', ...' : ''} - please verify.`);
}
if (needSizeLots.length > 0) {
  note(`Size is not written for some rows of ${needSizeLots.length} product(s): ${needSizeLots.slice(0, 10).join(', ')}${needSizeLots.length > 10 ? ', ...' : ''}. Rows at different rates were kept as separate variants with a blank Size - enter the Size of each before saving.`);
}
const carriedRows = allRows.filter((r) => r.carried).length;
if (carriedRows > 0) note(`${carriedRows} row(s) use ditto marks or a blank item cell - the design number was copied from the row above.`);

// ---- Variant field schema (matches Add-Product-To-PO v8 + the app UI) ----
const buildVariantsField = (variantValues) => ({
  id: 'variants',
  label: 'Variants',
  type: 'array',
  required: false,
  helperText: 'Optional. Leave empty to save as a single product using the quantity, cost and selling price above. Add one entry per colour/size variant; cost can differ per variant and selling price is driven by Size (two variants of the same size must use the same selling price).',
  value: Array.isArray(variantValues) ? variantValues : [],
  itemSchema: {
    title: 'Variant',
    fields: [
      { id: 'quantity', label: 'Quantity', type: 'number', required: true, value: null },
      { id: 'cost_price', label: 'Cost Price', type: 'number', required: false, value: null, helperText: 'Optional. Falls back to the product-level Cost Price if omitted.' },
      { id: 'selling_price', label: 'Selling Price', type: 'number', required: false, value: null, helperText: 'Optional. Falls back to product-level Selling Price. Driven by Size.' },
      {
        id: 'attributes', label: 'Attributes', type: 'array', required: false, value: [],
        helperText: 'e.g. Colour + Size. Leave empty for a plain product.',
        itemSchema: {
          title: 'Attribute',
          fields: [
            { id: 'name', label: 'Attribute Name', type: 'text', required: true, value: null, helperText: 'e.g. Colour, Size' },
            { id: 'value', label: 'Attribute Value', type: 'text', required: true, value: null, helperText: 'e.g. Blue, M' }
          ]
        }
      }
    ]
  }
});

const products = lots.map((lot, idx) => {
  const entries = [...lot.map.values()];
  const totalQty = round2(sum(entries, (e) => e.qty));
  const firstRow = lot.rows[0];
  const hsnRow = lot.rows.find((r) => r.hsn_code);
  const gstRow = lot.rows.find((r) => r.gst_percent !== null);

  const mrpBySize = {};
  entries.forEach((e) => { if (e.mrp !== null && e.mrp !== undefined && mrpBySize[e.sizeLabel || ''] === undefined) mrpBySize[e.sizeLabel || ''] = e.mrp; });
  const hasMrp = Object.keys(mrpBySize).length > 0;

  const variantValues = lot.hasAttrs ? entries.map((e) => {
    const attributes = [];
    if (e.colour) attributes.push({ name: 'Colour', value: e.colour });
    if (e.sizeLabel) attributes.push({ name: 'Size', value: e.sizeLabel });
    else if (lot.needsSize) attributes.push({ name: 'Size', value: '' }); // to be filled by the user
    return { quantity: e.qty, cost_price: e.cost, selling_price: CONFIG.MRP_AS_SELLING_PRICE && e.mrp ? e.mrp : null, attributes };
  }) : [];

  return {
    title: `Product ${idx + 1}${lot.name ? ' - ' + lot.name : ''}`,
    fields: [
      { id: 'product_name', label: 'Product Name', type: 'text', required: true, value: lot.name || '' },
      { id: 'quantity', label: 'Quantity', type: 'number', required: true, value: totalQty },
      { id: 'cost_price', label: 'Cost Price', type: 'number', required: true, value: entries.length ? entries[0].cost : firstRow.cost_price },
      { id: 'gst_percent', label: 'GST %', type: 'number', required: false, value: gstRow ? gstRow.gst_percent : null },
      { id: 'hsn_code', label: 'HSN Code', type: 'text', required: false, value: hsnRow ? hsnRow.hsn_code : '' },
      { id: 'po_id', label: 'PO ID', type: 'number', required: false, value: null },
      { id: 'product_id', label: 'Product ID', type: 'number', required: false, value: null },
      { id: 'category_name', label: 'Category Name', type: 'text', required: false, value: '' },
      { id: 'selling_price', label: 'Selling Price', type: 'number', required: false, value: CONFIG.MRP_AS_SELLING_PRICE && !lot.hasAttrs && firstRow.mrp ? firstRow.mrp : null },
      { id: 'price_list_default', label: 'Price List Default', type: 'boolean', required: false, value: false },
      { id: 'percentage_profit_percentage', label: 'Profit Percentage', type: 'number', required: false, value: null },
      { id: 'percentage_profit_discount', label: 'Discount', type: 'number', required: false, value: null },
      buildVariantsField(variantValues)
    ],
    // Extra, read-only context. Not part of the form; safe for the app to ignore.
    meta: {
      design_no: firstRow.design_no,
      description: lot.description || firstRow.product_name,
      bill_rows: [...new Set(lot.rows.map((r) => r.sr_no).filter((x) => x !== null))],
      total_quantity: totalQty,
      total_amount: round2(sum(lot.rows, (r) => r.taxable)),
      mrp_by_size: hasMrp ? mrpBySize : null,
      needs_size: !!lot.needsSize,
      needs_review: lot.notes.length + lot.quiet.length > 0,
      notes: [...lot.notes, ...lot.quiet]
    }
  };
});

if (reviewNotes.length > 0) needsReview = true;

const form = {
  form: {
    title: 'Bill Information',
    fields: [
      { id: 'party_bill_number', label: 'Party Bill Number', type: 'text', required: true, value: mergedHeader.party_bill_number || '' },
      { id: 'bill_date', label: 'Bill Date', type: 'date', required: true, value: mergedHeader.bill_date || '' },
      { id: 'party_name', label: 'Party Name', type: 'text', required: true, value: mergedHeader.party_name || '' },
      {
        id: 'mobile_or_gst',
        label: 'Mobile Number or GST Number',
        type: 'text',
        required: true,
        value: mergedHeader.mobile_or_gst || '',
        helperText: 'Enter mobile number OR GST number - either one is accepted.'
      }
    ]
  }
};

const docTypes = [...new Set((companion ? headerPages : keptPages).map((p) => p.doc_type))];

return [{
  json: {
    success: true,
    request_id: prep.request_id,
    shop_id: prep.shop_id,
    received_at: prep.received_at,
    processed_at: new Date().toISOString(),
    pages_processed: keptPages.length,
    schema_version: 'variants_v8',
    parser_version: 'v8',
    needs_review: needsReview,
    review_notes: reviewNotes,
    document: {
      type: companion ? companion.products_from : (docTypes[0] || 'unknown'),
      handwritten: keptPages.some((p) => p.handwritten),
      pages_expected: pagesExpected || null,
      pages_missing: pagesMissing,
      companion
    },
    reconciliation: {
      quantity: recon.quantity,
      amount: recon.amount,
      tax: recon.tax,
      grand_total: recon.grand_total,
      rows_read: allRows.length,
      computed: { total_quantity: sumQty, taxable_total: sumTaxable, other_charges: chargesTotal, tax_total: computedTax, grand_total: computedGrand, total_with_tax: computedGrand /* v7 name, kept for older clients */ },
      printed
    },
    other_charges: allCharges,
    duplicates_removed: {
      identical_files: Number(prep.duplicate_files_dropped || 0),
      pages: duplicatePagesRemoved,
      rows: duplicateRowsRemoved
    },
    ...form,
    products
  }
}];
