import { patch } from "@web/core/utils/patch";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

patch(OrderPaymentValidation.prototype, {
    async _askForCustomerIfRequired() {
        const order = this.order;
        const split = order.payment_ids.filter((payment) => payment.payment_method_id.split_transactions);
        if (split.length && !order.getPartner()) {
            const partnerIds = new Set(
                split.map((payment) => payment.payment_method_id.raw.statement_memo_partner_id)
            );
            const [partnerId] = partnerIds;
            if (partnerIds.size === 1 && partnerId) {
                const partner =
                    this.pos.models["res.partner"].get(partnerId) ||
                    (await this.pos.data.read("res.partner", [partnerId]))[0];
                if (partner) {
                    order.setPartner(partner);
                }
            }
        }
        return super._askForCustomerIfRequired(...arguments);
    },
});
