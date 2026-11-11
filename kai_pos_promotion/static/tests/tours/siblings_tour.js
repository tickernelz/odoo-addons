import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as SelectionPopup from "@point_of_sale/../tests/generic_helpers/selection_popup_util";
import * as Order from "@point_of_sale/../tests/generic_helpers/order_widget_util";
import { inLeftSide } from "@point_of_sale/../tests/pos/tours/utils/common";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";
import { registry } from "@web/core/registry";
import { promotionLineStaysUnselected } from "@kai_pos_promotion/../tests/tours/promotion_tours";

registry.category("web_tour.tours").add("kai_pos_promotion_siblings_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            { content: "open the cashier list", trigger: ".login-overlay .select-cashier", run: "click" },
            SelectionPopup.has("Promo Cashier", { run: "click" }),
            ProductScreen.isShown(),
            ProductScreen.addOrderline("Promo Gift Box", "1"),
            inLeftSide(Order.hasLine({ productName: "Seminyak Tier – 15%", price: "-78,000.00" })),
            { content: "the promotion asks no manager PIN", trigger: negate(".o_mv_dialog") },
            promotionLineStaysUnselected("Seminyak Tier"),
            { content: "still no manager PIN", trigger: negate(".o_mv_dialog") },
            ProductScreen.clickPayButton(),
            PaymentScreen.totalIs("442,000.00"),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            { content: "customer is asked first", trigger: ".modal .modal-header:contains(Customer required)" },
            { content: "select a customer", trigger: ".modal .modal-footer button:contains(Select customer)", run: "click" },
            { content: "pick the customer", trigger: ".modal .partner-list b:contains(Promo Customer)", run: "click" },
            { content: "the manager validates the payment", trigger: ".o_mv_dialog" },
            { content: "choose the manager", trigger: '.o_mv_managers button:contains("Promo Manager")', run: "click" },
            ..."482915".split("").map((digit) => ({
                content: `PIN digit ${digit}`,
                trigger: `.o_mv_dialog .numpad button[value="${digit}"]`,
                run: "click",
            })),
            { content: "validate the PIN", trigger: ".o_mv_validate:not([disabled])", run: "click" },
            ReceiptScreen.isShown(),
            { content: "the receipt names the promotion", trigger: ".pos-receipt:contains(Seminyak Tier – 15%)" },
        ].flat(),
});

registry.category("web_tour.tours").add("kai_pos_promotion_bank_card_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            { content: "open the register", trigger: ".modal .btn-primary:contains(Open Register)", run: "click" },
            ProductScreen.addOrderline("Promo Gift Box", "2"),
            inLeftSide(Order.hasLine({ productName: "Seminyak Tier – 20%", price: "-208,000.00" })),
            ProductScreen.clickPayButton(),
            PaymentScreen.totalIs("832,000.00"),
            PaymentScreen.clickPaymentMethod("Card Bank"),
            { content: "enter card prefix", trigger: "#bank-card-bin", run: "edit 900150" },
            { content: "enter last four digits", trigger: "#bank-card-last4", run: "edit 1234" },
            { content: "enter approval code", trigger: "#bank-card-approval", run: "edit AP1234" },
            {
                content: "apply the bank card discount",
                trigger: ".o_dialog button:contains(Apply Discount):not([disabled])",
                run: "click",
            },
            PaymentScreen.totalIs("732,000.00"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
        ].flat(),
});
