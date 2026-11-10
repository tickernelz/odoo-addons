import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { maskedCard } from "@pos_bank_card_program/app/utils/bank_card_detection";

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(vals);
        this.bank_card_masked = vals.bank_card_masked || this.bank_card_masked || "";
        this.bank_card_reservation_state =
            vals.bank_card_reservation_state || this.bank_card_reservation_state || "none";
        this.bank_card_usage_ref = vals.bank_card_usage_ref || this.bank_card_usage_ref || "";
        this.bank_card_offline = Boolean(
            vals.bank_card_offline ?? this.bank_card_offline ?? false
        );
    },

    getBankCardDiscountLine() {
        return this.lines.find((line) => line.is_bank_card_discount) || null;
    },

    getBankCardPayment() {
        return this.payment_ids.find((payment) => payment.bank_card_program_id) || null;
    },

    isBankCardExcluded(line, program) {
        const product = line.product_id;
        if (program.excluded_product_ids?.some((excluded) => excluded.id === product?.id)) {
            return true;
        }
        const categId = product?.product_tmpl_id?.categ_id?.id;
        if (categId && program.excluded_categ_ids?.some((excluded) => excluded.id === categId)) {
            return true;
        }
        return false;
    },

    getBankCardBasis(program, cardAmount = 0) {
        if (program.discount_basis === "card_amount") {
            return cardAmount || 0;
        }
        let basis = 0;
        for (const line of this.lines) {
            if (line.is_bank_card_discount || this.isBankCardExcluded(line, program)) {
                continue;
            }
            const prices = line.prices || {};
            const amount =
                program.discount_basis === "total"
                    ? prices.total_included ?? line.price_subtotal_incl ?? 0
                    : prices.total_excluded ?? line.price_subtotal ?? 0;
            if (amount < 0 && program.promo_order === "before_other") {
                continue;
            }
            basis += amount;
        }
        return Math.max(basis, 0);
    },

    async applyBankCardDiscount(pos, payment, applied) {
        const program = applied.program;
        const existing = this.getBankCardDiscountLine();
        if (existing) {
            this.removeOrderline(existing);
        }
        const discountProduct =
            program.discount_product_id ||
            pos.models["product.product"]
                .getAll()
                .find(
                    (product) =>
                        product.name === "Bank Card Discount" ||
                        product.display_name === "Bank Card Discount"
                );
        if (!discountProduct) {
            return false;
        }
        const line = await pos.addLineToOrder(
            {
                product_tmpl_id: discountProduct.product_tmpl_id,
                product_id: discountProduct,
                price_unit: -applied.discount_amount,
                qty: 1,
                tax_ids: [],
                is_bank_card_discount: true,
            },
            this,
            { merge: false, force: true },
            false
        );
        if (!line) {
            return false;
        }
        line.is_bank_card_discount = true;
        payment.card_bin = applied.card_bin;
        payment.card_no = applied.card_last4;
        payment.card_brand = applied.binRange?.network || "";
        payment.card_type = applied.binRange?.card_type || "";
        payment.card_tier = applied.binRange?.tier || "";
        payment.payment_method_authcode = applied.approval_code || "";
        payment.bank_card_program_id = program;
        payment.bank_card_discount = applied.discount_amount;
        payment.bank_card_bank_share = applied.bank_share_amount;
        this.bank_card_program_id = program;
        this.bank_card_masked = maskedCard(applied.card_bin, applied.card_last4);
        this.bank_card_usage_ref = applied.usage_id ? String(applied.usage_id) : "";
        this.bank_card_offline = Boolean(applied.offline);
        this.bank_card_reservation_state = "reserved";
        const othersPaid = this.amountPaid - payment.getAmount();
        payment.setAmount(Math.max(this.totalDue - othersPaid, 0));
        return line;
    },

    clearBankCardDiscount() {
        const line = this.getBankCardDiscountLine();
        if (line) {
            this.removeOrderline(line);
        }
        const payment = this.getBankCardPayment();
        if (payment) {
            payment.card_bin = "";
            payment.card_tier = "";
            payment.bank_card_program_id = false;
            payment.bank_card_discount = 0;
            payment.bank_card_bank_share = 0;
            const othersPaid = this.amountPaid - payment.getAmount();
            payment.setAmount(Math.max(this.totalDue - othersPaid, 0));
        }
        this.bank_card_program_id = false;
        this.bank_card_masked = "";
        this.bank_card_usage_ref = "";
        this.bank_card_offline = false;
        this.bank_card_reservation_state = "released";
    },
});
