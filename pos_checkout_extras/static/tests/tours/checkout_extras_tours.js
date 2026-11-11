import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
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
    return {
        content: "the customer required dialog is open",
        trigger: ".modal .modal-header:contains(Customer required)",
    };
}

function cancelCustomerDialog() {
    return {
        content: "cancel the customer required dialog",
        trigger: ".modal .modal-footer button:contains(Cancel)",
        run: "click",
    };
}

function selectCustomer() {
    return [
        {
            content: "choose to select a customer",
            trigger: ".modal .modal-footer button:contains(Select customer)",
            run: "click",
        },
        {
            content: "pick the customer",
            trigger: `.modal .partner-list b:contains(${CUSTOMER})`,
            run: "click",
        },
    ];
}

function productScreen() {
    return { content: "product screen is shown", trigger: ".product-screen" };
}

function paymentScreen() {
    return { content: "payment screen is shown", trigger: ".payment-screen" };
}

function receiptScreen() {
    return { content: "receipt screen is shown", trigger: ".receipt-screen" };
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
            cancelCustomerDialog(),
            productScreen(),
            ProductScreen.clickPayButton(false),
            customerDialog(),
            selectCustomer(),
            paymentScreen(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            receiptScreen(),
        ].flat(Infinity),
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
            receiptScreen(),
        ].flat(Infinity),
});

registry.category("web_tour.tours").add("pos_checkout_extras_fast_payment", {
    steps: () =>
        [
            ...openRegister(),
            ProductScreen.addOrderline(PRODUCT, "1"),
            {
                content: "pay with the fast payment button",
                trigger: ".product-screen .fast-pay-button:contains(Bank)",
                run: "click",
            },
            customerDialog(),
            selectCustomer(),
            receiptScreen(),
        ].flat(Infinity),
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
            receiptScreen(),
            {
                content: "the receipt prints the reference",
                trigger: ".pos-receipt-payment-reference:contains(TRF-2026-0001)",
            },
        ].flat(Infinity),
});

registry.category("web_tour.tours").add("pos_checkout_extras_refund", {
    steps: () =>
        [
            ...openRegister(),
            ...ProductScreen.clickRefund(),
            TicketScreen.selectOrder("Refundable Order"),
            ProductScreen.clickNumpad("1"),
            TicketScreen.confirmRefund(),
            paymentScreen(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            receiptScreen(),
        ].flat(Infinity),
});

registry.category("web_tour.tours").add("pos_checkout_extras_with_bank_card", {
    steps: () =>
        [
            ...openRegister(),
            ProductScreen.addOrderline(PRODUCT, "1"),
            ProductScreen.clickPayButton(false),
            customerDialog(),
            selectCustomer(),
            paymentScreen(),
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
            { content: "card dialog is open", trigger: ".modal .modal-header:contains(Bank Card Programme)" },
            { content: "enter card prefix", trigger: "#bank-card-bin", run: "edit 900150" },
            { content: "enter last four digits", trigger: "#bank-card-last4", run: "edit 1234" },
            { content: "enter approval code", trigger: "#bank-card-approval", run: "edit AP1234" },
            {
                content: "apply the discount",
                trigger: ".o_dialog button:contains(Apply Discount):not([disabled])",
                run: "click",
            },
            PaymentScreen.clickValidate(),
            receiptScreen(),
            { content: "receipt shows the card block", trigger: ".pos-receipt-bank-card" },
            {
                content: "receipt shows the payment reference",
                trigger: ".pos-receipt-payment-reference:contains(EDC-778899)",
            },
        ].flat(Infinity),
});
