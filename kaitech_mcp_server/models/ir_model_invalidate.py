"""
Cache invalidation hooks for MCP schema cache.

When ir.model or ir.model.fields records are created, updated, or deleted
(e.g. a module is installed/upgraded or a custom field is added via Studio),
the schema cache must be cleared so the next MCP request reflects current state.

Using broad invalidation (clear everything) is intentional — partial
invalidation would require tracking per-model dependencies in the cache key,
and model/field changes are rare compared to the benefit of not serving stale
schema data.
"""

import logging

from odoo import models

from ..services import schema_cache

_logger = logging.getLogger(__name__)


def _invalidate_all(label: str) -> None:
    count = schema_cache.invalidate()
    if count:
        _logger.debug("MCP schema cache: cleared %d entries after %s", count, label)


class IrModelInvalidate(models.Model):
    _inherit = "ir.model"

    def create(self, vals_list):
        result = super().create(vals_list)
        _invalidate_all("ir.model create")
        return result

    def write(self, vals):
        result = super().write(vals)
        _invalidate_all("ir.model write")
        return result

    def unlink(self):
        result = super().unlink()
        _invalidate_all("ir.model unlink")
        return result


class IrModelFieldsInvalidate(models.Model):
    _inherit = "ir.model.fields"

    def create(self, vals_list):
        result = super().create(vals_list)
        _invalidate_all("ir.model.fields create")
        return result

    def write(self, vals):
        result = super().write(vals)
        _invalidate_all("ir.model.fields write")
        return result

    def unlink(self):
        result = super().unlink()
        _invalidate_all("ir.model.fields unlink")
        return result
