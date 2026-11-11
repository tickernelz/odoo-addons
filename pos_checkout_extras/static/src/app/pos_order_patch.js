import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

patch(PosOrder.prototype, {
    get customerRequiredByConfig() {
        const mode = this.config?.customer_required_mode;
        return Boolean(
            mode &&
                mode !== "off" &&
                !this.getPartner() &&
                !this.isRefund &&
                !this.getHasRefundLines()
        );
    },
});
