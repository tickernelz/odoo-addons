import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_bank_card_program_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline("Outlet Tour Product", "1", "1000000"),
            ProductScreen.totalAmountIs("1,000,000.00"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Card Bank"),
            Dialog.is({ title: "Bank Card Programme" }),
            {
                content: "enter card prefix",
                trigger: "#bank-card-bin",
                run: "edit 900150",
            },
            {
                content: "detection chip shows the programme",
                trigger: ".o-bank-card-chip:contains(Bank A Tour Programme)",
            },
            {
                content: "enter last four digits",
                trigger: "#bank-card-last4",
                run: "edit 1234",
            },
            {
                content: "enter approval code",
                trigger: "#bank-card-approval",
                run: "edit AP1234",
            },
            {
                content: "apply the discount",
                trigger: ".o_dialog button:contains(Apply Discount):not([disabled])",
                run: "click",
            },
            {
                content: "remove discount button is available",
                trigger: "button:contains(Remove Bank Card Discount)",
            },
            PaymentScreen.totalIs("900,000.00"),
            {
                content: "reload the page",
                trigger: ".payment-screen",
                expectUnloadPage: true,
                run: function () {
                    window.location.reload();
                },
            },
            {
                content: "order and card context survive the reload",
                trigger: ".payment-screen button:contains(Remove Bank Card Discount)",
            },
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            {
                content: "receipt shows the card block",
                trigger: ".pos-receipt-bank-card:contains(Bank A Tour Programme)",
            },
            {
                content: "receipt shows the masked card",
                trigger: ".pos-receipt-bank-card:contains(9001 50** **** 1234)",
            },
            ReceiptScreen.clickNextOrder(),
            ProductScreen.addOrderline("Outlet Tour Product", "1", "1000000"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Card Bank"),
            Dialog.is({ title: "Bank Card Programme" }),
            {
                content: "enter excluded tier prefix",
                trigger: "#bank-card-bin",
                run: "edit 900250",
            },
            {
                content: "rejection reason is shown",
                trigger: ".o-bank-card-dialog .alert-warning:contains(Gold tier is not included)",
            },
            {
                content: "continue without discount",
                trigger: ".o_dialog button:contains(Continue Without Discount)",
                run: "click",
            },
            PaymentScreen.totalIs("1,000,000.00"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),
        ].flat(),
});
