import { _t } from "@web/core/l10n/translation";

export function binInterval(valueFrom, valueTo) {
    const factor = valueFrom.length === 6 ? 100 : 1;
    return [parseInt(valueFrom) * factor, parseInt(valueTo) * factor + factor - 1];
}

export function maskedCard(cardBin, cardLast4) {
    const bin = cardBin || "";
    const last4 = cardLast4 || "";
    if (bin.length >= 8) {
        return `${bin.slice(0, 4)} ${bin.slice(4, 8)} **** ${last4}`;
    }
    return `${bin.slice(0, 4)} ${bin.slice(4, 6)}** **** ${last4}`;
}

export function computeDiscount(program, basisAmount, currency) {
    let discount = ((basisAmount || 0) * (program.discount_percent || 0)) / 100;
    if (program.max_discount) {
        discount = Math.min(discount, program.max_discount);
    }
    discount = Math.max(discount, 0);
    if (currency) {
        discount = currency.round(discount);
    }
    let bankShare = (discount * (program.bank_share_percent || 0)) / 100;
    if (currency) {
        bankShare = currency.round(bankShare);
    }
    return {
        discount_amount: discount,
        bank_share_amount: bankShare,
        store_share_amount: discount - bankShare,
    };
}

export function findBinRange(models, cardBin, configId, paymentMethodId) {
    if (!/^\d+$/.test(cardBin || "") || ![6, 8].includes((cardBin || "").length)) {
        return null;
    }
    const [low, high] = binInterval(cardBin, cardBin);
    const ranges = models["pos.bank.card.bin.range"]?.getAll() || [];
    const matches = ranges.filter((range) => {
        const [rangeLow, rangeHigh] = binInterval(range.bin_from, range.bin_to);
        if (!(low <= rangeHigh && rangeLow <= high)) {
            return false;
        }
        const program = range.program_id;
        if (configId && program.config_ids?.length && !program.config_ids.some((config) => config.id === configId)) {
            return false;
        }
        if (
            paymentMethodId &&
            program.payment_method_ids?.length &&
            !program.payment_method_ids.some((method) => method.id === paymentMethodId)
        ) {
            return false;
        }
        return true;
    });
    if (!matches.length) {
        return null;
    }
    const exact = matches.filter((range) => range.bin_from.length === cardBin.length);
    return (exact.length ? exact : matches)[0];
}

export const CARD_TYPE_LABELS = {
    credit: () => _t("Credit"),
    debit: () => _t("Debit"),
};

export function detectLocal(models, cardBin, basisAmount, configId, paymentMethodId, currency) {
    const result = {
        eligible: false,
        reason: false,
        message: false,
        program: null,
        binRange: null,
        discount_amount: 0,
        bank_share_amount: 0,
        store_share_amount: 0,
    };
    const binRange = findBinRange(models, cardBin, configId, paymentMethodId);
    if (!binRange) {
        result.reason = "bin_not_registered";
        result.message = _t("This card prefix is not registered in any active programme.");
        return result;
    }
    result.binRange = binRange;
    result.program = binRange.program_id;
    if (!binRange.eligible) {
        result.reason = "tier_excluded";
        result.message =
            binRange.ineligible_reason || _t("This card tier is not included in the programme.");
        return result;
    }
    if (result.program.min_amount && (basisAmount || 0) < result.program.min_amount) {
        result.reason = "below_minimum";
        result.message = _t("The purchase is below the programme minimum.");
        return result;
    }
    Object.assign(result, computeDiscount(result.program, basisAmount || 0, currency));
    result.eligible = true;
    result.message = _t("%s applies to this card.", result.program.name);
    return result;
}

export function reasonMessage(reason, serverMessage) {
    if (serverMessage) {
        return serverMessage;
    }
    const messages = {
        tier_excluded: _t("This card tier is not included in the programme."),
        below_minimum: _t("The purchase is below the programme minimum."),
        quota_exhausted: _t("The programme quota has run out. The sale can continue without the discount."),
        card_used_today: _t("This card has already been used today."),
        bin_not_registered: _t("This card prefix is not registered in any active programme."),
        offline: _t("The point of sale is offline, so the shared quota cannot be checked."),
    };
    return messages[reason] || _t("This card is not eligible for a programme discount.");
}
