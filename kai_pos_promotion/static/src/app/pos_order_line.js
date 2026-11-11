import { patch } from "@web/core/utils/patch";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";

patch(PosOrderline.prototype, {
    get kaiIsPromotion() {
        return Boolean(this.kai_promo_id || this.raw?.kai_promo_id || this.kai_promo_refund_of_id);
    },
    get kaiIsManagedPromotion() {
        return this.kaiIsPromotion && !this.kai_promo_refund_of_id && !this.raw?.kai_promo_refund_of_id;
    },
    setFullProductName() {
        if (this.kaiIsPromotion && this.full_product_name) {
            return;
        }
        return super.setFullProductName(...arguments);
    },
    get orderDisplayProductName() {
        if (this.kaiIsPromotion && this.full_product_name) {
            return { name: this.full_product_name, attributeString: "" };
        }
        return super.orderDisplayProductName;
    },
    getDisplayClasses() {
        const classes = super.getDisplayClasses(...arguments);
        if (this.kaiIsPromotion) {
            classes["fst-italic"] = true;
        }
        return classes;
    },
});
