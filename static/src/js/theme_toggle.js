/** @odoo-module */

import { session } from "@web/session";

function applyGrgTheme() {
    if (session.grg_theme_enabled !== false) {
        document.body.classList.add("grg-theme-active");
    }
}

if (document.body) {
    applyGrgTheme();
} else {
    document.addEventListener("DOMContentLoaded", applyGrgTheme);
}
