{
    "name": "Kaitech Email Client",
    "version": "19.0.1.5.0",
    "summary": "Email client inside Odoo: Gmail, Microsoft 365, IMAP and SMTP in one unified inbox, "
               "team mailboxes with permissions, conversations linked to contacts and records",
    "description": """
Kaitech Email Client
====================
Unified inbox inside the Odoo backend, derived from "Email Client for Odoo Full"
19.0.1.4.1 by New Plain (LGPL-3). See README.md for what Kaitech changed.
""",
    "author": "New Plain, Kaitech",
    "website": "https://kaitech.io",
    "category": "Productivity/Discuss",
    "license": "LGPL-3",
    "depends": ["base_setup", "mail"],
    "external_dependencies": {"python": ["cryptography", "requests"]},
    "data": [
        "security/ir.model.access.csv",
        "security/umc_rules.xml",
        "data/umc_cron.xml",
        "views/res_config_settings_views.xml",
        "views/umc_mailbox_views.xml",
        "views/umc_inbox_views.xml",
        "views/umc_signature_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "kaitech_email_client/static/src/**/*",
        ],
        "web.assets_tests": [
            "kaitech_email_client/static/tests/tours/inbox_tour.js",
        ],
    },
    "images": ["static/description/banner.png"],
    "uninstall_hook": "uninstall_hook",
    "application": True,
    "installable": True,
}
