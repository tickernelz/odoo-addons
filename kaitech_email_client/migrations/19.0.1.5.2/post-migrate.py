from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    boxes = env["umc.mailbox"].with_context(active_test=False).search([])
    env.add_to_compute(boxes._fields["user_ids"], boxes)
    env.flush_all()
