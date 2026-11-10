import { registry } from "@web/core/registry";
import { Base } from "@point_of_sale/app/models/related_models";

export class PosBankCardProgram extends Base {
    static pythonModel = "pos.bank.card.program";
}

export class PosBankCardBinRange extends Base {
    static pythonModel = "pos.bank.card.bin.range";
}

registry.category("pos_available_models").add(PosBankCardProgram.pythonModel, PosBankCardProgram);
registry.category("pos_available_models").add(PosBankCardBinRange.pythonModel, PosBankCardBinRange);
