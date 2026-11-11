import { registry } from "@web/core/registry";
import { Base } from "@point_of_sale/app/models/related_models";

export class KaiPosPromo extends Base {
    static pythonModel = "kai.pos.promo";

    get kaiTiers() {
        return [...this.tier_ids].sort((a, b) => a.threshold - b.threshold);
    }
}

export class KaiPosPromoTier extends Base {
    static pythonModel = "kai.pos.promo.tier";
}

export class KaiPosPromoSchedule extends Base {
    static pythonModel = "kai.pos.promo.schedule";
}

for (const model of [KaiPosPromo, KaiPosPromoTier, KaiPosPromoSchedule]) {
    registry.category("pos_available_models").add(model.pythonModel, model);
}
