"""
BI Reporting Tools for the MCP Server.

Provides 7 advanced analytics tools that AI clients can call in a single
request to get sophisticated pivot tables, time series, rankings, cohort
analysis, funnel conversions, and data exports (CSV/XLSX).

All tools are READ-ONLY and safe under 'read' scope.
All tools enforce model and field restrictions from env.context['mcp_restrictions'].

Consolidation agent: merge TOOL_DEFINITIONS into mcp_tool_registry._TOOL_DEFINITIONS
and TOOL_HANDLERS into the handlers dict in tool_executor.execute_tool.
"""

import base64
import csv
import datetime
import io
import logging

from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Governance helpers
# ---------------------------------------------------------------------------


def _check_model_access(env, model_name: str) -> None:
    """Raise PermissionError if model is blocked by token restrictions."""
    restrictions = env.context.get("mcp_restrictions")
    if not restrictions:
        return

    denied_models = restrictions.get("denied_models") or set()
    allowed_models = restrictions.get("allowed_models") or set()

    if denied_models and model_name in denied_models:
        raise PermissionError(
            f"Access to model {model_name!r} is denied for this token."
        )
    if allowed_models and model_name not in allowed_models:
        raise PermissionError(
            f"Access to model {model_name!r} is not permitted by this token's model allowlist."
        )


def _filter_fields(env, model_name: str, requested_fields: list) -> list:
    """Return filtered list of fields based on token's field_restrictions.

    If the token has no field restrictions for this model, the original list
    is returned unchanged. Disallowed fields are silently dropped (BI tools
    are read-only).
    """
    restrictions = env.context.get("mcp_restrictions")
    if not restrictions:
        return requested_fields
    field_map = restrictions.get("field_restrictions") or {}
    allowed = field_map.get(model_name)
    if allowed is None:
        return requested_fields
    return [f for f in requested_fields if f in allowed]


def _resolve_model(env, model_name: str):
    """Validate model exists in registry, return env[model_name].

    Raises ValueError with a descriptive message when not found.
    """
    if model_name not in env.registry:
        raise ValueError(
            f"Model {model_name!r} not found in Odoo registry. "
            f"Use odoo_get_models to list available models."
        )
    return env[model_name]


def _validate_field_exists(model, model_name: str, field_name: str) -> None:
    """Raise ValueError if *field_name* does not exist on *model*."""
    bare = field_name.split(":")[0]
    if bare not in model._fields:
        raise ValueError(
            f"Field {bare!r} does not exist on model {model_name!r}. "
            f"Use odoo_fields_get(model='{model_name}') to see available fields."
        )


# ---------------------------------------------------------------------------
# Serialization helpers (mirrors tool_executor.odoo_json_default for tuples)
# ---------------------------------------------------------------------------


def _serialize_value(val):
    """Serialize a single ORM value to a JSON-safe type."""
    if hasattr(val, "_name") and hasattr(val, "ids"):
        if len(val) == 1:
            return {"id": val.id, "display_name": str(val.display_name)}
        return [{"id": r.id, "display_name": str(r.display_name)} for r in val]
    if isinstance(val, datetime.datetime):
        return val.isoformat(sep="T", timespec="seconds")
    if isinstance(val, datetime.date):
        return val.isoformat()
    return val


def _serialize_group_row(row, groupby: list, aggregates: list) -> dict:
    """Convert a _read_group result tuple into a plain dict."""
    result = {}
    for i, key in enumerate(groupby):
        field_name = key.split(":")[0]
        result[field_name] = _serialize_value(row[i])
    for j, agg in enumerate(aggregates):
        field_name = agg.split(":")[0]
        val = row[len(groupby) + j]
        result[field_name] = _serialize_value(val)
    return result


def _row_key(row_dict: dict, key_fields: list) -> str:
    """Build a stable string key from selected fields of a serialized row dict."""
    parts = []
    for f in key_fields:
        bare = f.split(":")[0]
        val = row_dict.get(bare)
        if isinstance(val, dict):
            parts.append(str(val.get("id", val.get("display_name", ""))))
        elif val is None:
            parts.append("")
        else:
            parts.append(str(val))
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Date / interval helpers
# ---------------------------------------------------------------------------

_INTERVAL_STEP = {
    "day": relativedelta(days=1),
    "week": relativedelta(weeks=1),
    "month": relativedelta(months=1),
    "quarter": relativedelta(months=3),
    "year": relativedelta(years=1),
}

_DATE_FORMAT = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
    "quarter": None,  # handled separately
    "year": "%Y",
}


