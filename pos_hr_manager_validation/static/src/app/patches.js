import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { ClosePosPopup } from "@point_of_sale/app/components/popups/closing_popup/closing_popup";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { isGuarded, requestManagerValidation } from "@pos_hr_manager_validation/app/manager_validation";

patch(PosStore.prototype, {
    async setup() {
        await super.setup(...arguments);
        this.mvPresence = null;
        if (typeof this.applyDiscount === "function") {
            const applyDiscount = this.applyDiscount;
            this.applyDiscount = async (percent, order = this.getOrder()) => {
                if (
                    !order?.isRefund &&
                    isGuarded(this, "discount") &&
                    !(await requestManagerValidation(this, "discount", {
                        subtitle: _t("Global discount of %s", percent + "%"),
                        reference: order?.uuid,
                    }))
                ) {
                    return;
                }
                return applyDiscount.call(this, percent, order);
            };
        }
    },
    setCashier(employee) {
        super.setCashier(...arguments);
        if (this.mvPresence && this.mvPresence !== employee?.id) {
            this.mvPresence = null;
        }
    },
    restrictLineDiscountChange() {
        return isGuarded(this, "discount") || super.restrictLineDiscountChange(...arguments);
    },
    restrictLinePriceChange() {
        return isGuarded(this, "price") || super.restrictLinePriceChange(...arguments);
    },
    disallowLineQuantityChange() {
        return isGuarded(this, "decrease_qty") || super.disallowLineQuantityChange(...arguments);
    },
    async setDiscountFromUI(line, value) {
        if (
            isGuarded(this, "discount") &&
            parseFloat(value) > line.getDiscount() &&
            !(await requestManagerValidation(this, "discount", {
                subtitle: _t("%(discount)s on %(product)s", {
                    discount: value + "%",
                    product: line.getFullProductName(),
                }),
            }))
        ) {
            return;
        }
        return super.setDiscountFromUI(...arguments);
    },
    async onDeleteOrder(order) {
        if (
            order.getOrderlines().length > 0 &&
            isGuarded(this, "delete_order") &&
            !(await requestManagerValidation(this, "delete_order", {
                subtitle: order.pos_reference || order.name || "",
                reference: order.uuid,
            }))
        ) {
            return false;
        }
        return super.onDeleteOrder(...arguments);
    },
    async cashMove() {
        if (
            !(await requestManagerValidation(this, "cash_move", {
                reference: "pos.session," + this.session.id,
            }))
        ) {
            return;
        }
        return super.cashMove(...arguments);
    },
    async editProduct(product) {
        if (!product && !(await requestManagerValidation(this, "create_product"))) {
            return;
        }
        return super.editProduct(...arguments);
    },
});

patch(OrderSummary.prototype, {
    async updateSelectedOrderline({ buffer, key }) {
        const line = this.currentOrder.getSelectedOrderline();
        if (
            line &&
            buffer === null &&
            this.pos.numpadMode === "quantity" &&
            isGuarded(this.pos, "delete_line")
        ) {
            this.numberBuffer.reset();
            if (
                !(await requestManagerValidation(this.pos, "delete_line", {
                    subtitle: line.getFullProductName(),
                }))
            ) {
                return;
            }
            this._setValue("remove");
            this.pos.numpadMode = "quantity";
            return;
        }
        return super.updateSelectedOrderline(...arguments);
    },
    async setLinePrice(line, price) {
        if (
            isGuarded(this.pos, "price") &&
            !(await requestManagerValidation(this.pos, "price", {
                subtitle: _t("%(product)s: %(price)s", {
                    product: line.getFullProductName(),
                    price: this.env.utils.formatCurrency(parseFloat(price) || 0),
                }),
            }))
        ) {
            return;
        }
        return super.setLinePrice(...arguments);
    },
    async updateQuantityNumber(newQuantity) {
        if (newQuantity !== null && isGuarded(this.pos, "decrease_qty")) {
            let line = this.currentOrder.getSelectedOrderline();
            if (line?.combo_parent_id) {
                line = line.combo_parent_id;
            }
            if (
                line &&
                newQuantity < line.getQuantity() &&
                !(await requestManagerValidation(this.pos, "decrease_qty", {
                    subtitle: _t("%(product)s: %(from)s to %(to)s", {
                        product: line.getFullProductName(),
                        from: line.getQuantity(),
                        to: newQuantity,
                    }),
                }))
            ) {
                return false;
            }
        }
        return super.updateQuantityNumber(...arguments);
    },
});

patch(OrderPaymentValidation.prototype, {
    async isOrderValid(isForceValidate) {
        if (!(await super.isOrderValid(...arguments))) {
            return false;
        }
        if (!isGuarded(this.pos, "payment")) {
            return true;
        }
        if ((await this._askForCustomerIfRequired()) === false) {
            return false;
        }
        const total = String(this.order.priceIncl);
        if (this.order.uiState.mvPaymentGranted === total) {
            return true;
        }
        const granted = await requestManagerValidation(this.pos, "payment", {
            subtitle: this.pos.env.utils.formatCurrency(this.order.priceIncl),
            reference: this.order.uuid,
        });
        if (granted) {
            this.order.uiState.mvPaymentGranted = total;
        }
        return granted;
    },
});

patch(ClosePosPopup.prototype, {
    async confirm() {
        if (
            !(await requestManagerValidation(this.pos, "close_session", {
                reference: "pos.session," + this.pos.session.id,
            }))
        ) {
            return;
        }
        return super.confirm(...arguments);
    },
});

patch(ControlButtons.prototype, {
    async clickFiscalPosition() {
        if (!(await requestManagerValidation(this.pos, "fiscal_position"))) {
            return;
        }
        return super.clickFiscalPosition(...arguments);
    },
    async clickPricelist() {
        if (!(await requestManagerValidation(this.pos, "pricelist"))) {
            return;
        }
        return super.clickPricelist(...arguments);
    },
});

patch(TicketScreen.prototype, {
    async onDoRefund() {
        const order = this.getSelectedOrder();
        if (
            order &&
            isGuarded(this.pos, "refund") &&
            !(await requestManagerValidation(this.pos, "refund", {
                subtitle: order.pos_reference || order.name || "",
                reference: order.uuid,
            }))
        ) {
            return;
        }
        return super.onDoRefund(...arguments);
    },
});
