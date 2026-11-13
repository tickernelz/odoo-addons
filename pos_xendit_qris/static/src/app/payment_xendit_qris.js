import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";
import { XenditQrisDialog } from "./xendit_qris_dialog";

const CLOSED_MESSAGES = {
    expired: _t("The QRIS expired before it was paid."),
    cancelled: _t("The QRIS was cancelled."),
    failed: _t("Xendit reported the QRIS payment as failed."),
};

export class PaymentXenditQris extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this.waiting = {};
    }

    async sendPaymentRequest(uuid) {
        await super.sendPaymentRequest(...arguments);
        const line = this._line(uuid);
        if (!line) {
            return false;
        }
        if (line.amount <= 0) {
            this._alert(_t("QRIS Xendit takes payments only. Refund with another payment method."));
            return false;
        }
        const order = line.pos_order_id;
        let request;
        try {
            request = await this._call("xendit_qris_create", [
                this.pos.session.id,
                order.uuid,
                line.uuid,
                line.amount,
                order.pos_reference || order.name || "",
            ]);
        } catch (error) {
            this._alert(this._message(error));
            return false;
        }
        line.setPaymentStatus("waitingCard");
        return new Promise((resolve) => {
            this.waiting[uuid] = { request, resolve };
            if (!this._settle(uuid, request)) {
                this._open(uuid);
            }
        });
    }

    async sendPaymentCancel(order, uuid) {
        const entry = this.waiting[uuid];
        if (!entry) {
            return true;
        }
        let status;
        try {
            status = await this._call("xendit_qris_cancel", [entry.request.id]);
        } catch (error) {
            this._alert(this._message(error));
            return false;
        }
        if (status.state === "paid") {
            this._alert(_t("The customer has already paid this QRIS."));
            setTimeout(() => this._settle(uuid, status));
            return false;
        }
        this._settle(uuid, status, true);
        return true;
    }

    async check(uuid) {
        const entry = this.waiting[uuid];
        if (!entry) {
            return "closed";
        }
        let status;
        try {
            status = await this._call("xendit_qris_status", [entry.request.id, true]);
        } catch (error) {
            this._alert(this._message(error));
            return "error";
        }
        return this._settle(uuid, status) ? status.state : "pending";
    }

    async cancelFromDialog(uuid) {
        const entry = this.waiting[uuid];
        if (!entry) {
            return "closed";
        }
        let status;
        try {
            status = await this._call("xendit_qris_cancel", [entry.request.id]);
        } catch (error) {
            this._alert(this._message(error));
            return "error";
        }
        return this._settle(uuid, status, true) ? status.state : "pending";
    }

    async onNotification(payload) {
        const uuid = Object.keys(this.waiting).find(
            (key) => this.waiting[key].request.id === payload.request_id
        );
        if (!uuid) {
            return;
        }
        try {
            this._settle(uuid, await this._call("xendit_qris_status", [payload.request_id, false]));
        } catch (error) {
            console.warn("QRIS Xendit status could not be read", error);
        }
    }

    _settle(uuid, status, quiet = false) {
        const entry = this.waiting[uuid];
        if (!entry || status.state === "pending") {
            return false;
        }
        delete this.waiting[uuid];
        entry.settled = true;
        entry.close?.();
        const line = this._line(uuid);
        if (status.state === "paid") {
            if (line) {
                line.transaction_id = status.reference;
            }
            entry.resolve(true);
        } else {
            if (!quiet) {
                this._alert(CLOSED_MESSAGES[status.state] || _t("The QRIS was not paid."));
            }
            entry.resolve(false);
        }
        return true;
    }

    _open(uuid) {
        const entry = this.waiting[uuid];
        if (!entry || entry.settled) {
            return;
        }
        entry.close = this.env.services.dialog.add(
            XenditQrisDialog,
            {
                request: entry.request,
                amount: this.env.utils.formatCurrency(entry.request.amount),
                check: () => this.check(uuid),
                cancel: () => this.cancelFromDialog(uuid),
            },
            {
                onClose: () => {
                    if (this.waiting[uuid] === entry && !entry.settled) {
                        this._open(uuid);
                    }
                },
            }
        );
    }

    _line(uuid) {
        return this.pos.models["pos.payment"].find((payment) => payment.uuid === uuid);
    }

    _call(method, args) {
        return this.pos.data.call("pos.payment.method", method, [[this.payment_method_id.id], ...args]);
    }

    _message(error) {
        return error?.data?.message || _t("The server could not be reached. Check the connection and try again.");
    }

    _alert(body) {
        this.env.services.dialog.add(AlertDialog, { title: _t("QRIS Xendit"), body });
    }
}

register_payment_method("xendit_qris", PaymentXenditQris);
