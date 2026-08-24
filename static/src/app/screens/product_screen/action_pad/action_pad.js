/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ActionpadWidget } from "@point_of_sale/app/screens/product_screen/action_pad/action_pad";
import { session } from "@web/session";

patch(ActionpadWidget, {
    template: "grg_config.ActionpadWidget",
});

patch(ActionpadWidget.prototype, {
    setup() {
        super.setup();
        this.dialog = useService("dialog");
    },

    get showQuickUpi() {
        return session.grg_quick_upi_enabled !== false;
    },

    async onQuickUpiPay() {
        const order = this.pos.getOrder();
        if (!order || order.lines.length === 0) {
            return;
        }

        const upiMethod = this.pos.models["pos.payment.method"]
            .getAll()
            .find((pm) => pm.name.toLowerCase().includes("upi"));

        if (!upiMethod) {
            this.dialog.add(AlertDialog, {
                title: "Quick UPI Pay",
                body: "No payment method with 'UPI' in its name found. Create one under Point of Sale > Configuration > Payment Methods, then reload the POS.",
            });
            return;
        }

        order.uiState = order.uiState || {};
        order.uiState.quickPayMethodId = upiMethod.id;
        order.uiState.quickPayPending = true;

        this.pos.mobile_pane = "right";
        this.pos.navigate("PaymentScreen", { orderUuid: order.uuid });
    },
});
