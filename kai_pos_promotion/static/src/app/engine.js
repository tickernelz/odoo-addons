const DAYS = ["day_mon", "day_tue", "day_wed", "day_thu", "day_fri", "day_sat", "day_sun"];
const EPSILON = 1e-6;

export function groupKey(name) {
    return (name || "").trim().toUpperCase();
}

export function localTime(zone, now = luxon.DateTime.now()) {
    const local = now.setZone(zone || "UTC");
    return local.isValid ? local : now;
}

function secondsOfDay(local) {
    return local.hour * 3600 + local.minute * 60 + local.second;
}

function windowSeconds(window) {
    return [Math.round(window.hour_from * 3600), Math.round(window.hour_to * 3600)];
}

export function openWindow(windows, zone, now) {
    if (!windows.length) {
        return { open: true, endsAt: null };
    }
    const local = localTime(zone, now);
    const second = secondsOfDay(local);
    for (const window of windows) {
        const [from, to] = windowSeconds(window);
        if (window[DAYS[local.weekday - 1]] && second >= from && second < to) {
            return { open: true, endsAt: local.startOf("day").plus({ seconds: to }) };
        }
    }
    return { open: false, endsAt: null };
}

export function lastWindowEnd(windows, zone, now) {
    const local = localTime(zone, now);
    const second = secondsOfDay(local);
    let latest = null;
    for (const window of windows) {
        const [, to] = windowSeconds(window);
        if (window[DAYS[local.weekday - 1]] && to <= second && (latest === null || to > latest)) {
            latest = to;
        }
    }
    return latest === null ? null : local.startOf("day").plus({ seconds: latest });
}

export function roundHalfUp(value, step) {
    const unit = step > 0 ? step : 1;
    const decimals = Math.max(0, -Math.floor(Math.log10(unit)) + 2);
    const rounded = Math.floor(value / unit + 0.5 + EPSILON) * unit;
    return Number(rounded.toFixed(decimals));
}

function tierAmount(tier, basis, maxDiscount, rounding) {
    let amount = tier.discount_type === "percent" ? (basis * tier.value) / 100 : tier.value;
    if (maxDiscount > 0) {
        amount = Math.min(amount, maxDiscount);
    }
    return roundHalfUp(Math.min(amount, basis), rounding);
}

export function tierOutcome(tiers, basis, maxDiscount, rounding) {
    let hit = null;
    let next = null;
    for (const tier of tiers) {
        if (basis + EPSILON >= tier.threshold) {
            hit = tier;
        } else if (!next) {
            next = tier;
        }
    }
    return {
        hit,
        next,
        amount: hit ? tierAmount(hit, basis, maxDiscount, rounding) : 0,
        nextAmount: next ? tierAmount(next, next.threshold, maxDiscount, rounding) : 0,
    };
}

function beats(candidate, best) {
    if (candidate.amount !== best.amount) {
        return candidate.amount > best.amount;
    }
    if (candidate.priority !== best.priority) {
        return candidate.priority < best.priority;
    }
    return candidate.order < best.order;
}

export function pickWinners(candidates) {
    const winners = new Map();
    for (const candidate of candidates) {
        if (!(candidate.amount > 0)) {
            continue;
        }
        const best = winners.get(candidate.group);
        if (!best || beats(candidate, best)) {
            winners.set(candidate.group, candidate);
        }
    }
    return winners;
}

export function allocate(amount, weights, step) {
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    if (!weights.length || total <= 0) {
        return weights.map(() => 0);
    }
    let largest = 0;
    weights.forEach((weight, index) => {
        if (weight > weights[largest]) {
            largest = index;
        }
    });
    const shares = weights.map((weight) => roundHalfUp((amount * weight) / total, step));
    const spread = shares.reduce((sum, share, index) => (index === largest ? sum : sum + share), 0);
    shares[largest] = Number((amount - spread).toFixed(6));
    return shares;
}
