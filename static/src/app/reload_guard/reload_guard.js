/** @odoo-module */

import { registry } from "@web/core/registry";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const PING_URL = "/web/webclient/version_info";
const PING_EVERY_MS = 10000;

const grgReloadGuardService = {
    dependencies: ["dialog"],
    start(env, { dialog }) {
        const state = { online: true, allowUnload: false, dialogOpen: false };

        async function ping() {
            if (!navigator.onLine) {
                state.online = false;
                return false;
            }
            try {
                const res = await fetch(PING_URL, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: {} }),
                    cache: "no-store",
                    signal: AbortSignal.timeout(4000),
                });
                state.online = res.ok;
            } catch {
                state.online = false;
            }
            return state.online;
        }
        ping();
        setInterval(ping, PING_EVERY_MS);
        window.addEventListener("online", ping);
        window.addEventListener("offline", () => (state.online = false));
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) ping();
        });

        function pendingCount() {
            try {
                const p = env.services.pos?.getPendingOrder?.();
                if (!p) return 0;
                let n = 0;
                for (const v of Object.values(p)) {
                    n += v?.length ?? v?.size ?? 0;
                }
                return n;
            } catch {
                return 0;
            }
        }

        function showOfflineWarning() {
            if (state.dialogOpen) return;
            state.dialogOpen = true;
            try {
                const n = pendingCount();
                const pendingLine = n
                    ? `\n\n${n} order(s) not yet synced are saved on this device and will sync when the server is back.`
                    : "";
                dialog.add(
                    ConfirmationDialog,
                    {
                        title: "Warning: Server Offline",
                        body:
                            "The server is NOT reachable.\n\n" +
                            "If you reload (or hard reload) now, the POS will close and will NOT open again until the server is back online.\n\n" +
                            "Press Cancel to keep selling offline." +
                            pendingLine,
                        confirmLabel: "Reload",
                        cancelLabel: "Cancel",
                        confirmClass: "btn-danger",
                        confirm: () => {
                            state.allowUnload = true;
                            window.location.reload();
                        },
                        cancel: () => {},
                    },
                    { onClose: () => (state.dialogOpen = false) }
                );
            } catch (e) {
                state.dialogOpen = false;
                console.error("[grg_config] reload guard error:", e);
            }
        }

        // Keyboard reload: intercept ONLY when offline; online = original behaviour
        window.addEventListener(
            "keydown",
            (ev) => {
                if (state.online) return;
                const k = (ev.key || "").toLowerCase();
                const isReload = ev.key === "F5" || ((ev.ctrlKey || ev.metaKey) && k === "r");
                if (!isReload) return;
                ev.preventDefault();
                ev.stopPropagation();
                ev.stopImmediatePropagation();
                showOfflineWarning();
            },
            true
        );

        // Browser reload button / tab close: native prompt ONLY when offline
        window.addEventListener("beforeunload", (ev) => {
            if (state.allowUnload || state.online) return;
            ev.preventDefault();
            ev.returnValue = "";
        });

        return { isOnline: () => state.online };
    },
};

registry.category("services").add("grg_reload_guard", grgReloadGuardService);
