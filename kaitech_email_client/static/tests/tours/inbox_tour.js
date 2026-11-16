import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("kaitech_email_client_inbox", {
    steps: () => [
        {
            content: "The team conversation is listed",
            trigger: ".o_umc_row:contains(Quotation 50 units)",
            run: "click",
        },
        {
            content: "It opens in the reading pane",
            trigger: ".o_umc_reader_head h4:contains(Quotation 50 units)",
        },
        {
            content: "Open the tag picker",
            trigger: ".o_umc_tag_edit",
            run: "click",
        },
        {
            content: "Name a new tag",
            trigger: ".o_umc_new_tag",
            run: "edit Waiting",
        },
        {
            content: "Add it",
            trigger: ".o_umc_tag_picker button:contains(Add)",
            run: "click",
        },
        {
            content: "The conversation carries the tag",
            trigger: ".o_umc_reader_head .o_umc_tags .o_umc_tag:contains(Waiting)",
        },
        {
            content: "Open the record picker",
            trigger: ".o_umc_link_btn",
            run: "click",
        },
        {
            content: "Search the contact",
            trigger: ".o_umc_link_query",
            run: "edit Acme",
        },
        {
            content: "Link the conversation to it",
            trigger: ".o_umc_link_result:contains(Acme Trading)",
            run: "click",
        },
        {
            content: "The link is shown as primary",
            trigger: ".o_umc_link:contains(Acme Trading):contains(Primary)",
        },
        {
            content: "Starred filter",
            trigger: ".o_umc_filters button[data-filter='starred']",
            run: "click",
        },
        {
            content: "Nothing is starred",
            trigger: ".o_umc_list:contains(No conversations here.)",
        },
        {
            content: "Filter by the new tag from the sidebar",
            trigger: ".o_umc_tag_nav:contains(Waiting)",
            run: "click",
        },
        {
            content: "Back to every message of the tag",
            trigger: ".o_umc_filters button[data-filter='all']",
            run: "click",
        },
        {
            content: "The tagged conversation is listed",
            trigger: ".o_umc_row:contains(Quotation 50 units)",
            run: "click",
        },
        {
            content: "Reply",
            trigger: ".o_umc_reader_head button:contains(Reply):not(:contains(all))",
            run: "click",
        },
        {
            content: "The reply logs on the linked contact and carries the default signature",
            trigger: ".modal .o_field_widget[name='link_ref'] input:value(Acme Trading)",
        },
        {
            content: "Signature",
            trigger: ".modal .o_field_widget[name='body'] .umc-signature:contains(Reply Person)",
        },
        {
            content: "Discard",
            trigger: ".modal button:contains(Discard)",
            run: "click",
        },
        {
            content: "Dialog closed",
            trigger: ".o_umc_inbox:not(:has(.modal))",
        },
    ],
});
