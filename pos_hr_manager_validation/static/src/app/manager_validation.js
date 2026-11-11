import { _t } from "@web/core/l10n/translation";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { ManagerPinDialog } from "@pos_hr_manager_validation/app/manager_pin_dialog";

const TITLES = {
    close_session: () => _t("Manager validation: close the register"),
    delete_order: () => _t("Manager validation: delete the order"),
    delete_line: () => _t("Manager validation: remove the line"),
    payment: () => _t("Manager validation: validate the payment"),
    discount: () => _t("Manager validation: apply a discount"),
    price: () => _t("Manager validation: change the price"),
    decrease_qty: () => _t("Manager validation: decrease the quantity"),
    cash_move: () => _t("Manager validation: cash in or out"),
    create_product: () => _t("Manager validation: create a product"),
    fiscal_position: () => _t("Manager validation: change the tax"),
    pricelist: () => _t("Manager validation: change the pricelist"),
    refund: () => _t("Manager validation: refund"),
};

export function isGuarded(pos, action) {
    return Boolean(
        pos.config.module_pos_hr && pos.config["mv_" + action] && pos.config._mv_managers?.length
    );
}

export async function requestManagerValidation(pos, action, { subtitle = "", reference = false } = {}) {
    if (!isGuarded(pos, action)) {
        return true;
    }
    const managers = pos.config._mv_managers;
    const cashier = pos.getCashier();
    const cashierIsManager = Boolean(cashier && managers.some((manager) => manager.id === cashier.id));
    if (cashierIsManager && pos.mvPresence === cashier.id) {
        return true;
    }
    if (pos.data.network.offline) {
        pos.notification.add(_t("Manager validation needs a connection to the server."), {
            type: "danger",
        });
        return false;
    }
    const target = reference || pos.getOrder()?.uuid || false;
    const result = await makeAwaitable(pos.dialog, ManagerPinDialog, {
        title: TITLES[action](),
        subtitle,
        managers,
        defaultManagerId: cashierIsManager ? cashier.id : managers[0].id,
        verify: (managerId, pin) =>
            pos.data.call("pos.session", "verify_manager_pin", [[pos.session.id], action, managerId, pin], {
                reference: target,
                remember: cashierIsManager && managerId === cashier.id,
            }),
    });
    if (!result) {
        return false;
    }
    if (result.remember) {
        pos.mvPresence = cashier.id;
    }
    return true;
}
