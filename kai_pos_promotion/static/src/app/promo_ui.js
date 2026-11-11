import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { ProductCard } from "@point_of_sale/app/components/product_card/product_card";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { CustomerDisplayPosAdapter } from "@point_of_sale/app/customer_display/customer_display_adapter";

patch(ProductCard.prototype, {
    setup() {
        super.setup(...arguments);
        this.pos = usePos();
    },
    get kaiHappyHour() {
        const product = this.props.product;
        this.pos.kaiTick;
        return this.pos.kaiHappyHourBadge(product?.product_tmpl_id?.id ?? product?.id);
    },
});

patch(OrderSummary.prototype, {
    setup() {
        super.setup(...arguments);
        this.kaiNotification = useService("notification");
    },
    get kaiBanner() {
        return this.currentOrder?.uiState?.kaiBanner;
    },
    clickLine(ev, orderline) {
        if (orderline.kaiIsPromotion) {
            ev.stopPropagation();
            this.kaiNotification.add(_t("Promotion lines are calculated automatically."), { type: "info" });
            return;
        }
        return super.clickLine(...arguments);
    },
});

patch(TicketScreen.prototype, {
    _onUpdateSelectedOrderline() {
        const order = this.getSelectedOrder();
        const lineId = this.getSelectedOrderlineId();
        const line = order?.lines.find((orderline) => orderline.id == lineId);
        if (line?.kaiIsPromotion) {
            this.numberBuffer.reset();
            this.dialog.add(AlertDialog, {
                title: _t("Promotion line"),
                body: _t("Refund the products instead: their share of the promotion is returned automatically."),
            });
            return;
        }
        return super._onUpdateSelectedOrderline(...arguments);
    },
    async onDoRefund() {
        const original = this.getSelectedOrder();
        await super.onDoRefund(...arguments);
        const destination = this.pos.getOrder();
        if (original && destination && destination !== original && destination.isRefund) {
            this.pos.kaiRefundShares(original, destination);
        }
    },
});

patch(CustomerDisplayPosAdapter.prototype, {
    formatOrderData(order) {
        super.formatOrderData(...arguments);
        this.data.kaiBanner = order.uiState?.kaiBanner?.text || "";
    },
});
