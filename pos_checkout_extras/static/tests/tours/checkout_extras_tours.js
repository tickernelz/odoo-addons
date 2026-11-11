import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as PartnerList from "@point_of_sale/../tests/pos/tours/utils/partner_list_util";
import * as TicketScreen from "@point_of_sale/../tests/pos/tours/utils/ticket_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import { registry } from "@web/core/registry";

const PRODUCT = "Checkout Tour Product";
const CUSTOMER = "Ayu Lestari";

function openRegister() {
    return [Chrome.startPoS(), Dialog.confirm("Open Register")];
}

function customerDialog() {
    return Dialog.is({ title: "Customer required" });
}

function selectCustomer() {
    return [Dialog.confirm("Select customer"), PartnerList.clickPartner(CUSTOMER)];
}

function requiredBadge(scope) {
    return {
        content: "the customer button shows the Required badge",
        trigger: `${scope} .o-customer-required-badge:contains(Required)`,
    };
}

registry.category("web_tour.tours").add("pos_checkout_extras_order_mode", {
    steps: () =>
        [
            ...openRegister(),
            ProductScreen.addOrderline(PRODUCT, "1"),
            requiredBadge(".product-screen"),
            ProductScreen.clickPayButton(false),
            customerDialog(),
            Dialog.cancel(),
            ProductScreen.isShown(),
            ProductScreen.clickPayButton(false),
            customerDialog(),
            selectCustomer(),
            PaymentScreen.isShown(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
        ].flat(),
});

registry.category("web_tour.tours").add("pos_checkout_extras_payment_mode", {
    steps: () =>
        [
            ...openRegister(),
            ProductScreen.addOrderline(PRODUCT, "1"),
            ProductScreen.clickPayButton(),
            requiredBadge(".payment-screen"),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            customerDialog(),
            selectCustomer(),
            ReceiptScreen.isShown(),
        ].flat(),
});

registry.category("web_tour.tours").add("pos_checkout_extras_fast_payment", {
    steps: () =>
        [
            ...openRegister(),
            ProductScreen.addOrderline(PRODUCT, "1"),
            ProductScreen.clickFastPaymentButton("Bank"),
            customerDialog(),
            selectCustomer(),
            ReceiptScreen.isShown(),
        ].flat(),
});

registry.category("web_tour.tours").add("pos_checkout_extras_payment_reference", {
    steps: () =>
        [
            ...openRegister(),
            ProductScreen.addOrderline(PRODUCT, "1"),
            ProductScreen.clickPayButton(),
            {
                content: "open the payment reference popup",
                trigger: ".payment-screen .o-payment-reference-button:contains(Add Payment Reference)",
                run: "click",
            },
            {
                content: "type the reference",
                trigger: ".modal textarea, .modal input",
                run: "edit TRF-2026-0001",
            },
            Dialog.confirm(),
            {
                content: "the button shows the reference",
                trigger: ".payment-screen .o-payment-reference-button:contains(TRF-2026-0001)",
            },
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            {
                content: "the receipt prints the reference",
                trigger: ".pos-receipt-payment-reference:contains(TRF-2026-0001)",
            },
        ].flat(),
});

registry.category("web_tour.tours").add("pos_checkout_extras_refund", {
    steps: () =>
        [
            ...openRegister(),
            ...ProductScreen.clickRefund(),
            TicketScreen.selectOrder("Refundable Order"),
            ProductScreen.clickNumpad("1"),
            TicketScreen.confirmRefund(),
            PaymentScreen.isShown(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
        ].flat(),
});

registry.category("web_tour.tours").add("pos_checkout_extras_with_bank_card", {
    steps: () =>
        [
            ...openRegister(),
            ProductScreen.addOrderline(PRODUCT, "1"),
            ProductScreen.clickPayButton(false),
            customerDialog(),
            selectCustomer(),
            PaymentScreen.isShown(),
            {
                content: "add the payment reference",
                trigger: ".payment-screen .o-payment-reference-button",
                run: "click",
            },
            {
                content: "type the reference",
                trigger: ".modal textarea, .modal input",
                run: "edit EDC-778899",
            },
            Dialog.confirm(),
            PaymentScreen.clickPaymentMethod("Card Bank"),
            Dialog.is({ title: "Bank Card Programme" }),
            { content: "enter card prefix", trigger: "#bank-card-bin", run: "edit 900150" },
            { content: "enter last four digits", trigger: "#bank-card-last4", run: "edit 1234" },
            { content: "enter approval code", trigger: "#bank-card-approval", run: "edit AP1234" },
            {
                content: "apply the discount",
                trigger: ".o_dialog button:contains(Apply Discount):not([disabled])",
                run: "click",
            },
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            { content: "receipt shows the card block", trigger: ".pos-receipt-bank-card" },
            {
                content: "receipt shows the payment reference",
                trigger: ".pos-receipt-payment-reference:contains(EDC-778899)",
            },
        ].flat(),
});
