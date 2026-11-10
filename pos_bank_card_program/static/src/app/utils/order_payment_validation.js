import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(OrderPaymentValidation.prototype, {
    async isOrderValid(isForceValidate) {
        const order = this.order;
        if (order.getBankCardDiscountLine()) {
            if (!order.bank_card_usage_ref && !order.bank_card_offline) {
                this.pos.dialog.add(AlertDialog, {
                    title: _t("Bank Card Discount"),
                    body: _t(
                        "The bank card discount has no quota reservation. Remove the discount line or apply the programme again."
                    ),
                });
                return false;
            }
            const payment = order.getBankCardPayment();
            const program = order.bank_card_program_id;
            if (
                program?.require_approval_code &&
                !String(payment?.payment_method_authcode || "").trim()
            ) {
                this.pos.dialog.add(AlertDialog, {
                    title: _t("Bank Card Discount"),
                    body: _t("The Approval Code field is required by programme %s.", program.name),
                });
                return false;
            }
        }
        return super.isOrderValid(isForceValidate);
    },
});
