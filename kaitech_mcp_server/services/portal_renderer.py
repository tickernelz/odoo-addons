# -*- coding: utf-8 -*-
# Copyright 2026 kaitech.io
# Part of kaitech.io. See LICENSE file for full copyright and licensing details.
"""
Portal page renderer — pure functions that convert widget specs into display-ready dicts.

Each public function accepts:
  - env:            an Odoo Environment (may be with_user() for governance)
  - widget_spec:    a single widget dict from the page spec
  - filter_values:  dict of filter name → value from query string

Return shape per widget type:

  KPI:
    {
      "type": "kpi",
      "title": str,
      "value": float|int,
      "formatted": str,        # e.g. "$1,234.56"
      "icon": str,             # fa-* class
      "color": str,            # hex color
      "error": str|None,       # set if computation failed
    }

  Chart:
    {
      "type": "chart",
      "title": str,
      "chart_type": str,
      "echarts_option": dict,  # full ECharts option, JSON-safe
      "error": str|None,
    }

  Table:
    {
      "type": "table",
      "title": str,
      "headers": list[str],
      "rows": list[list],
      "error": str|None,
    }
"""

import logging
import re

_logger = logging.getLogger(__name__)

_FILTER_PLACEHOLDER_RE = re.compile(r"\{\{filters\.(\w+)\}\}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_widget(env, widget_spec: dict, filter_values: dict) -> dict:
    """Compute render data for a single widget.

    Never raises — errors are captured in result["error"] so the page can
    still render the other widgets.
    """
    wtype = widget_spec.get("type", "")
    try:
        resolved_spec = _substitute_filters(widget_spec, filter_values)
        if wtype == "kpi":
            return _compute_kpi(env, resolved_spec)
        if wtype == "chart":
            return _compute_chart(env, resolved_spec)
        if wtype == "table":
            return _compute_table(env, resolved_spec)
        return {"type": wtype, "title": widget_spec.get("title", ""), "error": f"Unknown widget type: {wtype!r}"}
    except Exception as exc:
        _logger.warning("portal_renderer: widget %r failed: %s", widget_spec.get("title"), exc, exc_info=True)
        return {
            "type": wtype,
            "title": widget_spec.get("title", ""),
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# KPI
# ---------------------------------------------------------------------------


def _compute_kpi(env, spec: dict) -> dict:
    model_name = spec["model"]
    field = spec["field"]
    domain = spec.get("domain") or []
    aggregate = spec.get("aggregate", "sum")
    fmt = spec.get("format", "number")
    currency_code = spec.get("currency", "")

    _check_model(env, model_name)
    value = _aggregate_field(env, model_name, field, domain, aggregate)

    return {
        "type": "kpi",
        "title": spec.get("title", ""),
        "value": value,
        "formatted": _format_value(value, fmt, currency_code, env),
        "icon": spec.get("icon", "fa-bar-chart"),
        "color": spec.get("color", "#6366f1"),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------


def _compute_chart(env, spec: dict) -> dict:
    model_name = spec["model"]
    domain = spec.get("domain") or []
    groupby = spec.get("groupby") or []
    agg_fields = spec.get("fields") or []
    chart_type = spec.get("chart_type", "bar")
    limit = int(spec.get("limit", 50))

    _check_model(env, model_name)

    rows = _run_read_group(env, model_name, domain, groupby, agg_fields, limit=limit)
    echarts_option = _build_echarts_option(chart_type, groupby, agg_fields, rows, spec)

    return {
        "type": "chart",
        "title": spec.get("title", ""),
        "chart_type": chart_type,
        "echarts_option": echarts_option,
        "error": None,
    }


def _build_echarts_option(chart_type: str, groupby: list, agg_fields: list, rows: list, spec: dict) -> dict:
    """Build a full ECharts option dict from grouped data rows."""
    label_key = groupby[0].split(":")[0] if groupby else None
    value_key = agg_fields[0].split(":")[0] if agg_fields else None

    labels = []
    values = []
    for row in rows:
        raw_label = row.get(label_key) if label_key else ""
        if isinstance(raw_label, dict):
            raw_label = raw_label.get("display_name") or str(raw_label)
        elif raw_label is None:
            raw_label = "(none)"
        labels.append(str(raw_label))

        raw_val = row.get(value_key, 0) if value_key else 0
        values.append(float(raw_val) if raw_val is not None else 0.0)

    color = spec.get("color", "#6366f1")

    if chart_type in ("bar",):
        return {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": labels, "axisLabel": {"rotate": 30}},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "data": values, "itemStyle": {"color": color}}],
        }

    if chart_type == "line":
        return {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": labels},
            "yAxis": {"type": "value"},
            "series": [{"type": "line", "data": values, "smooth": True, "itemStyle": {"color": color}}],
        }

    if chart_type == "area":
        return {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": labels},
            "yAxis": {"type": "value"},
            "series": [{
                "type": "line",
                "data": values,
                "smooth": True,
                "areaStyle": {},
                "itemStyle": {"color": color},
            }],
        }

    if chart_type == "pie":
        return {
            "tooltip": {"trigger": "item"},
            "legend": {"orient": "vertical", "left": "left"},
            "series": [{
                "type": "pie",
                "radius": "65%",
                "data": [{"name": l, "value": v} for l, v in zip(labels, values)],
                "emphasis": {"itemStyle": {"shadowBlur": 10, "shadowOffsetX": 0, "shadowColor": "rgba(0,0,0,0.5)"}},
            }],
        }

    if chart_type == "donut":
        return {
            "tooltip": {"trigger": "item"},
            "legend": {"orient": "vertical", "left": "left"},
            "series": [{
                "type": "pie",
                "radius": ["40%", "70%"],
                "data": [{"name": l, "value": v} for l, v in zip(labels, values)],
                "emphasis": {"itemStyle": {"shadowBlur": 10, "shadowOffsetX": 0, "shadowColor": "rgba(0,0,0,0.5)"}},
            }],
        }

    # Fallback
    return {
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value"},
        "series": [{"type": "bar", "data": values}],
    }


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


def _compute_table(env, spec: dict) -> dict:
    model_name = spec["model"]
    domain = spec.get("domain") or []
    groupby = spec.get("groupby") or []
    agg_fields = spec.get("fields") or []
    orderby = spec.get("orderby", "")
    limit = int(spec.get("limit", 20))

    _check_model(env, model_name)

    # When there is no groupby, use search_read (plain list — no aggregates needed).
    # When groupby is present, use _read_group (aggregated rows).
    if not groupby:
        plain_fields = [f.split(":")[0] for f in agg_fields]
        kw = {"limit": limit}
        if orderby:
            kw["order"] = orderby
        records = env[model_name].search_read(domain, plain_fields, **kw)

        headers = [f.replace("_", " ").title() for f in plain_fields]
        table_rows = []
        for rec in records:
            row = []
            for key in plain_fields:
                val = rec.get(key)
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    # Many2one returns [id, display_name]
                    val = val[1]
                elif val is None or val is False:
                    val = ""
                elif isinstance(val, float):
                    val = round(val, 2)
                row.append(val)
            table_rows.append(row)
    else:
        rows = _run_read_group(env, model_name, domain, groupby, agg_fields, limit=limit, orderby=orderby)

        headers = []
        for gb in groupby:
            headers.append(gb.split(":")[0].replace("_", " ").title())
        for af in agg_fields:
            parts = af.split(":")
            label = parts[0].replace("_", " ").title()
            if len(parts) > 1:
                label = f"{label} ({parts[1].upper()})"
            headers.append(label)

        col_keys = [gb.split(":")[0] for gb in groupby] + [af.split(":")[0] for af in agg_fields]
        table_rows = []
        for row in rows:
            table_row = []
            for key in col_keys:
                val = row.get(key)
                if isinstance(val, dict):
                    val = val.get("display_name") or str(val)
                elif val is None:
                    val = ""
                elif isinstance(val, float):
                    val = round(val, 2)
                table_row.append(val)
            table_rows.append(table_row)

    return {
        "type": "table",
        "title": spec.get("title", ""),
        "headers": headers,
        "rows": table_rows,
        "error": None,
    }


# ---------------------------------------------------------------------------
# ORM helpers — these use _read_group for aggregation (no Python sum loops)
# ---------------------------------------------------------------------------


def _aggregate_field(env, model_name: str, field: str, domain: list, aggregate: str) -> float:
    """Return a single aggregate value for *field* on *model_name*."""
    agg_expr = f"{field}:{aggregate}"
    rows = env[model_name]._read_group(
        domain=domain,
        groupby=[],
        aggregates=[agg_expr],
    )
    if not rows:
        return 0.0
    row = rows[0]
    val = row[0] if row else 0
    return float(val) if val is not None else 0.0


def _run_read_group(env, model_name: str, domain: list, groupby: list, agg_fields: list,
                    limit: int = 80, orderby: str = "") -> list:
    """Run _read_group and return a list of serializable dicts."""
    kw = {"limit": limit}
    if orderby:
        kw["orderby"] = orderby

    rows = env[model_name]._read_group(
        domain=domain,
        groupby=groupby,
        aggregates=agg_fields,
        **kw,
    )

    result = []
    gb_keys = [g.split(":")[0] for g in groupby]
    agg_keys = [a.split(":")[0] for a in agg_fields]

    for row in rows:
        row_dict = {}
        for i, key in enumerate(gb_keys):
            val = row[i]
            if hasattr(val, "id"):
                row_dict[key] = {"id": val.id, "display_name": str(val)}
            else:
                row_dict[key] = val
        for j, key in enumerate(agg_keys):
            raw = row[len(gb_keys) + j]
            row_dict[key] = float(raw) if isinstance(raw, (int, float)) and raw is not None else raw
        result.append(row_dict)

    return result


# ---------------------------------------------------------------------------
# Filter substitution
# ---------------------------------------------------------------------------


def _substitute_filters(spec: dict, filter_values: dict) -> dict:
    """Return a deep copy of *spec* with {{filters.<name>}} placeholders replaced."""
    if not filter_values:
        return spec
    import copy
    return _deep_substitute(copy.deepcopy(spec), filter_values)


def _deep_substitute(obj, filter_values: dict):
    if isinstance(obj, str):
        def _replacer(m):
            return filter_values.get(m.group(1), m.group(0))
        return _FILTER_PLACEHOLDER_RE.sub(_replacer, obj)
    if isinstance(obj, list):
        return [_deep_substitute(item, filter_values) for item in obj]
    if isinstance(obj, dict):
        return {k: _deep_substitute(v, filter_values) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_value(value: float, fmt: str, currency_code: str, env) -> str:
    """Format a numeric value as a human-readable string."""
    if fmt == "integer":
        return f"{int(value):,}"
    if fmt == "percent":
        return f"{value:.1f}%"
    if fmt == "currency":
        symbol = _get_currency_symbol(env, currency_code)
        return f"{symbol}{value:,.2f}"
    # default: number
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _get_currency_symbol(env, currency_code: str) -> str:
    """Return the symbol for *currency_code*, defaulting to company currency."""
    try:
        if currency_code:
            cur = env["res.currency"].search([("name", "=", currency_code)], limit=1)
            if cur:
                return cur.symbol or currency_code
        company_cur = env.company.currency_id
        return company_cur.symbol if company_cur else "$"
    except Exception:
        return "$"


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def _check_model(env, model_name: str) -> None:
    """Raise ValueError if *model_name* is not in the registry."""
    if model_name not in env.registry:
        raise ValueError(f"Model {model_name!r} not found in Odoo registry.")
