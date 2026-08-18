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
        const order = this.pos.getOrder();
        if (!order?.uiState?.quickPayPending) {
            return;
        }
        order.uiState.quickPayPending = false;
        await this.pos.printReceipt({ order });
    },
});