def _parse_date_str(value) -> datetime.date | None:
    """Parse a date string or date/datetime object returned by _read_group."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    # String form returned by Odoo: "2026-01-01 00:00:00", "2026-01-01", "2026", "2026-01"...
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


def _format_date_for_interval(dt: datetime.date, interval: str) -> str:
    """Format a date for a given interval key."""
    if interval == "quarter":
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"
    fmt = _DATE_FORMAT.get(interval, "%Y-%m-%d")
    return dt.strftime(fmt)


def _floor_date_to_interval(dt: datetime.date, interval: str) -> datetime.date:
    """Truncate *dt* to the start of its interval period."""
    if interval == "day":
        return dt
    if interval == "week":
        return dt - datetime.timedelta(days=dt.weekday())
    if interval == "month":
        return dt.replace(day=1)
    if interval == "quarter":
        month = ((dt.month - 1) // 3) * 3 + 1
        return dt.replace(month=month, day=1)
    if interval == "year":
        return dt.replace(month=1, day=1)
    return dt


def _date_range(start: datetime.date, end: datetime.date, interval: str) -> list[datetime.date]:
    """Generate all period-start dates from *start* to *end* inclusive."""
    step = _INTERVAL_STEP.get(interval)
    if not step:
        return []
    dates = []
    current = _floor_date_to_interval(start, interval)
    end_floor = _floor_date_to_interval(end, interval)
    while current <= end_floor:
        dates.append(current)
        current = (datetime.datetime.combine(current, datetime.time.min) + step).date()
    return dates


# ---------------------------------------------------------------------------
# Tool 1: odoo_pivot
# ---------------------------------------------------------------------------


def _odoo_pivot(env, args: dict):
    """Pivot-table aggregation across row x column dimensions."""
    model_name = args["model"]
    _check_model_access(env, model_name)
    model = _resolve_model(env, model_name)

    domain = args.get("domain") or []
    row_fields = list(args["row_fields"])
    col_fields = list(args["col_fields"])
    measures_input = args["measures"]
    limit = int(args.get("limit") or 1000)
    orderby = args.get("orderby")

    # Validate measure fields exist
    for m in measures_input:
        _validate_field_exists(model, model_name, m["field"])

    # Build aggregates list for _read_group
    agg_map = {}  # measure_key -> (field, aggregate)
    aggregates = []
    for m in measures_input:
        field = m["field"]
        agg = m.get("aggregate", "sum")
        key = f"{field}_{agg}"
        agg_map[key] = (field, agg)
        aggregates.append(f"{field}:{agg}")

    groupby = row_fields + col_fields

    kw = {"limit": limit}
    if orderby:
        kw["orderby"] = orderby

    rows_raw = model._read_group(
        domain=domain,
        groupby=groupby,
        aggregates=aggregates,
        **kw,
    )

    # Serialize rows and collect unique row/column keys
    unique_rows: dict[str, dict] = {}
    unique_cols: dict[str, dict] = {}
    cells: dict[str, dict] = {}

    for raw_row in rows_raw:
        serialized = _serialize_group_row(raw_row, groupby, aggregates)
        rk = _row_key(serialized, row_fields)
        ck = _row_key(serialized, col_fields)
        cell_key = f"{rk}|{ck}"

        # Capture dimension labels
        if rk not in unique_rows:
            unique_rows[rk] = {f.split(":")[0]: serialized[f.split(":")[0]] for f in row_fields}
        if ck not in unique_cols:
            unique_cols[ck] = {f.split(":")[0]: serialized[f.split(":")[0]] for f in col_fields}

        # Capture measure values for cell
        cell_values = {}
        for i, m in enumerate(measures_input):
            field = m["field"]
            agg = m.get("aggregate", "sum")
            cell_values[f"{field}_{agg}"] = serialized[field]
        cells[cell_key] = cell_values

    # Compute row totals (sum over all columns for each row)
    row_totals: dict[str, dict] = {}
    for rk in unique_rows:
        totals = {}
        for i, m in enumerate(measures_input):
            mkey = f"{m['field']}_{m.get('aggregate', 'sum')}"
            vals = [
                cells[f"{rk}|{ck}"][mkey]
                for ck in unique_cols
                if f"{rk}|{ck}" in cells and isinstance(cells[f"{rk}|{ck}"][mkey], (int, float))
            ]
            totals[mkey] = sum(vals)
        row_totals[rk] = totals

    # Compute column totals (sum over all rows for each column)
    col_totals: dict[str, dict] = {}
    for ck in unique_cols:
        totals = {}
        for i, m in enumerate(measures_input):
            mkey = f"{m['field']}_{m.get('aggregate', 'sum')}"
            vals = [
                cells[f"{rk}|{ck}"][mkey]
                for rk in unique_rows
                if f"{rk}|{ck}" in cells and isinstance(cells[f"{rk}|{ck}"][mkey], (int, float))
            ]
            totals[mkey] = sum(vals)
        col_totals[ck] = totals

    # Grand total
    grand_total: dict[str, float] = {}
    for m in measures_input:
        mkey = f"{m['field']}_{m.get('aggregate', 'sum')}"
        grand_total[mkey] = sum(
            v[mkey] for v in row_totals.values() if isinstance(v.get(mkey), (int, float))
        )

    return {
        "model": model_name,
        "row_fields": row_fields,
        "col_fields": col_fields,
        "measures": [{
            "key": f"{m['field']}_{m.get('aggregate', 'sum')}",
            "field": m["field"],
            "aggregate": m.get("aggregate", "sum"),
        } for m in measures_input],
        "rows": [{"key": k, "dimensions": v} for k, v in unique_rows.items()],
        "columns": [{"key": k, "dimensions": v} for k, v in unique_cols.items()],
        "cells": cells,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "grand_total": grand_total,
    }


# ---------------------------------------------------------------------------
# Tool 2: odoo_time_series
# ---------------------------------------------------------------------------


def _odoo_time_series(env, args: dict):
    """Time series aggregation with optional gap filling."""
    model_name = args["model"]
    _check_model_access(env, model_name)
    model = _resolve_model(env, model_name)

    domain = args.get("domain") or []
    date_field = args["date_field"]
    interval = args.get("interval", "month")
    measure = args["measure"]
    fill_gaps = args.get("fill_gaps", True)
    fill_value = args.get("fill_value", 0)
    date_from_str = args.get("date_from")
    date_to_str = args.get("date_to")

    if interval not in _INTERVAL_STEP:
        raise ValueError(
            f"Invalid interval {interval!r}. "
            f"Allowed: {sorted(_INTERVAL_STEP.keys())}"
        )

    _validate_field_exists(model, model_name, date_field)
    _validate_field_exists(model, model_name, measure["field"])

    agg_field = measure["field"]
    agg_func = measure.get("aggregate", "sum")
    groupby_key = f"{date_field}:{interval}"
    aggregate_expr = f"{agg_field}:{agg_func}"

    rows_raw = model._read_group(
        domain=domain,
        groupby=[groupby_key],
        aggregates=[aggregate_expr],
        limit=False,
    )

    data_points: dict[str, float] = {}
    data_dates: list[datetime.date] = []

    for raw_row in rows_raw:
        date_val = raw_row[0]
        measure_val = raw_row[1]
        dt = _parse_date_str(date_val)
        if dt is None:
            continue
        key = _format_date_for_interval(dt, interval)
        data_points[key] = measure_val if measure_val is not None else fill_value
        data_dates.append(dt)

    if not data_dates and not fill_gaps:
        return {
            "model": model_name,
            "date_field": date_field,
            "interval": interval,
            "measure": {"field": agg_field, "aggregate": agg_func},
            "series": [],
        }

    # Determine the full date range
    date_from = None
    date_to = None
    if date_from_str:
        date_from = _parse_date_str(date_from_str)
    if date_to_str:
        date_to = _parse_date_str(date_to_str)

    if data_dates:
        data_min = min(data_dates)
        data_max = max(data_dates)
        range_start = min(date_from, data_min) if date_from else data_min
        range_end = max(date_to, data_max) if date_to else data_max
    else:
        # fill_gaps=True but no data — use provided date range
        if date_from and date_to:
            range_start = date_from
            range_end = date_to
        else:
            return {
                "model": model_name,
                "date_field": date_field,
                "interval": interval,
                "measure": {"field": agg_field, "aggregate": agg_func},
                "series": [],
            }

    series = []
    if fill_gaps:
        for period_start in _date_range(range_start, range_end, interval):
            key = _format_date_for_interval(period_start, interval)
            series.append({
                "date": key,
                "value": data_points.get(key, fill_value),
            })
    else:
        # Return only dates that have data, sorted
        for period_start in _date_range(range_start, range_end, interval):
            key = _format_date_for_interval(period_start, interval)
            if key in data_points:
                series.append({"date": key, "value": data_points[key]})

    return {
        "model": model_name,
        "date_field": date_field,
        "interval": interval,
        "measure": {"field": agg_field, "aggregate": agg_func},
        "fill_gaps": fill_gaps,
        "fill_value": fill_value,
        "series": series,
    }


# ---------------------------------------------------------------------------
# Tool 3: odoo_top_n
# ---------------------------------------------------------------------------


def _odoo_top_n(env, args: dict):
    """Top-N ranking across a grouped dataset."""
    model_name = args["model"]
    _check_model_access(env, model_name)
    model = _resolve_model(env, model_name)

    domain = args.get("domain") or []
    group_field = args["group_field"]
    measure = args["measure"]
    n = int(args.get("n") or 10)
    ascending = bool(args.get("ascending", False))
    include_others = bool(args.get("include_others", True))

    _validate_field_exists(model, model_name, group_field)
    _validate_field_exists(model, model_name, measure["field"])

    agg_field = measure["field"]
    agg_func = measure.get("aggregate", "sum")
    aggregate_expr = f"{agg_field}:{agg_func}"

    # Fetch all groups — no limit here so we can compute "Others" accurately
    rows_raw = model._read_group(
        domain=domain,
        groupby=[group_field],
        aggregates=[aggregate_expr],
        limit=False,
    )

    # Serialize and sort
    all_rows = []
    for raw_row in rows_raw:
        dim_val = _serialize_value(raw_row[0])
        measure_val = raw_row[1]
        if measure_val is None:
            measure_val = 0
        # Extract label
        if isinstance(dim_val, dict):
            label = dim_val.get("display_name") or str(dim_val.get("id", ""))
            key = str(dim_val.get("id", label))
        elif dim_val is None:
            label = "(empty)"
            key = ""
        else:
            label = str(dim_val)
            key = label
        all_rows.append({"key": key, "label": label, "value": measure_val, "_dim": dim_val})

    all_rows.sort(key=lambda r: (r["value"] if r["value"] is not None else 0), reverse=not ascending)

    total = sum(r["value"] for r in all_rows if isinstance(r["value"], (int, float)))

    top_rows = all_rows[:n]
    rest_rows = all_rows[n:]

    def _enrich(row):
        pct = round(row["value"] / total * 100, 2) if total else 0
        return {
            "key": row["key"],
            "label": row["label"],
            "value": row["value"],
            "percent_of_total": pct,
        }

    result_rows = [_enrich(r) for r in top_rows]

    others = None
    if include_others and rest_rows:
        others_val = sum(r["value"] for r in rest_rows if isinstance(r["value"], (int, float)))
        others = {
            "count": len(rest_rows),
            "value": others_val,
            "percent": round(others_val / total * 100, 2) if total else 0,
        }

    return {
        "model": model_name,
        "group_field": group_field,
        "measure": {"field": agg_field, "aggregate": agg_func},
        "n": n,
        "ascending": ascending,
        "total": total,
        "rows": result_rows,
        "others": others,
    }


# ---------------------------------------------------------------------------
# Tool 4: odoo_cohort
# ---------------------------------------------------------------------------


def _odoo_cohort(env, args: dict):
    """Cohort retention analysis.

    Performance note: this tool loads individual record values for the
    cohort_field and activity_field. For large datasets (>50k records)
    consider adding an appropriate domain filter to scope the analysis.

    V1 limitation: cohort_field and activity_field must both be on the
    same model. Cross-model cohort analysis (e.g. partners + their orders)
    is a planned V2 feature.
    """
    model_name = args["model"]
    _check_model_access(env, model_name)
    model = _resolve_model(env, model_name)

    domain = args.get("domain") or []
    cohort_field = args["cohort_field"]
    activity_field = args["activity_field"]
    interval = args.get("interval", "month")
    periods = int(args.get("periods") or 12)
    measure_type = args.get("measure_type", "count_distinct")
    measure_field = args.get("measure_field")

    if interval not in _INTERVAL_STEP:
        raise ValueError(
            f"Invalid interval {interval!r}. "
            f"Allowed: {sorted(_INTERVAL_STEP.keys())}"
        )

    _validate_field_exists(model, model_name, cohort_field)
    _validate_field_exists(model, model_name, activity_field)
    if measure_field:
        _validate_field_exists(model, model_name, measure_field)

    # Determine which fields to read
    read_fields = list({cohort_field, activity_field})
    if measure_field and measure_field not in read_fields:
        read_fields.append(measure_field)

    # Apply field filter (silently drop disallowed fields)
    read_fields = _filter_fields(env, model_name, read_fields)
    if cohort_field not in read_fields or activity_field not in read_fields:
        raise PermissionError(
            f"cohort_field {cohort_field!r} or activity_field {activity_field!r} "
            f"is not accessible under this token's field restrictions."
        )

    # Load records — cap at 50000 to protect memory
    records = model.search_read(domain, read_fields, limit=50000, order=f"{cohort_field} asc")

    # Group records by cohort period
    cohort_map: dict[str, list] = {}
    for rec in records:
        cv = rec.get(cohort_field)
        av = rec.get(activity_field)
        cohort_dt = _parse_date_str(cv)
        if cohort_dt is None:
            continue
        ck = _format_date_for_interval(_floor_date_to_interval(cohort_dt, interval), interval)
        if ck not in cohort_map:
            cohort_map[ck] = []
        cohort_map[ck].append({
            "cohort_date": cohort_dt,
            "activity_date": _parse_date_str(av),
            "measure_val": rec.get(measure_field) if measure_field else None,
        })

    step = _INTERVAL_STEP[interval]
    cohorts_out = []

    for cohort_key in sorted(cohort_map.keys()):
        members = cohort_map[cohort_key]
        cohort_start = _floor_date_to_interval(members[0]["cohort_date"], interval)
        cohort_size = len(members)

        period_results = []
        for p in range(periods):
            # Period window: [cohort_start + p*step, cohort_start + (p+1)*step)
            window_start = (
                datetime.datetime.combine(cohort_start, datetime.time.min)
                + step * p
            ).date()
            window_end = (
                datetime.datetime.combine(cohort_start, datetime.time.min)
                + step * (p + 1)
            ).date()

            if measure_type == "count_distinct":
                count = sum(
                    1 for m in members
                    if m["activity_date"] is not None
                    and window_start <= m["activity_date"] < window_end
                )
                value = count
            else:
                # sum of measure_field values
                value = sum(
                    (m["measure_val"] or 0) for m in members
                    if m["activity_date"] is not None
                    and window_start <= m["activity_date"] < window_end
                    and isinstance(m["measure_val"], (int, float))
                )

            percent = round(value / cohort_size * 100, 2) if cohort_size else 0
            period_results.append({
                "n": p,
                "period_label": _format_date_for_interval(window_start, interval),
                "value": value,
                "percent": percent,
            })

        cohorts_out.append({
            "cohort": cohort_key,
            "size": cohort_size,
            "periods": period_results,
        })

    return {
        "model": model_name,
        "cohort_field": cohort_field,
        "activity_field": activity_field,
        "interval": interval,
        "periods": periods,
        "measure_type": measure_type,
        "record_count_loaded": len(records),
        "note": (
            "V1: cohort_field and activity_field must be on the same model. "
            "Records capped at 50,000 — add domain filters for large datasets."
        ),
        "cohorts": cohorts_out,
    }


# ---------------------------------------------------------------------------
# Tool 5: odoo_funnel
# ---------------------------------------------------------------------------


def _odoo_funnel(env, args: dict):
    """Funnel conversion tracking across ordered stages."""
    model_name = args["model"]
    _check_model_access(env, model_name)
    model = _resolve_model(env, model_name)

    stages_input = args["stages"]
    measure_type = args.get("measure_type", "count")
    measure_field = args.get("measure_field")

    if not stages_input:
        raise ValueError("'stages' must contain at least one stage definition.")

    if measure_type == "sum":
        if not measure_field:
            raise ValueError("'measure_field' is required when measure_type is 'sum'.")
        _validate_field_exists(model, model_name, measure_field)

    stage_results = []
    for stage in stages_input:
        stage_name = stage.get("name", "")
        stage_domain = stage.get("domain") or []

        if measure_type == "count":
            value = model.search_count(stage_domain)
        else:
            # sum: use read_group to get total
            rows = model._read_group(
                domain=stage_domain,
                groupby=[],
                aggregates=[f"{measure_field}:sum"],
                limit=1,
            )
            if rows:
                value = rows[0][0] or 0
            else:
                value = 0

        stage_results.append({"name": stage_name, "value": value})

    # Compute conversion metrics
    first_value = stage_results[0]["value"] if stage_results else 0
    funnel_out = []
    prev_value = None

    for i, sr in enumerate(stage_results):
        val = sr["value"]
        absolute_conv = round(val / first_value * 100, 2) if first_value else 0
        if prev_value is None:
            relative_conv = 100.0
            drop_off = 0
        else:
            relative_conv = round(val / prev_value * 100, 2) if prev_value else 0
            drop_off = prev_value - val

        funnel_out.append({
            "name": sr["name"],
            "value": val,
            "absolute_conversion": absolute_conv,
            "relative_conversion": relative_conv,
            "drop_off": drop_off,
        })
        prev_value = val

    return {
        "model": model_name,
        "measure_type": measure_type,
        "measure_field": measure_field,
        "stages": funnel_out,
    }


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

_EXPORT_BATCH_SIZE = 1000
_EXPORT_MAX_LIMIT = 10000


def _read_records_batched(model, domain: list, fields: list, limit: int) -> list:
    """Read up to *limit* records in batches to avoid large memory spikes."""
    records = []
    offset = 0
    batch_size = min(_EXPORT_BATCH_SIZE, limit)

    while len(records) < limit:
        batch = model.search_read(
            domain, fields,
            limit=min(batch_size, limit - len(records)),
            offset=offset,
        )
        if not batch:
            break
        records.extend(batch)
        offset += len(batch)
        if len(batch) < batch_size:
            break

    return records


def _create_attachment(env, filename: str, content_bytes: bytes, mimetype: str) -> dict:
    """Create an ir.attachment from raw bytes. Returns a summary dict."""
    content_b64 = base64.b64encode(content_bytes).decode()
    attachment = env["ir.attachment"].create({
        "name": filename,
        "datas": content_b64,
        "mimetype": mimetype,
    })
    return {
        "attachment_id": attachment.id,
        "filename": attachment.name,
        "download_url": f"/web/content/{attachment.id}?download=1",
        "file_size_bytes": len(content_bytes),
    }


def _serialize_cell_for_export(val) -> str:
    """Convert any ORM value to a plain string suitable for CSV/XLSX export."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "True" if val else "False"
    if isinstance(val, datetime.datetime):
        return val.isoformat(sep="T", timespec="seconds")
    if isinstance(val, datetime.date):
        return val.isoformat()
    if isinstance(val, (list, tuple)) and len(val) == 2 and isinstance(val[0], int):
        # Many2one tuple (id, display_name)
        return str(val[1])
    if isinstance(val, list):
        return ";".join(str(v) for v in val)
    return str(val)


