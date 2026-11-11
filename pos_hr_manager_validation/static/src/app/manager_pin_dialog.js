import { _t } from "@web/core/l10n/translation";
import { Component, useEffect, useExternalListener, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { useService } from "@web/core/utils/hooks";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { ConnectionLostError } from "@web/core/network/rpc";
import { BACKSPACE, EMPTY, ZERO, Numpad, getButtons } from "@point_of_sale/app/components/numpad/numpad";

const MAX_PIN_LENGTH = 12;
const SWALLOW_BARCODES = new Proxy({}, { get: () => () => {} });

export class ManagerPinDialog extends Component {
    static template = "pos_hr_manager_validation.ManagerPinDialog";
    static components = { Dialog, Numpad };
    static props = {
        title: String,
        subtitle: { type: String, optional: true },
        managers: Array,
        defaultManagerId: Number,
        verify: Function,
        getPayload: Function,
        close: Function,
    };

    setup() {
        this.state = useState({ managerId: this.props.defaultManagerId, pin: "", error: "", busy: false });
        this.buttons = getButtons([EMPTY, ZERO, BACKSPACE]);
        useHotkey("enter", () => this.confirm());
        useHotkey("escape", () => this.cancel());
        useExternalListener(window, "keydown", this.onKeydown.bind(this));
        const barcodeReader = useService("barcode_reader");
        useEffect(
            () => {
                if (!barcodeReader) {
                    return;
                }
                const previous = barcodeReader.exclusiveCbMap;
                barcodeReader.exclusiveCbMap = SWALLOW_BARCODES;
                return () => {
                    barcodeReader.exclusiveCbMap = previous;
                };
            },
            () => []
        );
    }

    get managerName() {
        return this.props.managers.find((manager) => manager.id === this.state.managerId)?.name || "";
    }

    get maskedPin() {
        return "\u2022".repeat(this.state.pin.length);
    }

    onKeydown(event) {
        if (event.ctrlKey || event.metaKey || event.altKey) {
            return;
        }
        if (/^[0-9]$/.test(event.key) || event.key === "Backspace") {
            event.preventDefault();
            event.stopPropagation();
            this.press(event.key);
        }
    }

    press(key) {
        if (this.state.busy) {
            return;
        }
        if (key === "Backspace") {
            this.state.pin = this.state.pin.slice(0, -1);
        } else if (/^[0-9]$/.test(key) && this.state.pin.length < MAX_PIN_LENGTH) {
            this.state.pin += key;
            this.state.error = "";
        }
    }

    selectManager(managerId) {
        this.state.managerId = managerId;
        this.state.pin = "";
        this.state.error = "";
    }

    async confirm() {
        const pin = this.state.pin;
        if (!pin || this.state.busy) {
            return;
        }
        this.state.busy = true;
        let result;
        try {
            result = await this.props.verify(this.state.managerId, pin);
        } catch (error) {
            this.state.pin = "";
            this.state.busy = false;
            if (error instanceof ConnectionLostError) {
                this.state.error = _t("The server cannot be reached. Try again when the connection is back.");
                return;
            }
            this.props.close();
            throw error;
        }
        this.state.busy = false;
        this.state.pin = "";
        if (result.status === "granted") {
            this.props.getPayload({ managerId: this.state.managerId, remember: Boolean(result.remember) });
            this.props.close();
            return;
        }
        if (result.status === "locked") {
            this.state.error = _t(
                "Too many wrong PINs. Manager validation is locked until %s.",
                formatDateTime(deserializeDateTime(result.locked_until))
            );
        } else {
            this.state.error = _t("Wrong PIN. %s attempt(s) left.", result.attempts_left);
        }
    }

    cancel() {
        this.props.close();
    }
}
