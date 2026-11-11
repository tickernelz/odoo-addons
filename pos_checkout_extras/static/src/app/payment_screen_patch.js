import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { TextInputPopup } from "@point_of_sale/app/components/popups/text_input_popup/text_input_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";

export const PAYMENT_REFERENCE_SIZE = 64;

patch(PaymentScreen.prototype, {
    async editPaymentReference() {
        const value = await makeAwaitable(this.dialog, TextInputPopup, {
            title: _t("Payment Reference"),
            placeholder: _t("Transfer number, approval code, voucher..."),
            startingValue: this.currentOrder.payment_reference || "",
            rows: 1,
        });
        if (value !== undefined) {
            this.currentOrder.payment_reference =
                value.trim().slice(0, PAYMENT_REFERENCE_SIZE) || false;
        }
    },
});
