import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("kaitech_email_client_own_mailbox", {
    steps: () => [
        {
            content: "The owner's personal mailbox is in the sidebar",
            trigger: ".o_umc_sidebar .o_umc_box:contains(Owner Web)",
        },
        {
            content: "Its conversation is listed",
            trigger: ".o_umc_row:contains(Lab result)",
            run: "click",
        },
        {
            content: "It opens in the reading pane",
            trigger: ".o_umc_reader_head h4:contains(Lab result)",
        },
    ],
});

registry.category("web_tour.tours").add("kaitech_email_client_no_foreign_mailbox", {
    steps: () => [
        {
            content: "No mailbox is offered to someone who owns none and is assigned to none",
            trigger: ".o_umc_sidebar:contains(No mailbox yet.):not(:has(.o_umc_box))",
        },
        {
            content: "No conversation of another person's mailbox is listed",
            trigger: ".o_umc_list:contains(No conversations here.):not(:has(.o_umc_row))",
        },
    ],
});
