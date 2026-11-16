from odoo import fields, models


class UmcTag(models.Model):
    _name = "umc.tag"
    _description = "Email Client - tag"
    _order = "name"

    name = fields.Char(required=True)
    color = fields.Integer()

    _name_uniq = models.Constraint("UNIQUE(name)", "A tag with this name already exists.")
