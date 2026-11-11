import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { askForCustomer } from "@pos_checkout_extras/app/customer_required";

patch(PosStore.prototype, {
    async pay() {
        const order = this.getOrder();
        if (order?.customerRequiredByConfig && this.config.customer_required_mode === "order") {
            const selected = await askForCustomer(
                this,
                _t("This point of sale needs a customer on every order. Select a customer to continue to payment.")
            );
            if (!selected) {
                return;
            }
        }
        return super.pay(...arguments);
    },
});
