import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { openWindow } from "@kai_pos_promotion/app/engine";

function taxKey(taxes) {
    return taxes
        .filter((tax) => tax.amount_type !== "fixed")
        .map((tax) => tax.id)
        .sort((a, b) => a - b)
        .join(",");
}

const scopes = new Map();

function scopeOf(promo) {
    let scope = scopes.get(promo.id);
    if (!scope) {
        scope = {
            products: new Set(promo.product_ids.map((product) => product.id)),
            categories: new Set(promo.categ_ids.map((category) => category.id)),
            excludedProducts: new Set(promo.excluded_product_ids.map((product) => product.id)),
            excludedCategories: new Set(promo.excluded_categ_ids.map((category) => category.id)),
            verdicts: new Map(),
        };
        scope.everything = !scope.products.size && !scope.categories.size;
        scopes.set(promo.id, scope);
    }
    return scope;
}

function inCategories(product, categoryIds) {
    if (!categoryIds.size) {
        return false;
    }
    let category = product?.categ_id || product?.product_tmpl_id?.categ_id;
    while (category) {
        if (categoryIds.has(category.id)) {
            return true;
        }
        category = category.parent_id;
    }
    return false;
}

export function kaiCounts(promo, product) {
    if (!product) {
        return false;
    }
    const scope = scopeOf(promo);
    let verdict = scope.verdicts.get(product.id);
    if (verdict === undefined) {
        verdict =
            !scope.excludedProducts.has(product.id) &&
            !inCategories(product, scope.excludedCategories) &&
            (scope.everything || scope.products.has(product.id) || inCategories(product, scope.categories));
        scope.verdicts.set(product.id, verdict);
    }
    return verdict;
}

patch(PosOrder.prototype, {
    getOrderlines() {
        const lines = super.getOrderlines(...arguments);
        return [...lines.filter((line) => !line.kaiIsPromotion), ...lines.filter((line) => line.kaiIsPromotion)];
    },
    selectOrderline(line) {
        if (line?.kaiIsPromotion) {
            return;
        }
        return super.selectOrderline(...arguments);
    },
    isLineValidForLoyaltyPoints(line) {
        return !line.kaiIsPromotion && super.isLineValidForLoyaltyPoints(...arguments);
    },
    _get_regular_order_lines() {
        const lines = super._get_regular_order_lines(...arguments);
        return lines ? lines.filter((line) => !line.kaiIsPromotion) : lines;
    },
    _programIsApplicable(program) {
        if (program.kai_happy_hour && !openWindow(program.kai_schedule_ids, this.config.kai_timezone).open) {
            return false;
        }
        return super._programIsApplicable(...arguments);
    },
    kaiPromotionLines() {
        return this.lines.filter((line) => line.kaiIsManagedPromotion);
    },
    kaiRewardCounts(line, promo) {
        const reward = line.reward_id;
        const program = reward?.program_id;
        if (!reward || ["gift_card", "ewallet"].includes(program?.program_type)) {
            return false;
        }
        if (reward.reward_type === "product") {
            return kaiCounts(promo, line._reward_product_id || line.reward_product_id);
        }
        if (reward.discount_applicability === "order") {
            return false;
        }
        const targets = new Set(reward.all_discount_product_ids.map((product) => product.id));
        return this.lines.some(
            (other) =>
                !other.is_reward_line && targets.has(other.product_id?.id) && kaiCounts(promo, other.product_id)
        );
    },
    kaiBasisRows() {
        const ignored = new Set(this._getIgnoredProductIdsTotalDiscount());
        const rows = [];
        for (const line of this.lines) {
            if (
                !line.qty ||
                line.kaiIsPromotion ||
                line.is_bank_card_discount ||
                line.refunded_orderline_id ||
                ignored.has(line.product_id?.id) ||
                line.isTipLine?.()
            ) {
                continue;
            }
            rows.push({
                line,
                reward: Boolean(line.is_reward_line),
                included: line.prices.total_included,
                base: line.qty * line.price_unit * (1 - line.getDiscount() / 100),
                key: taxKey(line.tax_ids),
            });
        }
        return rows;
    },
    kaiBasis(promo, rows = this.kaiBasisRows()) {
        const parts = {};
        let total = 0;
        for (const row of rows) {
            const counted = row.reward
                ? this.kaiRewardCounts(row.line, promo)
                : kaiCounts(promo, row.line.product_id);
            if (!counted) {
                continue;
            }
            const part = (parts[row.key] ||= { key: row.key, taxes: row.line.tax_ids, included: 0, base: 0, shares: {} });
            part.included += row.included;
            part.base += row.base;
            if (!row.reward) {
                part.shares[row.line.uuid] = (part.shares[row.line.uuid] || 0) + row.included;
            }
            total += row.included;
        }
        return { total, parts: Object.values(parts).filter((part) => part.included > 0) };
    },
});
