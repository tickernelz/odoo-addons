import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    async setup() {
        await super.setup(...arguments);
        this.data.connectWebSocket("XENDIT_QRIS", (payload) => {
            for (const method of this.models["pos.payment.method"].getAll()) {
                if (method.use_payment_terminal === "xendit_qris") {
                    method.payment_terminal?.onNotification(payload);
                }
            }
        });
    },
});
