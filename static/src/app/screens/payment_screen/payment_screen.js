/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { onMounted } from "@odoo/owl";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup();
        onMounted(() => this._quickUpiAutoPay());
    },

    async _quickUpiAutoPay() {
        const order = this.pos.getOrder();
        const methodId = order?.uiState?.quickPayMethodId;
        if (!methodId) {
            return;
        }
        order.uiState.quickPayMethodId = null;

        const method = this.pos.models["pos.payment.method"].get(methodId);
        if (!method) {
            return;
        }

        const result = order.addPaymentline(method);
        if (!result?.status) {
            return;
        }

        await this.validateOrder(true);
    },
});
