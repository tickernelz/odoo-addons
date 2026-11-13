/* global posmodel */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import { registry } from "@web/core/registry";

async function payAtXendit() {
    const line = posmodel.getPendingPaymentLine("xendit_qris");
    const terminal = line.payment_method_id.payment_terminal;
    const request = terminal.waiting[line.uuid].request;
    const response = await fetch("/pos_xendit_qris/webhook", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-callback-token": "webhook-token-for-tests" },
        body: JSON.stringify({
            event: "payment.capture",
            data: {
                payment_id: "py-tour",
                reference_id: request.reference,
                payment_request_id: "pr-" + request.reference,
                currency: "IDR",
                request_amount: request.amount,
                status: "SUCCEEDED",
                captures: [{ capture_id: "cptr-tour", capture_amount: request.amount }],
            },
        }),
    });
    if (response.status !== 200) {
        throw new Error("webhook answered " + response.status);
    }
}

registry.category("web_tour.tours").add("pos_xendit_qris_pay", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline("QRIS Coffee"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("QRIS Xendit"),
            {
                content: "The QRIS is shown to the customer",
                trigger: ".modal .o_xendit_qris_code[src^='data:image/png']",
            },
            {
                content: "The customer pays and Xendit calls the webhook",
                trigger: ".modal .o_xendit_qris_reference",
                run: payAtXendit,
            },
            ReceiptScreen.isShown(),
        ].flat(),
});
