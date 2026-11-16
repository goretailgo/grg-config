/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { onMounted } from "@odoo/owl";
import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";

patch(ReceiptScreen.prototype, {
    setup() {
        super.setup();
        onMounted(() => this._quickUpiAutoPrint());
    },

    async _quickUpiAutoPrint() {
        try {
            const order = this.currentOrder || this.pos.getOrder();
            if (!order?.uiState?.quickPayPending) {
                return;
            }
            order.uiState.quickPayPending = false;
            await this.pos.printReceipt({ order });
        } catch (e) {
            // Never crash the POS: cashier can still use the Print button
            console.error("[grg_config] Quick UPI auto-print failed:", e);
        }
    },
});
