import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { formatCurrency } from "@web/core/currency";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import {
    allocate,
    groupKey,
    lastWindowEnd,
    localTime,
    openWindow,
    pickWinners,
    tierOutcome,
} from "@kai_pos_promotion/app/engine";

const TICK_MS = 30000;

function taxFactor(taxes) {
    let factor = 1;
    for (const tax of taxes) {
        if (!tax.price_include && tax.amount_type === "percent") {
            factor += tax.amount / 100;
        }
    }
    return factor;
}

patch(PosStore.prototype, {
    async setup() {
        this.kaiTick = 0;
        this.kaiLastEvalMs = 0;
        await super.setup(...arguments);
        const schedule = () => this.kaiSchedule();
        const lines = this.models["pos.order.line"];
        lines.addEventListener("create", schedule);
        lines.addEventListener("update", schedule);
        lines.addEventListener("delete", schedule);
        this.kaiWindows = this.kaiOpenWindows();
        setInterval(() => this.kaiOnTick(), TICK_MS);
    },
    kaiSchedule() {
        if (this.kaiApplying || this.kaiQueued) {
            return;
        }
        this.kaiQueued = true;
        queueMicrotask(() => {
            this.kaiQueued = false;
            this.kaiRecompute();
        });
    },
    kaiOpenWindows() {
        const zone = this.config.kai_timezone;
        const open = [];
        for (const promo of this.models["kai.pos.promo"].getAll()) {
            if (promo.happy_hour && openWindow(promo.schedule_ids, zone).open) {
                open.push(`p${promo.id}`);
            }
        }
        for (const program of this.models["loyalty.program"].getAll()) {
            if (program.kai_happy_hour && openWindow(program.kai_schedule_ids, zone).open) {
                open.push(`l${program.id}`);
            }
        }
        return open.join(",");
    },
    kaiOnTick() {
        this.kaiTick++;
        const windows = this.kaiOpenWindows();
        if (windows !== this.kaiWindows) {
            this.kaiWindows = windows;
            this.kaiBadges = null;
            const order = this.getOrder();
            if (order && !order.finalized && !order.isRefund) {
                this.kaiNotifyEnded(this.kaiEndedHappyHours(order));
            }
            if (this.models["loyalty.program"].length) {
                this.updateRewards();
            }
            this.kaiSchedule();
        }
    },
    kaiNotifyEnded(ended) {
        for (const [name, time] of ended) {
            this.notification.add(
                _t("Happy hour %(promotion)s ended at %(time)s. Prices were recalculated.", {
                    promotion: name,
                    time,
                }),
                { type: "warning" }
            );
        }
    },
    kaiFormat(amount) {
        const rounded = this.currency.round(amount);
        const options = Number.isInteger(rounded) ? { digits: [69, 0] } : {};
        return formatCurrency(rounded, this.currency.id, options);
    },
    kaiTierLabel(tier) {
        return tier.discount_type === "percent" ? `${tier.value}%` : this.kaiFormat(tier.value);
    },
    kaiRecompute() {
        const order = this.getOrder();
        if (!order || order.finalized || order.isRefund || this.kaiApplying) {
            return;
        }
        const started = performance.now();
        const zone = this.config.kai_timezone;
        const now = luxon.DateTime.now();
        const candidates = [];
        const rows = order.kaiBasisRows();
        for (const promo of this.models["kai.pos.promo"].getAll()) {
            if (promo.promo_type !== "tier") {
                continue;
            }
            if (promo.happy_hour && !openWindow(promo.schedule_ids, zone, now).open) {
                continue;
            }
            const basis = order.kaiBasis(promo, rows);
            const outcome = tierOutcome(promo.kaiTiers, basis.total, promo.max_discount, promo.rounding);
            candidates.push({
                kind: "custom",
                promo,
                group: groupKey(promo.exclusive_group) || `promo-${promo.id}`,
                priority: promo.priority,
                order: promo.id,
                amount: outcome.amount,
                basis,
                outcome,
            });
        }
        candidates.push(...this.kaiNativeCandidates(order));
        const winners = pickWinners(candidates);
        this.kaiApplying = true;
        try {
            const promotionChanged = this.kaiWriteLines(
                order,
                candidates.filter((c) => c.kind === "custom" && winners.get(c.group) === c)
            );
            this.kaiSettleNative(order, candidates, winners, promotionChanged);
        } finally {
            this.kaiApplying = false;
        }
        order.uiState.kaiBanner = this.kaiBanner(candidates, winners);
        this.kaiLastEvalMs = performance.now() - started;
    },
    kaiNativeCandidates(order) {
        const programs = this.models["loyalty.program"]
            .getAll()
            .filter((program) => groupKey(program.kai_exclusive_group));
        if (!programs.length) {
            return [];
        }
        const suppressed = new Set(order.uiState.kaiSuppressed || []);
        const claimable = suppressed.size ? order.getClaimableRewards(false, false, false) : [];
        const rewardLines = order.lines.filter((line) => line.is_reward_line && !line.kaiIsPromotion);
        const result = [];
        for (const program of programs) {
            const lines = rewardLines.filter((line) => line.reward_id?.program_id?.id === program.id);
            const candidate = {
                kind: "native",
                program,
                group: groupKey(program.kai_exclusive_group),
                priority: program.kai_priority,
                order: 1e9 + program.id,
                amount: 0,
                lines,
                applied: lines.length > 0,
                rewardIds: new Set(lines.map((line) => line.reward_id.id)),
            };
            if (candidate.applied) {
                candidate.amount = lines.reduce((sum, line) => {
                    const fair = this.kaiNativeOrderAmount(order, line.reward_id);
                    return sum + (fair ?? -line.prices.total_included);
                }, 0);
            } else {
                for (const { reward, coupon_id } of claimable) {
                    if (reward.program_id.id !== program.id || !suppressed.has(reward.id)) {
                        continue;
                    }
                    const values = order._getRewardLineValues({
                        reward,
                        coupon_id,
                        product: null,
                        price: null,
                        quantity: null,
                        cost: null,
                    });
                    if (!Array.isArray(values)) {
                        continue;
                    }
                    const amount = -values.reduce(
                        (sum, value) => sum + value.price_unit * value.qty * taxFactor(value.tax_ids || []),
                        0
                    );
                    candidate.rewardIds.add(reward.id);
                    candidate.amount = Math.max(candidate.amount, this.kaiNativeOrderAmount(order, reward) ?? amount);
                }
            }
            if (candidate.applied || candidate.rewardIds.size) {
                result.push(candidate);
            }
        }
        return result;
    },
    kaiNativeOrderAmount(order, reward) {
        if (
            reward?.reward_type !== "discount" ||
            reward.discount_applicability !== "order" ||
            !["percent", "per_order"].includes(reward.discount_mode)
        ) {
            return null;
        }
        let discountable = 0;
        for (const line of order.getOrderlines()) {
            if (line.qty && !line.kaiIsPromotion && line.reward_id !== reward) {
                discountable += line.prices.total_included;
            }
        }
        let amount = reward.discount_mode === "percent" ? (discountable * reward.discount) / 100 : reward.discount;
        if (reward.discount_max_amount > 0) {
            amount = Math.min(amount, reward.discount_max_amount);
        }
        return Math.max(0, Math.min(amount, discountable));
    },
    kaiSettleNative(order, candidates, winners, promotionChanged) {
        const disabled = order.uiState.disabledRewards;
        if (!disabled) {
            return;
        }
        const suppressed = new Set(order.uiState.kaiSuppressed || []);
        let release = false;
        for (const candidate of candidates) {
            if (candidate.kind !== "native") {
                continue;
            }
            const wins = winners.get(candidate.group) === candidate;
            if (candidate.applied && !wins) {
                for (const line of candidate.lines) {
                    line.delete();
                }
                for (const id of candidate.rewardIds) {
                    disabled.add(id);
                    suppressed.add(id);
                }
            } else if (candidate.applied && wins && promotionChanged) {
                release = true;
            } else if (!candidate.applied && wins) {
                for (const id of candidate.rewardIds) {
                    disabled.delete(id);
                    suppressed.delete(id);
                }
                release = true;
            }
        }
        order.uiState.kaiSuppressed = [...suppressed];
        if (release) {
            queueMicrotask(() => this.updateRewards());
        }
    },
    kaiWriteLines(order, winners) {
        const wanted = [];
        for (const winner of winners) {
            const product = winner.promo.discount_product_id;
            if (!product) {
                continue;
            }
            const parts = winner.basis.parts;
            const amounts = allocate(
                winner.amount,
                parts.map((part) => part.included),
                winner.promo.rounding
            );
            const name = `${winner.promo.name} – ${this.kaiTierLabel(winner.outcome.hit)}`;
            parts.forEach((part, index) => {
                if (!amounts[index]) {
                    return;
                }
                wanted.push({
                    promo: winner.promo,
                    product,
                    name,
                    taxes: part.taxes,
                    key: part.key,
                    priceUnit: -this.currency.round((amounts[index] * part.base) / part.included),
                    shares: JSON.stringify(part.shares),
                });
            });
        }
        const existing = order.kaiPromotionLines();
        const same =
            existing.length === wanted.length &&
            wanted.every((want, index) => {
                const line = existing[index];
                return (
                    line.kai_promo_id?.id === want.promo.id &&
                    line.full_product_name === want.name &&
                    Math.abs(line.price_unit - want.priceUnit) < 0.005 &&
                    line.tax_ids.map((tax) => tax.id).sort((a, b) => a - b).join(",") === want.key
                );
            });
        if (same) {
            existing.forEach((line, index) => {
                if (line.kai_promo_shares !== wanted[index].shares) {
                    line.kai_promo_shares = wanted[index].shares;
                }
            });
            return false;
        }
        for (const line of existing) {
            line.delete();
        }
        for (const want of wanted) {
            this.models["pos.order.line"].create({
                order_id: order,
                product_id: want.product,
                qty: 1,
                price_unit: want.priceUnit,
                price_type: "manual",
                tax_ids: want.taxes.map((tax) => ["link", tax]),
                kai_promo_id: want.promo,
                kai_promo_shares: want.shares,
                full_product_name: want.name,
            });
        }
        return true;
    },
    kaiBanner(candidates, winners) {
        let upsell = null;
        for (const candidate of candidates) {
            const { outcome, basis } = candidate;
            if (candidate.kind !== "custom" || !outcome.next || basis.total <= 0) {
                continue;
            }
            const winner = winners.get(candidate.group);
            if (winner && winner !== candidate && outcome.nextAmount <= winner.amount) {
                continue;
            }
            const gap = outcome.next.threshold - basis.total;
            if (!upsell || gap < upsell.gap) {
                upsell = { gap, next: outcome.next };
            }
        }
        if (upsell) {
            return {
                kind: "upsell",
                text: _t("Add %(amount)s more for %(discount)s off", {
                    amount: this.kaiFormat(upsell.gap),
                    discount: this.kaiTierLabel(upsell.next),
                }),
            };
        }
        const top = candidates.find(
            (c) => c.kind === "custom" && winners.get(c.group) === c && !c.outcome.next
        );
        if (top) {
            return {
                kind: "reached",
                text: _t("Congratulations! The customer gets %(promotion)s – %(discount)s", {
                    promotion: top.promo.name,
                    discount: this.kaiTierLabel(top.outcome.hit),
                }),
            };
        }
        return null;
    },
    kaiEndedHappyHours(order) {
        const zone = this.config.kai_timezone;
        const now = luxon.DateTime.now();
        const ended = new Map();
        const check = (name, windows) => {
            if (!openWindow(windows, zone, now).open) {
                const end = lastWindowEnd(windows, zone, now);
                ended.set(name, end ? end.toFormat("HH:mm") : localTime(zone, now).toFormat("HH:mm"));
            }
        };
        for (const line of order.lines) {
            const promo = line.kaiIsManagedPromotion && line.kai_promo_id;
            if (promo?.happy_hour) {
                check(promo.name, promo.schedule_ids);
            }
            const program = line.is_reward_line && line.reward_id?.program_id;
            if (program?.kai_happy_hour) {
                check(program.name, program.kai_schedule_ids);
            }
        }
        return [...ended.entries()];
    },
    async pay() {
        const order = this.getOrder();
        if (order && !order.finalized && !order.isRefund) {
            const ended = this.kaiEndedHappyHours(order);
            if (this.models["loyalty.program"].some((program) => program.kai_happy_hour)) {
                await this.orderUpdateLoyaltyPrograms();
                order._updateRewardLines();
            }
            this.kaiRecompute();
            this.kaiNotifyEnded(ended);
        }
        return super.pay(...arguments);
    },
    kaiHappyHourBadge(templateId) {
        if (!templateId) {
            return "";
        }
        if (!this.kaiBadges || this.kaiBadges.tick !== this.kaiTick) {
            this.kaiBadges = { tick: this.kaiTick, ends: this.kaiBadgeEnds() };
        }
        const endsAt = this.kaiBadges.ends.get(templateId);
        if (!endsAt) {
            return "";
        }
        const left = endsAt.diff(localTime(this.config.kai_timezone), ["hours", "minutes"]);
        return _t("Happy Hour · %(hours)sh %(minutes)sm left", {
            hours: Math.max(0, Math.floor(left.hours)),
            minutes: Math.max(0, Math.floor(left.minutes)),
        });
    },
    kaiBadgeEnds() {
        const zone = this.config.kai_timezone;
        const ends = new Map();
        const mark = (products, endsAt) => {
            for (const product of products) {
                const templateId = product.product_tmpl_id?.id;
                if (templateId && !ends.has(templateId)) {
                    ends.set(templateId, endsAt);
                }
            }
        };
        for (const program of this.models["loyalty.program"].getAll()) {
            if (!program.kai_happy_hour) {
                continue;
            }
            const window = openWindow(program.kai_schedule_ids, zone);
            if (!window.open || !window.endsAt) {
                continue;
            }
            for (const reward of program.reward_ids) {
                if (reward.reward_type === "product") {
                    mark(reward.reward_product_ids, window.endsAt);
                } else if (reward.discount_applicability !== "order") {
                    mark(reward.all_discount_product_ids, window.endsAt);
                }
            }
        }
        for (const promo of this.models["kai.pos.promo"].getAll()) {
            if (!promo.happy_hour) {
                continue;
            }
            const window = openWindow(promo.schedule_ids, zone);
            if (window.open && window.endsAt) {
                mark(promo.product_ids, window.endsAt);
            }
        }
        return ends;
    },
    kaiRefundShares(original, destination) {
        const promotionLines = original.lines.filter((line) => line.kaiIsManagedPromotion);
        if (!promotionLines.length) {
            return;
        }
        for (const line of [...destination.lines]) {
            if (line.kai_promo_refund_of_id && promotionLines.includes(line.kai_promo_refund_of_id)) {
                line.delete();
            }
        }
        const refunds = destination.lines.filter(
            (line) => line.refunded_orderline_id && line.refunded_orderline_id.order_id === original
        );
        for (const promotionLine of promotionLines) {
            let shares = {};
            try {
                shares = JSON.parse(promotionLine.kai_promo_shares || "{}");
            } catch {
                continue;
            }
            const total = Object.values(shares).reduce((sum, share) => sum + share, 0);
            if (!(total > 0)) {
                continue;
            }
            let returned = 0;
            for (const refund of refunds) {
                const sold = refund.refunded_orderline_id;
                const share = shares[sold.uuid];
                if (share && sold.qty) {
                    returned += (share * Math.abs(refund.qty)) / Math.abs(sold.qty);
                }
            }
            if (!(returned > 0)) {
                continue;
            }
            const fraction = Math.min(1, returned / total);
            this.models["pos.order.line"].create({
                order_id: destination,
                product_id: promotionLine.product_id,
                qty: -1,
                price_unit: this.currency.round(promotionLine.price_unit * fraction),
                price_type: "manual",
                tax_ids: promotionLine.tax_ids.map((tax) => ["link", tax]),
                kai_promo_refund_of_id: promotionLine,
                full_product_name: promotionLine.full_product_name,
            });
        }
    },
});
