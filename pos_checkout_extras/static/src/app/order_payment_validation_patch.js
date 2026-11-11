import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { askForCustomer } from "@pos_checkout_extras/app/customer_required";

patch(OrderPaymentValidation.prototype, {
    async _askForCustomerIfRequired() {
        if (this.order.customerRequiredByConfig) {
            const selected = await askForCustomer(
                this.pos,
                _t("Select the customer before you validate the payment.")
            );
            if (!selected) {
                return false;
            }
        }
        return super._askForCustomerIfRequired(...arguments);
    },
});
