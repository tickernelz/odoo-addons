import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { BankCardDialog } from "@pos_bank_card_program/app/components/bank_card_dialog/bank_card_dialog";

patch(PaymentScreen.prototype, {
    async addNewPaymentLine(paymentMethod) {
        const added = await super.addNewPaymentLine(paymentMethod);
        if (!added || !paymentMethod.bank_card_required || this.isRefundOrder) {
            return added;
        }
        const order = this.currentOrder;
        if (order.getBankCardDiscountLine()) {
            return added;
        }
        const payment = this.paymentLines.at(-1);
        if (!payment || payment.payment_method_id.id !== paymentMethod.id) {
            return added;
        }
        const applied = await makeAwaitable(this.dialog, BankCardDialog, {
            order,
            paymentMethod,
            paymentAmount: payment.getAmount(),
        });
        if (!applied) {
            return added;
        }
        const line = await order.applyBankCardDiscount(this.pos, payment, applied);
        if (line) {
            await this.pos.data.synchronizeLocalDataInIndexedDB();
        }
        if (!line && applied.usage_id) {
            await this.pos.data.silentCall("pos.bank.card.program", "release_quota", [
                applied.usage_id,
                this.pos.config.id,
            ]);
            this.dialog.add(AlertDialog, {
                title: _t("Bank Card Discount"),
                body: _t(
                    "The discount product is not available in this point of sale, so the discount was not applied."
                ),
            });
        }
        return added;
    },

    async bankCardCleanupIfPaymentRemoved() {
        const order = this.currentOrder;
        if (!order.getBankCardDiscountLine()) {
            return;
        }
        if (order.bank_card_usage_ref) {
            await this.pos.data.call("pos.bank.card.program", "release_quota", [
                parseInt(order.bank_card_usage_ref),
                this.pos.config.id,
            ]);
        }
        order.clearBankCardDiscount();
        await this.pos.data.synchronizeLocalDataInIndexedDB();
        this.notification.add(_t("The bank card discount was removed with its card payment."), {
            type: "warning",
        });
    },

    deletePaymentLine(uuid) {
        const line = this.paymentLines.find((payment) => payment.uuid === uuid);
        const wasBankCardPayment = Boolean(line?.bank_card_program_id);
        super.deletePaymentLine(uuid);
        if (wasBankCardPayment && !this.paymentLines.includes(line)) {
            this.bankCardCleanupIfPaymentRemoved();
        }
    },

    async removeBankCardDiscount() {
        const order = this.currentOrder;
        if (!order.getBankCardDiscountLine()) {
            return;
        }
        let verified = false;
        for (let attempt = 0; attempt < 3 && !verified; attempt++) {
            const pin = await makeAwaitable(this.dialog, NumberPopup, {
                title: _t("Store Manager PIN"),
                subtitle: _t("Enter the PIN to remove the bank card discount."),
                formatDisplayedValue: (value) => "•".repeat(String(value || "").length),
            });
            if (pin === undefined || pin === null || pin === "") {
                return;
            }
            verified = await this.pos.data.call("pos.bank.card.program", "verify_manager_pin", [
                String(pin),
            ]);
            if (!verified) {
                this.notification.add(_t("The PIN is not correct."), { type: "warning" });
            }
        }
        if (!verified) {
            return;
        }
        if (order.bank_card_usage_ref) {
            await this.pos.data.call("pos.bank.card.program", "release_quota", [
                parseInt(order.bank_card_usage_ref),
                this.pos.config.id,
            ]);
        }
        order.clearBankCardDiscount();
        await this.pos.data.synchronizeLocalDataInIndexedDB();
        this.notification.add(_t("The bank card discount was removed."), { type: "success" });
    },
});
