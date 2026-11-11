import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as PosHr from "@pos_hr/../tests/tours/utils/pos_hr_helpers";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";
import { registry } from "@web/core/registry";

function pinDialogIs(title) {
    return { content: "manager PIN dialog is open", trigger: `.modal .modal-title:contains("${title}")` };
}
function chooseManager(name) {
    return {
        content: `choose ${name}`,
        trigger: `.o_mv_managers button:contains("${name}")`,
        run: "click",
    };
}
function clickPin(pin) {
    return pin.split("").map((digit) => ({
        content: `PIN digit ${digit}`,
        trigger: `.o_mv_dialog .numpad button[value="${digit}"]`,
        run: "click",
    }));
}
function typePin(pin) {
    return {
        content: "type the PIN on the keyboard",
        trigger: ".o_mv_dialog .o_mv_pin",
        run: () => {
            for (const key of pin) {
                window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
            }
        },
    };
}
function validatePin() {
    return { content: "validate the PIN", trigger: ".o_mv_validate:not([disabled])", run: "click" };
}
function pinDialogClosed() {
    return { content: "PIN dialog is closed", trigger: negate(".o_mv_dialog") };
}
function login() {
    return [Chrome.startPoS(), PosHr.login("MV Cashier"), ProductScreen.isShown()].flat();
}

registry.category("web_tour.tours").add("pos_mv_with_required_customer_tour", {
    steps: () =>
        [
            login(),
            ProductScreen.clickDisplayedProduct("Desk Pad"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            { content: "customer is asked first", trigger: ".modal .modal-header:contains(Customer required)" },
            { content: "no PIN dialog yet", trigger: negate(".o_mv_dialog") },
            { content: "select a customer", trigger: ".modal .modal-footer button:contains(Select customer)", run: "click" },
            { content: "pick the customer", trigger: ".modal .partner-list b:contains(Deco Addict)", run: "click" },
            pinDialogIs("validate the payment"),
            chooseManager("MV Manager"),
            clickPin("482915"),
            validatePin(),
            ReceiptScreen.isShown(),
        ].flat(),
});

registry.category("web_tour.tours").add("pos_mv_off_tour", {
    steps: () =>
        [
            login(),
            ProductScreen.clickDisplayedProduct("Desk Pad"),
            ProductScreen.clickNumpad("%", "5"),
            { content: "discount applied at once", trigger: ".orderline:contains(5%)" },
            { content: "no PIN dialog", trigger: negate(".o_mv_dialog") },
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
        ].flat(),
});

registry.category("web_tour.tours").add("pos_mv_guarded_tour", {
    steps: () =>
        [
            login(),
            ProductScreen.clickDisplayedProduct("Desk Pad"),
            ProductScreen.clickDisplayedProduct("Letter Tray"),
            ProductScreen.clickNumpad("%", "1"),
            Dialog.is({ title: "Set the new discount" }),
            Dialog.confirm(),
            pinDialogIs("apply a discount"),
            chooseManager("MV Manager"),
            clickPin("111111"),
            validatePin(),
            { content: "wrong PIN keeps the dialog open", trigger: ".o_mv_error:contains(Wrong PIN)" },
            { content: "discount not applied", trigger: negate(".orderline:contains(10%)") },
            typePin("482915"),
            { content: "keyboard PIN is masked", trigger: ".o_mv_pin:contains(••••••)" },
            validatePin(),
            pinDialogClosed(),
            { content: "discount applied", trigger: ".orderline:contains(10%)" },
            ProductScreen.clickNumpad("Qty", "⌫", "⌫"),
            pinDialogIs("remove the line"),
            chooseManager("MV Manager"),
            clickPin("482915"),
            validatePin(),
            pinDialogClosed(),
            { content: "line removed", trigger: negate(".orderline:contains(Letter Tray)") },
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
            pinDialogIs("validate the payment"),
            chooseManager("MV Manager"),
            clickPin("482915"),
            validatePin(),
            ReceiptScreen.isShown(),
        ].flat(),
});
