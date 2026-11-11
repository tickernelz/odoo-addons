import { _t } from "@web/core/l10n/translation";
import { ask } from "@point_of_sale/app/utils/make_awaitable_dialog";

export async function askForCustomer(pos, body) {
    const confirmed = await ask(pos.dialog, {
        title: _t("Customer required"),
        body,
        confirmLabel: _t("Select customer"),
        cancelLabel: _t("Cancel"),
    });
    if (!confirmed) {
        return false;
    }
    return Boolean(await pos.selectPartner());
}
