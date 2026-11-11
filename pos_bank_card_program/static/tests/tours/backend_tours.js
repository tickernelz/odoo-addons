import { registry } from "@web/core/registry";

function pickBank(name) {
    return [
        {
            content: `type ${name}`,
            trigger: ".modal .o_field_widget[name=bank_id] input",
            run: `edit ${name}`,
        },
        {
            content: `choose ${name}`,
            trigger: `.o-autocomplete--dropdown-item:contains(${name})`,
            run: "click",
        },
        {
            content: `${name} is set`,
            trigger: `.modal .o_field_widget[name=bank_id] input:value(${name})`,
        },
    ];
}

function labelsBesideValues() {
    for (const label of document.querySelectorAll(".modal .o_inner_group .o_cell label")) {
        const value = label.closest(".o_cell").nextElementSibling;
        const labelBox = label.getBoundingClientRect();
        const valueBox = value.getBoundingClientRect();
        if (Math.abs(labelBox.top - valueBox.top) > 8 || labelBox.right > valueBox.left) {
            throw new Error(`"${label.textContent.trim()}" is not on the row of its value`);
        }
    }
}

registry.category("web_tour.tours").add("pos_bank_card_program_claim_wizard_tour", {
    steps: () => [
        ...pickBank("Bank B"),
        {
            content: "preview a bank with nothing to claim",
            trigger: ".modal button[name=action_preview]",
            run: "click",
        },
        {
            content: "the empty result is explained",
            trigger: ".modal .alert-info:contains(No unclaimed confirmed usage)",
        },
        {
            content: "nothing to claim or print",
            trigger:
                ".modal footer:has(button[name=action_back]):not(:has(button[name=action_create_claim])):not(:has(button[name=action_print_pdf]))",
        },
        {
            content: "no outlet list without rows",
            trigger: ".modal:not(:has(.o_field_widget[name=line_ids]))",
        },
        {
            content: "go back",
            trigger: ".modal button[name=action_back]",
            run: "click",
        },
        ...pickBank("Bank A"),
        {
            content: "preview a bank with usage",
            trigger: ".modal button[name=action_preview]",
            run: "click",
        },
        {
            content: "the outlet row is listed",
            trigger: ".modal .o_field_widget[name=line_ids] .o_data_row:contains(Outlet 1)",
        },
        {
            content: "the claim can be created and printed",
            trigger:
                ".modal footer:has(button[name=action_create_claim]):has(button[name=action_print_pdf]):not(:has(.alert-info))",
        },
        {
            content: "the outlet list spans the dialog",
            trigger: ".modal .o_field_widget[name=line_ids]",
            run() {
                const list = document.querySelector(".modal .o_field_widget[name=line_ids]");
                const body = document.querySelector(".modal .modal-body");
                if (list.getBoundingClientRect().width < body.clientWidth * 0.9) {
                    throw new Error("the outlet list does not span the dialog");
                }
            },
        },
        {
            content: "every total sits beside its label",
            trigger: ".modal .oe_subtotal_footer .o_field_widget[name=total_bank_share_amount]",
            run: labelsBesideValues,
        },
    ],
});

registry.category("web_tour.tours").add("pos_bank_card_program_dashboard_tour", {
    steps: () => [
        {
            content: "the bank share card shows the usage of the period",
            trigger: ".o_bank_card_kpi[data-kpi=bank_share]:contains(100,000.00)",
        },
        {
            content: "the daily chart is drawn",
            trigger: ".o_bank_card_dashboard canvas",
            run() {
                const canvas = document.querySelector(".o_bank_card_dashboard canvas");
                if (!canvas.width || !canvas.height) {
                    throw new Error("the daily chart has no size");
                }
            },
        },
        {
            content: "the quota of the programme is listed with its limit",
            trigger: ".o_bank_card_quotas div.mb-3:contains(Bank A Programme):contains(0 / 10):has(.progress)",
        },
        {
            content: "the outlet is ranked",
            trigger: ".o_bank_card_outlets tbody tr:contains(Outlet 1)",
        },
        {
            content: "filter on a bank without usage",
            trigger: "#bank_card_dashboard_bank",
            run: "selectByLabel Bank B",
        },
        {
            content: "the outlet list explains the empty period",
            trigger: ".o_bank_card_outlets:contains(No card usage in this period.)",
        },
    ],
});
