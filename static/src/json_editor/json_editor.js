/** @odoo-module **/
import { Component, onWillUpdateProps, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const TYPES = ["string", "number", "boolean", "null", "object", "array"];

function typeOf(v) {
    if (v === null || v === undefined) {
        return "null";
    }
    if (Array.isArray(v)) {
        return "array";
    }
    return typeof v === "object" ? "object" : typeof v;
}

function emptyOf(t) {
    return { string: "", number: 0, boolean: false, null: null, object: {}, array: [] }[t];
}

function convert(v, t) {
    const from = typeOf(v);
    if (from === t) {
        return v;
    }
    if (t === "string") {
        return from === "null" || from === "object" || from === "array" ? "" : String(v);
    }
    if (t === "number") {
        const n = Number(v);
        return Number.isFinite(n) ? n : 0;
    }
    if (t === "boolean") {
        return v === "true" || (from !== "string" && !!v);
    }
    return emptyOf(t);
}

export class GrgJsonNode extends Component {
    static template = "grg_config.JsonNode";
    static props = {
        value: { optional: true },
        depth: Number,
        readonly: Boolean,
        onChange: Function,
        // set by the parent when this node is a nested object/array
        keyName: { optional: true },
        isArrayItem: { type: Boolean, optional: true },
        onRename: { type: Function, optional: true },
        onRemove: { type: Function, optional: true },
        onMove: { type: Function, optional: true },
    };

    setup() {
        this.TYPES = TYPES;
        this.state = useState({ open: this.props.depth < 3, newKey: "", newType: "string", keyError: "" });
    }

    get type() {
        return typeOf(this.props.value);
    }
    get isContainer() {
        return this.type === "object" || this.type === "array";
    }
    get isArray() {
        return this.type === "array";
    }
    isContainerValue(v) {
        const t = typeOf(v);
        return t === "object" || t === "array";
    }
    get entries() {
        const v = this.props.value;
        return this.isArray ? v.map((x, i) => [i, x]) : Object.entries(v);
    }
    get summary() {
        const v = this.props.value;
        if (this.type === "object") {
            const name = v.label || v.title || v.name || v.id;
            return typeof name === "string" ? name : "";
        }
        return "";
    }
    get countLabel() {
        const n = this.entries.length;
        return this.isArray ? `${n} item${n === 1 ? "" : "s"}` : `${n} key${n === 1 ? "" : "s"}`;
    }
    toggle() {
        this.state.open = !this.state.open;
    }
    setChild(k, v) {
        const copy = this.isArray ? [...this.props.value] : { ...this.props.value };
        copy[k] = v;
        this.props.onChange(copy);
    }
    removeChild(k) {
        if (this.isArray) {
            const copy = [...this.props.value];
            copy.splice(k, 1);
            this.props.onChange(copy);
        } else {
            const copy = { ...this.props.value };
            delete copy[k];
            this.props.onChange(copy);
        }
    }
    renameChild(oldKey, ev) {
        const newKey = ev.target.value.trim();
        if (!newKey || newKey === oldKey || newKey in this.props.value) {
            ev.target.value = oldKey;
            return;
        }
        const copy = {};
        for (const [k, v] of Object.entries(this.props.value)) {
            copy[k === oldKey ? newKey : k] = v;
        }
        this.props.onChange(copy);
    }
    moveChild(i, dir) {
        const j = i + dir;
        const copy = [...this.props.value];
        if (j < 0 || j >= copy.length) {
            return;
        }
        [copy[i], copy[j]] = [copy[j], copy[i]];
        this.props.onChange(copy);
    }
    addChild() {
        if (this.isArray) {
            let item = emptyOf(this.state.newType);
            // new array item of objects: copy the shape of the first item (e.g. a new form field)
            const first = this.props.value[0];
            if (this.state.newType === "object" && typeOf(first) === "object") {
                item = Object.fromEntries(Object.entries(first).map(([k, v]) => [k, emptyOf(typeOf(v))]));
            }
            this.props.onChange([...this.props.value, item]);
            return;
        }
        const key = this.state.newKey.trim();
        if (!key) {
            this.state.keyError = _t("Enter a key name");
            return;
        }
        if (key in this.props.value) {
            this.state.keyError = _t("Key already exists");
            return;
        }
        this.state.keyError = "";
        this.state.newKey = "";
        this.props.onChange({ ...this.props.value, [key]: emptyOf(this.state.newType) });
    }
    onText(ev) {
        this.props.onChange(ev.target.value);
    }
    onNumber(ev) {
        const n = Number(ev.target.value);
        if (ev.target.value !== "" && Number.isFinite(n)) {
            this.props.onChange(n);
        } else {
            ev.target.value = this.props.value;
        }
    }
    changeType(ev) {
        this.props.onChange(convert(this.props.value, ev.target.value));
    }
}
GrgJsonNode.components = { GrgJsonNode };

export class GrgJsonEditor extends Component {
    static template = "grg_config.JsonEditor";
    static components = { GrgJsonNode };
    static props = {
        ...standardFieldProps,
        preview: { type: String, optional: true },
    };

    setup() {
        this.state = useState({ mode: "visual", codeText: this.pretty(this.rawValue), error: "", copied: false });
        onWillUpdateProps((next) => {
            const raw = next.record.data[next.name] || "";
            if (!this.state.error && raw !== this.lastPushed) {
                this.state.codeText = this.pretty(raw);
            }
        });
    }

    get rawValue() {
        return this.props.record.data[this.props.name] || "";
    }
    get data() {
        try {
            const d = JSON.parse(this.rawValue || "{}");
            return d && typeof d === "object" && !Array.isArray(d) ? d : {};
        } catch {
            return {};
        }
    }
    pretty(raw) {
        try {
            return JSON.stringify(JSON.parse(raw || "{}"), null, 2);
        } catch {
            return raw;
        }
    }
    push(obj) {
        const text = JSON.stringify(obj, null, 2);
        this.lastPushed = text;
        this.props.record.update({ [this.props.name]: text });
    }
    setMode(mode) {
        if (mode === this.state.mode) {
            return;
        }
        if (this.state.error) {
            // leaving code mode with broken JSON: drop the unsaved text
            this.state.error = "";
        }
        this.state.codeText = this.pretty(this.rawValue);
        this.state.mode = mode;
    }
    onDataChange(obj) {
        this.push(obj);
        this.state.codeText = JSON.stringify(obj, null, 2);
    }
    onCodeInput(ev) {
        this.state.codeText = ev.target.value;
        try {
            const obj = JSON.parse(ev.target.value);
            if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
                throw new Error(_t("Top level must be a JSON object { ... }"));
            }
            this.state.error = "";
            this.push(obj);
        } catch (e) {
            this.state.error = e.message;
        }
    }
    formatCode() {
        if (!this.state.error) {
            this.state.codeText = this.pretty(this.state.codeText);
        }
    }
    async copy() {
        try {
            await navigator.clipboard.writeText(JSON.stringify(this.data, null, 2));
            this.state.copied = true;
            setTimeout(() => (this.state.copied = false), 1500);
        } catch {
            // clipboard not available (http): ignore
        }
    }
    get previewForm() {
        const form = this.data.form;
        const list = (x) => (Array.isArray(x) ? x.filter((f) => f && typeof f === "object") : []);
        const section = (sec) => {
            if (!sec || typeof sec !== "object" || Array.isArray(sec)) {
                return null;
            }
            return {
                title: sec.title || "",
                repeatable: !!sec.repeatable,
                attributes: list(sec.attributes),
                fields: list(sec.fields),
                variants: section(sec.variants),
            };
        };
        if (!form || typeof form !== "object") {
            return { title: "", fields: [], products: null };
        }
        return { title: form.title || "", fields: list(form.fields), products: section(form.products) };
    }
    isSelected(o, v) {
        return `${o}` === `${v}`;
    }
    fieldOptions(f) {
        return Array.isArray(f.options) ? f.options.map((o) => (o && typeof o === "object" ? o.label ?? o.value : o)) : [];
    }
    inputType(t) {
        return ["date", "number", "email", "tel", "datetime-local", "time"].includes(t) ? t : "text";
    }
}

export const grgJsonEditor = {
    component: GrgJsonEditor,
    displayName: _t("JSON Editor"),
    supportedTypes: ["text"],
    extractProps: ({ options }) => ({ preview: options.preview || "" }),
};

registry.category("fields").add("grg_json_editor", grgJsonEditor);
