import { registry } from "@web/core/registry";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";

const { DateTime } = luxon;

function equal(actual, expected, label) {
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        throw new Error(`${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
    }
}

const TIERS = [
    { threshold: 300000, discount_type: "percent", value: 10 },
    { threshold: 500000, discount_type: "percent", value: 15 },
    { threshold: 1000000, discount_type: "percent", value: 20 },
];
const WEEKDAYS = { day_mon: true, day_tue: true, day_wed: true, day_thu: true, day_fri: true, day_sat: false, day_sun: false };

function checks() {
    const { allocate, groupKey, openWindow, pickWinners, roundHalfUp, tierOutcome } =
        odoo.loader.modules.get("@kai_pos_promotion/app/engine");
    equal(tierOutcome(TIERS, 299999, 0, 1).amount, 0, "below the first tier");
    equal(tierOutcome(TIERS, 300000, 0, 1).amount, 30000, "threshold is inclusive");
    equal(tierOutcome(TIERS, 315000, 0, 1).amount, 31500, "O-05");
    equal(tierOutcome(TIERS, 520000, 0, 1).amount, 78000, "O-06");
    equal(tierOutcome(TIERS, 1040000, 0, 1).amount, 208000, "O-07");
    equal(tierOutcome(TIERS, 1040000, 150000, 1).amount, 150000, "maximum discount");
    equal(tierOutcome(TIERS, 320000, 0, 1).next.value, 15, "next tier");
    equal(tierOutcome(TIERS, 333335, 0, 1).amount, 33334, "rounded once, half up");
    equal(roundHalfUp(12.5, 1), 13, "half rounds up");
    equal(roundHalfUp(31549, 100), 31500, "rounding step below half");
    equal(roundHalfUp(31550, 100), 31600, "rounding step at half");
    equal(
        tierOutcome([{ threshold: 400000, discount_type: "amount", value: 120000 }], 450000, 0, 1).amount,
        120000,
        "amount tier"
    );

    const winners = pickWinners([
        { group: "ORDER-TIER", amount: 78000, priority: 10, order: 2, name: "a" },
        { group: "ORDER-TIER", amount: 78000, priority: 5, order: 3, name: "b" },
        { group: "ORDER-TIER", amount: 62400, priority: 1, order: 1, name: "c" },
        { group: "OTHER", amount: 1000, priority: 10, order: 4, name: "d" },
        { group: "OTHER", amount: 1000, priority: 10, order: 5, name: "e" },
        { group: "EMPTY", amount: 0, priority: 1, order: 6, name: "f" },
    ]);
    equal(winners.get("ORDER-TIER").name, "b", "equal discounts: lower priority wins");
    equal(winners.get("OTHER").name, "d", "equal discount and priority: older wins");
    equal(winners.has("EMPTY"), false, "no winner without a discount");
    equal(groupKey(" order-tier "), "ORDER-TIER", "group names ignore case and spaces");

    const instant = (iso) => DateTime.fromISO(iso, { setZone: true });
    const window = [{ ...WEEKDAYS, hour_from: 14, hour_to: 16 }];
    equal(openWindow(window, "Asia/Makassar", instant("2026-11-11T13:59:59+08:00")).open, false, "13:59:59 WITA");
    equal(openWindow(window, "Asia/Makassar", instant("2026-11-11T14:00:00+08:00")).open, true, "14:00:00 WITA");
    equal(openWindow(window, "Asia/Makassar", instant("2026-11-11T15:59:59+08:00")).open, true, "15:59:59 WITA");
    equal(openWindow(window, "Asia/Makassar", instant("2026-11-11T16:00:00+08:00")).open, false, "16:00:00 WITA");
    equal(openWindow(window, "Asia/Jakarta", instant("2026-11-11T14:00:00+08:00")).open, false, "same instant is 13:00 WIB");
    equal(openWindow(window, "Asia/Jakarta", instant("2026-11-11T15:00:00+08:00")).open, true, "same instant is 14:00 WIB");
    equal(openWindow(window, "Asia/Makassar", instant("2026-11-14T14:30:00+08:00")).open, false, "Saturday is off");
    equal(openWindow([], "Asia/Makassar", instant("2026-11-14T03:00:00+08:00")).open, true, "no window means always");

    const shares = allocate(100001, [1, 1, 1], 1);
    equal(shares.reduce((sum, share) => sum + share, 0), 100001, "allocation keeps the total");
    equal(allocate(0, [], 1), [], "nothing to allocate");
}

registry.category("web_tour.tours").add("kai_pos_promotion_engine_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            {
                content: "engine rules",
                trigger: ".product-screen",
                run: () => {
                    checks();
                },
            },
        ].flat(),
});
