import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as TicketScreen from "@point_of_sale/../tests/pos/tours/utils/ticket_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as Order from "@point_of_sale/../tests/generic_helpers/order_widget_util";
import * as OfflineUtil from "@point_of_sale/../tests/generic_helpers/offline_util";
import { inLeftSide } from "@point_of_sale/../tests/pos/tours/utils/common";
import { registry } from "@web/core/registry";

const { DateTime } = luxon;

function banner(text) {
    return inLeftSide([{ content: `banner says ${text}`, trigger: `.o_kai_promo_banner:contains(${text})` }]);
}

function addMore(name, quantity) {
    return [
        ProductScreen.clickDisplayedProduct(name),
        inLeftSide(Order.hasLine({ productName: name, quantity })),
    ].flat();
}

function promoLine(name, price) {
    return inLeftSide(Order.hasLine({ productName: name, price }));
}

function noPromoLine(name) {
    return inLeftSide(Order.doesNotHaveLine({ productName: name }));
}

function at(isoWithZone) {
    return {
        content: `clock is ${isoWithZone}`,
        trigger: "body",
        run: () => {
            const instant = DateTime.fromISO(isoWithZone, { setZone: true });
            DateTime.now = () => instant;
            posmodel.kaiOnTick();
        },
    };
}

export function promotionLineStaysUnselected(name) {
    return inLeftSide([
        {
            content: "click the promotion line",
            trigger: `.order-container .orderline:contains(${name})`,
            run: "click",
        },
        {
            content: "the cashier is told the line is automatic",
            trigger: ".o_notification:contains(Promotion lines are calculated automatically)",
        },
        {
            content: "the promotion line cannot be selected",
            trigger: ".order-container",
            run: () => {
                const order = posmodel.getOrder();
                const line = order.lines.find((orderline) => orderline.kaiIsPromotion);
                posmodel.selectOrderLine(order, line);
                if (order.getSelectedOrderline() === line) {
                    throw new Error("the promotion line was selected");
                }
            },
        },
    ]);
}

function cashAndValidate() {
    return [
        ProductScreen.clickPayButton(),
        PaymentScreen.clickPaymentMethod("Cash"),
        PaymentScreen.clickValidate(),
        ReceiptScreen.isShown(),
    ];
}

registry.category("web_tour.tours").add("kai_pos_promotion_tier_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline("Promo Oil", "1"),
            noPromoLine("Seminyak Tier"),
            banner("115,000 more for 10% off"),
            ProductScreen.addOrderline("Promo Wash", "1"),
            ProductScreen.addOrderline("Promo Balm", "1"),
            ProductScreen.addOrderline("Promo Sachet", "1"),
            promoLine("Seminyak Tier – 10%", "-32,000.00"),
            banner("180,000 more for 15% off"),
            ProductScreen.clickNumpad("⌫"),
            ProductScreen.clickNumpad("⌫"),
            promoLine("Seminyak Tier – 10%", "-31,500.00"),
            ProductScreen.totalAmountIs("283,500.00"),
            promotionLineStaysUnselected("Seminyak Tier"),
            ...cashAndValidate(),
            {
                content: "the receipt names the promotion",
                trigger: ".pos-receipt:contains(Seminyak Tier – 10%)",
            },
            ReceiptScreen.clickNextOrder(),
            ProductScreen.addOrderline("Promo Gift Box", "1"),
            promoLine("Seminyak Tier – 15%", "-78,000.00"),
            ProductScreen.totalAmountIs("442,000.00"),
            addMore("Promo Gift Box", "2"),
            promoLine("Seminyak Tier – 20%", "-208,000.00"),
            banner("Congratulations! The customer gets Seminyak Tier – 20%"),
            ProductScreen.addOrderline("Promo Ticket", "1"),
            promoLine("Seminyak Tier – 20%", "-208,000.00"),
            ProductScreen.totalAmountIs("1,082,000.00"),
            ...cashAndValidate(),
            ReceiptScreen.clickNextOrder(),
            ...ProductScreen.clickRefund(),
            TicketScreen.selectFilter("Paid"),
            TicketScreen.selectOrder("002"),
            inLeftSide(Order.hasLine({ productName: "Promo Gift Box", withClass: ".selected" })),
            ProductScreen.clickNumpad("1"),
            TicketScreen.toRefundTextContains("To Refund: 1"),
            TicketScreen.confirmRefund(),
            PaymentScreen.isShown(),
            PaymentScreen.totalIs("-416,000.00"),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),
        ].flat(),
});

registry.category("web_tour.tours").add("kai_pos_promotion_group_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline("Promo Gift Box", "1"),
            promoLine("Seminyak Tier – 15%", "-78,000.00"),
            noPromoLine("National Tier"),
            addMore("Promo Gift Box", "2"),
            promoLine("25% on your order", "-260,000.00"),
            noPromoLine("Seminyak Tier"),
            noPromoLine("National Tier"),
            ProductScreen.totalAmountIs("780,000.00"),
            ProductScreen.clickNumpad("⌫"),
            ProductScreen.clickNumpad("1"),
            promoLine("Seminyak Tier – 15%", "-78,000.00"),
            noPromoLine("25% on your order"),
            ProductScreen.totalAmountIs("442,000.00"),
            ...cashAndValidate(),
            ReceiptScreen.clickNextOrder(),
        ].flat(),
});

registry.category("web_tour.tours").add("kai_pos_promotion_happy_hour_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            at("2026-11-11T13:59:59+08:00"),
            {
                content: "no badge before the window",
                trigger: ".product-list:not(:has(.o_kai_happy_hour))",
            },
            ProductScreen.addOrderline("Promo Coffee", "1"),
            ProductScreen.totalAmountIs("120,000.00"),
            at("2026-11-11T14:00:00+08:00"),
            {
                content: "badge on the coffee card",
                trigger: "article.product:contains(Promo Coffee) .o_kai_happy_hour:contains(Happy Hour)",
            },
            addMore("Promo Coffee", "2"),
            ProductScreen.totalAmountIs("204,000.00"),
            at("2026-11-11T16:00:00+08:00"),
            {
                content: "the cashier is told the happy hour ended",
                trigger: ".o_notification:contains(Coffee Happy Hour ended at 16:00)",
            },
            ProductScreen.totalAmountIs("240,000.00"),
            ProductScreen.clickPayButton(),
            PaymentScreen.totalIs("240,000.00"),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),
        ].flat(),
});

registry.category("web_tour.tours").add("kai_pos_promotion_offline_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            OfflineUtil.setOfflineMode(),
            ProductScreen.addOrderline("Promo Gift Box", "2"),
            promoLine("Seminyak Tier – 20%", "-208,000.00"),
            ProductScreen.totalAmountIs("832,000.00"),
            ...cashAndValidate(),
            Dialog.confirm("Continue with limited functionality"),
            ReceiptScreen.clickNextOrder(),
            OfflineUtil.setOnlineMode(),
            ProductScreen.addOrderline("Promo Oil", "1"),
            ...cashAndValidate(),
            ReceiptScreen.clickNextOrder(),
        ].flat(),
});