# ---------------------------------------------------------------------------
# Tool 6: odoo_export_csv
# ---------------------------------------------------------------------------


def _odoo_export_csv(env, args: dict):
    """Export records to CSV and store as ir.attachment."""
    model_name = args["model"]
    _check_model_access(env, model_name)
    model = _resolve_model(env, model_name)

    domain = args.get("domain") or []
    requested_fields = list(args.get("fields") or [])
    filename = args.get("filename") or f"{model_name.replace('.', '_')}_export.csv"
    limit = min(int(args.get("limit") or _EXPORT_MAX_LIMIT), _EXPORT_MAX_LIMIT)

    if not filename.lower().endswith(".csv"):
        filename += ".csv"

    # Validate and filter fields
    if not requested_fields:
        raise ValueError(
            "The 'fields' parameter is required for odoo_export_csv. "
            "Use odoo_fields_get to discover available fields, then specify which to export."
        )
    fields = _filter_fields(env, model_name, requested_fields)
    if not fields:
        raise PermissionError(
            f"None of the requested fields are accessible for model {model_name!r} "
            f"under this token's field restrictions."
        )

    records = _read_records_batched(model, domain, fields, limit)
    row_count = len(records)

    # Build CSV in memory
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(fields)
    for rec in records:
        writer.writerow([_serialize_cell_for_export(rec.get(f)) for f in fields])

    csv_bytes = buf.getvalue().encode("utf-8-sig")  # utf-8-sig for Excel BOM compatibility

    result = _create_attachment(env, filename, csv_bytes, "text/csv")
    result["row_count"] = row_count
    result["model"] = model_name
    result["fields_exported"] = fields
    return result


