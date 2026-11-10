import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { ConnectionLostError } from "@web/core/network/rpc";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import {
    CARD_TYPE_LABELS,
    detectLocal,
    findBinRange,
    reasonMessage,
} from "@pos_bank_card_program/app/utils/bank_card_detection";

export class BankCardDialog extends Component {
    static template = "pos_bank_card_program.BankCardDialog";
    static components = { Dialog };
    static props = {
        order: Object,
        paymentMethod: Object,
        paymentAmount: { type: Number, optional: true },
        getPayload: Function,
        close: Function,
    };

    setup() {
        this.pos = usePos();
        this.state = useState({
            cardBin: "",
            cardLast4: "",
            approvalCode: "",
            serverMessage: "",
            busy: false,
        });
    }

    get basisAmount() {
        const range = findBinRange(
            this.pos.models,
            this.state.cardBin,
            this.pos.config.id,
            this.props.paymentMethod.id
        );
        if (!range) {
            return this.props.order.totalDue || 0;
        }
        return this.props.order.getBankCardBasis(
            range.program_id,
            this.props.paymentAmount || 0
        );
    }

    get detection() {
        return detectLocal(
            this.pos.models,
            this.state.cardBin,
            this.basisAmount,
            this.pos.config.id,
            this.props.paymentMethod.id,
            this.props.order.currency
        );
    }

    get program() {
        return this.detection.program;
    }

    get cardTypeLabel() {
        const type = this.detection.binRange?.card_type;
        return type ? CARD_TYPE_LABELS[type]?.() || type : "";
    }

    get inputsValid() {
        if (![6, 8].includes(this.state.cardBin.length)) {
            return false;
        }
        if (this.state.cardLast4.length !== 4) {
            return false;
        }
        if (this.program?.require_approval_code && !this.state.approvalCode.trim()) {
            return false;
        }
        return true;
    }

    get canApply() {
        return this.inputsValid && this.detection.eligible && !this.state.busy;
    }

    get resultMessage() {
        if (this.state.serverMessage) {
            return this.state.serverMessage;
        }
        if (![6, 8].includes(this.state.cardBin.length)) {
            return _t("Enter the first 6 to 8 digits of the card to check the programme.");
        }
        return this.detection.message || "";
    }

    onInput(field, event) {
        const digits = event.target.value.replace(/\D/g, "");
        this.state[field] = field === "cardBin" ? digits.slice(0, 8) : digits.slice(0, 4);
        event.target.value = this.state[field];
        this.state.serverMessage = "";
    }

    onApprovalInput(event) {
        this.state.approvalCode = event.target.value.toUpperCase().slice(0, 12);
        event.target.value = this.state.approvalCode;
        this.state.serverMessage = "";
    }

    offlineCount(program) {
        const orders = this.pos.models["pos.order"].getAll();
        return orders.filter(
            (order) =>
                order.bank_card_offline && order.bank_card_program_id?.id === program.id
        ).length;
    }

    applyOffline(detection) {
        const program = detection.program;
        if (!program.offline_allowed || this.offlineCount(program) >= (program.offline_allowance || 0)) {
            this.state.serverMessage = reasonMessage("offline");
            return;
        }
        this.props.getPayload({
            program,
            binRange: detection.binRange,
            card_bin: this.state.cardBin,
            card_last4: this.state.cardLast4,
            approval_code: this.state.approvalCode.trim(),
            discount_amount: detection.discount_amount,
            bank_share_amount: detection.bank_share_amount,
            store_share_amount: detection.store_share_amount,
            usage_id: false,
            offline: true,
        });
        this.props.close();
    }

    async apply() {
        if (!this.canApply) {
            return;
        }
        const detection = this.detection;
        const program = detection.program;
        if (!navigator.onLine) {
            this.applyOffline(detection);
            return;
        }
        this.state.busy = true;
        try {
            const result = await this.pos.data.call("pos.bank.card.program", "reserve_quota", [
                program.id,
                this.state.cardBin,
                this.state.cardLast4,
                this.basisAmount,
                this.state.approvalCode.trim() || false,
                this.pos.config.id,
                this.props.paymentMethod.id,
                false,
                false,
            ]);
            if (!result?.reserved) {
                this.state.serverMessage = reasonMessage(result?.reason, result?.message);
                return;
            }
            this.props.getPayload({
                program,
                binRange: detection.binRange,
                card_bin: this.state.cardBin,
                card_last4: this.state.cardLast4,
                approval_code: this.state.approvalCode.trim(),
                discount_amount: result.discount_amount,
                bank_share_amount: result.bank_share_amount,
                store_share_amount: result.store_share_amount,
                usage_id: result.usage_id,
                offline: false,
            });
            this.props.close();
        } catch (error) {
            if (error instanceof ConnectionLostError) {
                this.applyOffline(detection);
            } else {
                throw error;
            }
        } finally {
            this.state.busy = false;
        }
    }

    skip() {
        this.props.getPayload(false);
        this.props.close();
    }
}
