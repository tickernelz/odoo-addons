import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { rpc } from "@web/core/network/rpc";

export class LanguageDialog extends Component {
    static template = "pos_bank_card_program.LanguageDialog";
    static components = { Dialog };
    static props = ["langs", "current", "sessionId", "close"];

    async select(code) {
        if (code !== this.props.current) {
            await rpc("/pos_bank_card_program/lang/set", { code, session_id: this.props.sessionId });
            window.location.reload();
        } else {
            this.props.close();
        }
    }
}