# ---------------------------------------------------------------------------
# Tool 7: odoo_export_xlsx
# ---------------------------------------------------------------------------


def _odoo_export_xlsx(env, args: dict):
    """Export records to XLSX and store as ir.attachment.

    Falls back to CSV if xlsxwriter is not installed, adding a 'warning' key
    to the response so the caller knows the format changed.
    """
    model_name = args["model"]
    _check_model_access(env, model_name)
    model = _resolve_model(env, model_name)

    domain = args.get("domain") or []
    requested_fields = list(args.get("fields") or [])
    filename = args.get("filename") or f"{model_name.replace('.', '_')}_export.xlsx"
    limit = min(int(args.get("limit") or _EXPORT_MAX_LIMIT), _EXPORT_MAX_LIMIT)
    sheet_name = args.get("sheet_name") or model_name[:31]  # Excel sheet name max 31 chars

    if not filename.lower().endswith(".xlsx"):
        filename += ".xlsx"

    if not requested_fields:
        raise ValueError(
            "The 'fields' parameter is required for odoo_export_xlsx. "
            "Use odoo_fields_get to discover available fields, then specify which to export."
        )
    fields = _filter_fields(env, model_name, requested_fields)
    if not fields:
        raise PermissionError(
            f"None of the requested fields are accessible for model {model_name!r} "
            f"under this token's field restrictions."
        )

    records = _read_records_batched(model, domain, fields, limit)
    row_count = len(records)

    # Try xlsxwriter; fall back to CSV on ImportError
    try:
        import xlsxwriter  # noqa: PLC0415 — lazy import intentional
    except ImportError:
        _logger.warning(
            "xlsxwriter not installed; odoo_export_xlsx falling back to CSV. "
            "Install xlsxwriter: pip install xlsxwriter"
        )
        # Rewrite filename to .csv and delegate to CSV builder
        csv_filename = filename.replace(".xlsx", ".csv")
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(fields)
        for rec in records:
            writer.writerow([_serialize_cell_for_export(rec.get(f)) for f in fields])
        csv_bytes = buf.getvalue().encode("utf-8-sig")
        result = _create_attachment(env, csv_filename, csv_bytes, "text/csv")
        result["row_count"] = row_count
        result["model"] = model_name
        result["fields_exported"] = fields
        result["warning"] = (
            "xlsxwriter is not installed on this Odoo server. "
            "The file was exported as CSV instead. "
            "Ask your administrator to run: pip install xlsxwriter"
        )
        return result

    # Build XLSX in memory
    buf = io.BytesIO()
    workbook = xlsxwriter.Workbook(buf, {"in_memory": True})
    worksheet = workbook.add_worksheet(sheet_name)

    # Formats
    header_fmt = workbook.add_format({
        "bold": True,
        "bg_color": "#F2F2F2",
        "border": 1,
        "text_wrap": True,
    })
    cell_fmt = workbook.add_format({"border": 0})

    # Header row
    for col_idx, field_name in enumerate(fields):
        worksheet.write(0, col_idx, field_name, header_fmt)

    # Freeze first row
    worksheet.freeze_panes(1, 0)

    # Data rows
    for row_idx, rec in enumerate(records, start=1):
        for col_idx, field_name in enumerate(fields):
            raw_val = rec.get(field_name)
            if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
                worksheet.write_number(row_idx, col_idx, raw_val, cell_fmt)
            else:
                worksheet.write(row_idx, col_idx, _serialize_cell_for_export(raw_val), cell_fmt)

    # Auto-fit columns (heuristic: max of header and first 20 rows)
    for col_idx, field_name in enumerate(fields):
        max_len = len(field_name)
        for rec in records[:20]:
            cell_str = _serialize_cell_for_export(rec.get(field_name))
            max_len = max(max_len, min(len(cell_str), 60))
        worksheet.set_column(col_idx, col_idx, max_len + 2)

    workbook.close()
    xlsx_bytes = buf.getvalue()

    result = _create_attachment(
        env, filename, xlsx_bytes,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    result["row_count"] = row_count
    result["model"] = model_name
    result["fields_exported"] = fields
    result["sheet_name"] = sheet_name
    return result


# ---------------------------------------------------------------------------
# TOOL_DEFINITIONS — MCP JSON Schema advertised to AI clients
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "odoo_pivot",
        "description": (
            "Create a pivot-table aggregation across two dimensions — like Excel or Google Sheets "
            "pivot on live Odoo data. "
            "Example: sales by salesperson (rows) x quarter (columns), measure = amount_total:sum. "
            "Returns rows (unique row dimension values), columns (unique column dimension values), "
            "cells keyed by 'row_key|col_key', plus row_totals, col_totals, and grand_total. "
            "Use odoo_fields_get first to find groupable and numeric fields."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name, e.g. 'sale.order'",
                },
                "domain": {
                    "type": "array",
                    "description": "Odoo domain filter, e.g. [['state','=','sale']]",
                    "default": [],
                },
                "row_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Fields to use as row dimension. Supports date granularity notation: "
                        "'date_order:month', 'date_order:quarter'. Min 1 field."
                    ),
                    "minItems": 1,
                },
                "col_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Fields to use as column dimension. Supports date granularity notation. "
                        "Min 1 field. For a single column grouping, e.g. ['date_order:quarter']."
                    ),
                    "minItems": 1,
                },
                "measures": {
                    "type": "array",
                    "description": "Measures to compute for each cell.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {
                                "type": "string",
                                "description": "Numeric field name, e.g. 'amount_total'",
                            },
                            "aggregate": {
                                "type": "string",
                                "enum": ["sum", "count", "avg", "min", "max"],
                                "default": "sum",
                            },
                        },
                        "required": ["field"],
                    },
                    "minItems": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of raw grouped rows to retrieve (default 1000).",
                    "default": 1000,
                },
                "orderby": {
                    "type": "string",
                    "description": "Sort order for the raw read_group result, e.g. 'amount_total desc'",
                },
            },
            "required": ["model", "row_fields", "col_fields", "measures"],
        },
    },
    {
        "name": "odoo_time_series",
        "description": (
            "Aggregate a numeric field over time with a chosen interval (day/week/month/quarter/year). "
            "Optionally fills in missing periods with a fill_value (default 0) so the series is "
            "always contiguous — ideal for plotting charts without gaps. "
            "Example: monthly revenue trend for the past year — "
            "model='sale.order', date_field='date_order', interval='month', "
            "measure={field:'amount_total', aggregate:'sum'}, fill_gaps=true. "
            "Use date_from / date_to to extend the range beyond the data boundaries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "domain": {"type": "array", "default": []},
                "date_field": {
                    "type": "string",
                    "description": "Date or datetime field to group by, e.g. 'date_order', 'create_date'",
                },
                "interval": {
                    "type": "string",
                    "enum": ["day", "week", "month", "quarter", "year"],
                    "default": "month",
                },
                "measure": {
                    "type": "object",
                    "description": "What to measure per period.",
                    "properties": {
                        "field": {"type": "string", "description": "Numeric field to aggregate"},
                        "aggregate": {
                            "type": "string",
                            "enum": ["sum", "count", "avg", "min", "max"],
                            "default": "sum",
                        },
                    },
                    "required": ["field"],
                },
                "fill_gaps": {
                    "type": "boolean",
                    "description": "Fill missing periods with fill_value (default true)",
                    "default": True,
                },
                "fill_value": {
                    "type": "number",
                    "description": "Value to use for missing periods when fill_gaps=true (default 0)",
                    "default": 0,
                },
                "date_from": {
                    "type": "string",
                    "description": "Optional start date ISO 8601, e.g. '2026-01-01'. Extends range before first data point.",
                },
                "date_to": {
                    "type": "string",
                    "description": "Optional end date ISO 8601, e.g. '2026-12-31'. Extends range after last data point.",
                },
            },
            "required": ["model", "date_field", "measure"],
        },
    },
    {
        "name": "odoo_top_n",
        "description": (
            "Rank records by a measure and return the top N groups with their values and "
            "percentage of total. Optionally aggregates everything outside the top N into "
            "an 'others' bucket. "
            "Example: top 10 customers by revenue — "
            "model='sale.order', group_field='partner_id', "
            "measure={field:'amount_total', aggregate:'sum'}, n=10. "
            "Set ascending=true to get bottom N instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "domain": {"type": "array", "default": []},
                "group_field": {
                    "type": "string",
                    "description": "Field to group and rank by, e.g. 'partner_id', 'product_id', 'user_id'",
                },
                "measure": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "aggregate": {
                            "type": "string",
                            "enum": ["sum", "count", "avg", "min", "max"],
                            "default": "sum",
                        },
                    },
                    "required": ["field"],
                },
                "n": {
                    "type": "integer",
                    "description": "Number of top results to return (default 10)",
                    "default": 10,
                },
                "ascending": {
                    "type": "boolean",
                    "description": "Set true to rank ascending (bottom N). Default false (top N).",
                    "default": False,
                },
                "include_others": {
                    "type": "boolean",
                    "description": "Include an 'others' bucket with the combined total outside top N (default true)",
                    "default": True,
                },
            },
            "required": ["model", "group_field", "measure"],
        },
    },
    {
        "name": "odoo_cohort",
        "description": (
            "Cohort retention analysis: group records by when they were acquired (cohort_field) "
            "and track their activity over subsequent periods (activity_field). "
            "Shows how many cohort members remain active at period 0, 1, 2, ... N. "
            "Example: customer retention by signup month — "
            "model='res.partner', cohort_field='create_date', activity_field='last_activity_date', "
            "interval='month', periods=12. "
            "V1 note: both fields must be on the same model. "
            "Records are capped at 50,000 — add domain filters for large datasets. "
            "Use odoo_fields_get first to find the right date fields."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "domain": {"type": "array", "default": []},
                "cohort_field": {
                    "type": "string",
                    "description": "Date/datetime field defining when a record joins a cohort, e.g. 'create_date'",
                },
                "activity_field": {
                    "type": "string",
                    "description": "Date/datetime field tracking the last/next activity, e.g. 'last_purchase_date'",
                },
                "interval": {
                    "type": "string",
                    "enum": ["day", "week", "month", "quarter", "year"],
                    "default": "month",
                },
                "periods": {
                    "type": "integer",
                    "description": "Number of follow-up periods to track per cohort (default 12)",
                    "default": 12,
                },
                "measure_type": {
                    "type": "string",
                    "enum": ["count_distinct", "sum"],
                    "description": "count_distinct = count active members; sum = sum of measure_field",
                    "default": "count_distinct",
                },
                "measure_field": {
                    "type": "string",
                    "description": "Numeric field to sum per period when measure_type='sum'",
                },
            },
            "required": ["model", "cohort_field", "activity_field"],
        },
    },
    {
        "name": "odoo_funnel",
        "description": (
            "Funnel conversion analysis: measure how many records pass through each ordered stage. "
            "Each stage is defined by its own domain, so stages can represent any business concept. "
            "Returns absolute_conversion (vs first stage), relative_conversion (vs previous stage), "
            "and drop_off count. "
            "Example: lead-to-sale funnel — stages=["
            "{name:'Leads', domain:[['type','=','lead']]},"
            "{name:'Qualified', domain:[['stage_id.name','=','Qualified']]},"
            "{name:'Won', domain:[['stage_id.probability','=',100]]}]. "
            "Use measure_type='sum' with measure_field for revenue funnels."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "stages": {
                    "type": "array",
                    "description": "Ordered list of funnel stages. Each stage is evaluated independently.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Human-readable stage name, e.g. 'Leads'",
                            },
                            "domain": {
                                "type": "array",
                                "description": "Odoo domain for this stage, e.g. [['state','=','open']]",
                                "default": [],
                            },
                        },
                        "required": ["name"],
                    },
                    "minItems": 1,
                },
                "measure_type": {
                    "type": "string",
                    "enum": ["count", "sum"],
                    "description": "count = number of records; sum = sum of measure_field",
                    "default": "count",
                },
                "measure_field": {
                    "type": "string",
                    "description": "Numeric field to sum when measure_type='sum', e.g. 'amount_total'",
                },
            },
            "required": ["model", "stages"],
        },
    },
    {
        "name": "odoo_export_csv",
        "description": (
            "Export records matching a domain to a CSV file stored as an Odoo ir.attachment. "
            "Returns the attachment ID and a download_url to fetch the file. "
            "The file uses UTF-8 with BOM for seamless Excel compatibility. "
            "Limited to 10,000 rows per call. Reads in batches of 1,000 to stay memory-friendly. "
            "Example: export all confirmed sale orders with partner, date, and total — "
            "model='sale.order', domain=[['state','=','sale']], "
            "fields=['name','partner_id','date_order','amount_total']."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "domain": {"type": "array", "default": []},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields to include in the export. Required — use odoo_fields_get to discover.",
                },
                "filename": {
                    "type": "string",
                    "description": "Output filename, e.g. 'sales_report.csv'. Extension auto-appended if missing.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to export (default 10000, max 10000)",
                    "default": 10000,
                },
            },
            "required": ["model", "fields"],
        },
    },
    {
        "name": "odoo_export_xlsx",
        "description": (
            "Export records to an Excel XLSX file stored as an Odoo ir.attachment. "
            "Header row is bold with freeze pane. Numeric columns use number format for arithmetic. "
            "Columns are auto-sized based on the first 20 rows. "
            "Falls back to CSV automatically if xlsxwriter is not installed, with a warning in the response. "
            "Limited to 10,000 rows per call. "
            "Example: export kontrak rinci to Excel — "
            "model='purchase.order', fields=['name','partner_id','date_order','amount_total'], "
            "sheet_name='PO Report'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Technical model name"},
                "domain": {"type": "array", "default": []},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields to include. Required — use odoo_fields_get to discover.",
                },
                "filename": {
                    "type": "string",
                    "description": "Output filename, e.g. 'report.xlsx'. Extension auto-appended if missing.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to export (default 10000, max 10000)",
                    "default": 10000,
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Excel worksheet name (max 31 chars). Defaults to model name.",
                },
            },
            "required": ["model", "fields"],
        },
    },
]

# ---------------------------------------------------------------------------
# TOOL_HANDLERS — name -> handler callable (merged by consolidation agent)
# ---------------------------------------------------------------------------

TOOL_HANDLERS = {
    "odoo_pivot": _odoo_pivot,
    "odoo_time_series": _odoo_time_series,
    "odoo_top_n": _odoo_top_n,
    "odoo_cohort": _odoo_cohort,
    "odoo_funnel": _odoo_funnel,
    "odoo_export_csv": _odoo_export_csv,
    "odoo_export_xlsx": _odoo_export_xlsx,
}
