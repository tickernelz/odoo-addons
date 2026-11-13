import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";

const { DateTime } = luxon;

export class XenditQrisDialog extends Component {
    static template = "pos_xendit_qris.XenditQrisDialog";
    static components = { Dialog };
    static props = {
        request: Object,
        amount: String,
        check: Function,
        cancel: Function,
        close: Function,
    };

    setup() {
        this.state = useState({ busy: false, notice: "" });
    }

    get validUntil() {
        return DateTime.fromSQL(this.props.request.expires_at, { zone: "utc" })
            .toLocal()
            .toFormat("HH:mm");
    }

    async run(action) {
        this.state.busy = true;
        this.state.notice = "";
        const outcome = await action();
        this.state.busy = false;
        if (outcome === "pending") {
            this.state.notice = _t("Not paid yet. Ask the customer to finish the payment in their app.");
        }
    }

    onCheck() {
        return this.run(this.props.check);
    }

    onCancel() {
        return this.run(this.props.cancel);
    }
}
