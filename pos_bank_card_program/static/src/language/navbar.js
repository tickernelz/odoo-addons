import { patch } from "@web/core/utils/patch";
import { user } from "@web/core/user";
import { Navbar } from "@point_of_sale/app/components/navbar/navbar";
import { LanguageDialog } from "./language_dialog";

patch(Navbar.prototype, {
    get installedLanguages() {
        return this.pos.models["res.lang"]
            .getAll()
            .map((lang) => ({ code: lang.code, name: lang.name }));
    },
    get canSwitchLanguage() {
        return this.installedLanguages.length > 1 && !this.pos.data.network.offline;
    },
    get currentLanguageName() {
        const hit = this.installedLanguages.find((lang) => lang.code === user.context.lang);
        return hit ? hit.name : "";
    },
    openLanguageDialog() {
        this.dialog.add(LanguageDialog, {
            langs: this.installedLanguages,
            current: user.context.lang,
            sessionId: this.pos.session.id,
        });
    },
});
