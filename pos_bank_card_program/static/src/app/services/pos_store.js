import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

patch(PosStore.prototype, {
    async deleteOrders(orders, serverIds = [], ignoreChange = false) {
        for (const order of orders || []) {
            if (order?.bank_card_usage_ref) {
                await this.data.silentCall("pos.bank.card.program", "release_quota", [
                    parseInt(order.bank_card_usage_ref),
                    this.config.id,
                ]);
                order.bank_card_usage_ref = "";
                order.bank_card_reservation_state = "released";
            }
        }
        return super.deleteOrders(orders, serverIds, ignoreChange);
    },
});
