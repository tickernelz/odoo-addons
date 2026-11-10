import { patch } from "@web/core/utils/patch";
import { onWillStart, useState } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { Navbar } from "@point_of_sale/app/components/navbar/navbar";
import { LanguageDialog } from "./language_dialog";

patch(Navbar.prototype, {
    setup() {
        super.setup(...arguments);
        this.bankCardLang = useState({ langs: [], current: "" });
        onWillStart(async () => {
            try {
                const data = await rpc("/pos_bank_card_program/lang/list");
                this.bankCardLang.langs = data.langs;
                this.bankCardLang.current = data.current;
            } catch {
                this.bankCardLang.langs = [];
                this.bankCardLang.current = "";
            }
        });
    },
    get canSwitchLanguage() {
        return this.bankCardLang.langs.length > 1;
    },
    get currentLanguageName() {
        const hit = this.bankCardLang.langs.find((lang) => lang.code === this.bankCardLang.current);
        return hit ? hit.name : "";
    },
    openLanguageDialog() {
        this.dialog.add(LanguageDialog, {
            langs: this.bankCardLang.langs,
            current: this.bankCardLang.current,
        });
    },
});
