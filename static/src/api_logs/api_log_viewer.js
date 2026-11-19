/** @odoo-module **/
import { Component, markup, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, serializeDateTime } from "@web/core/l10n/dates";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const { DateTime } = luxon;
const FIELDS = [
    "request_time", "method", "path", "query_string", "status_code", "status_class", "duration_ms",
    "user_id", "ip", "user_agent", "request_headers", "request_body", "request_size",
    "response_content_type", "response_body", "response_size", "error_message",
];
const RANGES = { "15m": { minutes: 15 }, "1h": { hours: 1 }, "24h": { hours: 24 }, "7d": { days: 7 }, all: null };

function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export class GrgApiLogViewer extends Component {
    static template = "grg_config.ApiLogViewer";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.RANGES = Object.keys(RANGES);
        this.state = useState({
            logs: [], total: 0, errors4: 0, errors5: 0, loading: false,
            page: 0, pageSize: 50,
            status: "all", method: "all", endpoint: "", range: "24h", search: "",
            endpoints: [], selected: null, detailTab: "response", auto: false, lastRefresh: null,
        });
        this.searchTimer = null;
        this.autoTimer = null;
        onWillStart(() => this.reload());
        onWillUnmount(() => {
            clearInterval(this.autoTimer);
            clearTimeout(this.searchTimer);
        });
    }

    get companyId() {
        return this.props.record.resId;
    }

    domain(extra = []) {
        const s = this.state;
        const d = ["|", ["company_id", "=", this.companyId], ["company_id", "=", false]];
        if (s.status !== "all") {
            d.push(["status_class", "=", s.status]);
        }
        if (s.method !== "all") {
            d.push(["method", "=", s.method]);
        }
        if (s.endpoint) {
            d.push(["path", "=", s.endpoint]);
        }
        if (RANGES[s.range]) {
            d.push(["request_time", ">=", serializeDateTime(DateTime.now().minus(RANGES[s.range]))]);
        }
        const q = s.search.trim();
        if (q) {
            d.push("|", "|", "|", "|", ["path", "ilike", q], ["query_string", "ilike", q],
                ["request_body", "ilike", q], ["response_body", "ilike", q], ["user_id.name", "ilike", q]);
        }
        return d.concat(extra);
    }

    async reload(keepSelection = true) {
        if (!this.companyId) {
            return;
        }
        this.state.loading = true;
        try {
            const [logs, total, e4, e5, endpoints] = await Promise.all([
                this.orm.searchRead("grg.api.log", this.domain(), FIELDS, {
                    limit: this.state.pageSize, offset: this.state.page * this.state.pageSize, order: "id desc",
                }),
                this.orm.searchCount("grg.api.log", this.domain()),
                this.orm.searchCount("grg.api.log", this.domain([["status_class", "=", "4xx"]])),
                this.orm.searchCount("grg.api.log", this.domain([["status_class", "=", "5xx"]])),
                this.orm.call("grg.api.log", "grg_log_endpoints", [this.companyId]),
            ]);
            Object.assign(this.state, { logs, total, errors4: e4, errors5: e5, endpoints, lastRefresh: DateTime.now() });
            if (keepSelection && this.state.selected) {
                this.state.selected = logs.find((l) => l.id === this.state.selected.id) || this.state.selected;
            }
        } finally {
            this.state.loading = false;
        }
    }

    setFilter(key, value) {
        this.state[key] = value;
        this.state.page = 0;
        this.reload();
    }
    onSearchInput(ev) {
        this.state.search = ev.target.value;
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => {
            this.state.page = 0;
            this.reload();
        }, 350);
    }
    toggleAuto() {
        this.state.auto = !this.state.auto;
        clearInterval(this.autoTimer);
        if (this.state.auto) {
            this.autoTimer = setInterval(() => this.reload(), 5000);
        }
    }
    goPage(delta) {
        const max = Math.max(0, Math.ceil(this.state.total / this.state.pageSize) - 1);
        this.state.page = Math.min(max, Math.max(0, this.state.page + delta));
        this.reload(false);
    }
    setTab(tab) {
        this.state.detailTab = tab;
    }
    get pagerEnd() {
        return Math.min((this.state.page + 1) * this.state.pageSize, this.state.total);
    }
    select(log) {
        this.state.selected = this.state.selected && this.state.selected.id === log.id ? null : log;
    }
    async clearLogs() {
        if (!window.confirm(_t("Delete all API logs of this company?"))) {
            return;
        }
        await this.orm.call("grg.api.log", "grg_log_clear", [this.companyId]);
        this.state.selected = null;
        this.notification.add(_t("API logs cleared"), { type: "success" });
        this.reload(false);
    }

    // ---------- formatting
    fmtTime(v) {
        return v ? deserializeDateTime(v).toFormat("dd MMM, HH:mm:ss") : "";
    }
    fmtAge(v) {
        return v ? deserializeDateTime(v).toRelative() : "";
    }
    fmtSize(n) {
        if (!n) {
            return "";
        }
        return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
    }
    fmtMs(ms) {
        return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
    }
    statusClass(code) {
        return code >= 500 ? "o_grg_al_s5" : code >= 400 ? "o_grg_al_s4" : code >= 300 ? "o_grg_al_s3" : "o_grg_al_s2";
    }
    durationClass(ms) {
        return ms >= 3000 ? "text-danger fw-bold" : ms >= 800 ? "text-warning fw-semibold" : "text-muted";
    }
    pretty(text) {
        if (!text) {
            return "";
        }
        try {
            return JSON.stringify(JSON.parse(text), null, 2);
        } catch {
            return text;
        }
    }
    highlight(text) {
        const pretty = this.pretty(text);
        if (!pretty) {
            return "";
        }
        const html = escapeHtml(pretty).replace(
            /("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(\.\d+)?([eE][+-]?\d+)?)/g,
            (m) => {
                let cls = "o_grg_al_num";
                if (/^"/.test(m)) {
                    cls = /:$/.test(m) ? "o_grg_al_key" : "o_grg_al_str";
                } else if (/true|false/.test(m)) {
                    cls = "o_grg_al_bool";
                } else if (/null/.test(m)) {
                    cls = "o_grg_al_null";
                }
                return `<span class="${cls}">${m}</span>`;
            }
        );
        return markup(html);
    }
    curl(log) {
        let headers = {};
        try {
            headers = JSON.parse(log.request_headers || "{}");
        } catch {
            headers = {};
        }
        const skip = ["host", "content-length", "connection", "accept-encoding", "user-agent", "postman-token"];
        const url = `${window.location.origin}${log.path}${log.query_string ? "?" + log.query_string : ""}`;
        const parts = [`curl --location --request ${log.method} '${url}'`];
        for (const [k, v] of Object.entries(headers)) {
            if (skip.includes(k.toLowerCase())) {
                continue;
            }
            const val = k.toLowerCase() === "authorization" ? "Bearer <API_KEY>" : v;
            if (k.toLowerCase() === "content-type" && String(v).startsWith("multipart/")) {
                continue;
            }
            parts.push(`--header '${k}: ${String(val).replace(/'/g, "'\\''")}'`);
        }
        let body = null;
        try {
            body = JSON.parse(log.request_body || "null");
        } catch {
            body = null;
        }
        if (body && body.files !== undefined && body.form !== undefined) {
            for (const [k, v] of Object.entries(body.form || {})) {
                parts.push(`--form '${k}=${v}'`);
            }
            for (const f of body.files || []) {
                parts.push(`--form '${f.field}=@"/path/to/${f.filename || "file"}"'`);
            }
        } else if (log.request_body) {
            parts.push(`--data '${log.request_body.replace(/'/g, "'\\''")}'`);
        }
        return parts.join(" \\\n  ");
    }
    async copy(text, label) {
        try {
            await navigator.clipboard.writeText(text || "");
            this.notification.add(_t("%s copied", label), { type: "info" });
        } catch {
            this.notification.add(_t("Clipboard not available (needs HTTPS)"), { type: "warning" });
        }
    }
}


export const grgApiLogViewer = {
    component: GrgApiLogViewer,
    displayName: _t("API Log Viewer"),
    supportedTypes: ["integer"],
};
registry.category("fields").add("grg_api_log_viewer", grgApiLogViewer);
