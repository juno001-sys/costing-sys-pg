from __future__ import annotations

from datetime import datetime, timedelta
from flask import render_template, request,g
from utils.access_scope import (
    get_accessible_stores,
    normalize_accessible_store_id,
)

from . import reports_bp, get_db


@reports_bp.route("/work-logs", methods=["GET"])
def work_logs():
    db = get_db()

    # -------------------------
    # Filters (GET params)
    # -------------------------
    selected_store_id = normalize_accessible_store_id(
        request.args.get("store_id")
    )
    store_id = str(selected_store_id) if selected_store_id else ""
    
    action = request.args.get("action") or ""
    module = request.args.get("module") or ""
    q = request.args.get("q") or ""
    only_errors = request.args.get("only_errors") == "1"

    # NEW: slow filter (>= 3000ms)
    only_slow = request.args.get("only_slow") == "1"
    slow_ms = 3000

    # date range (default: last 7 days)
    today = datetime.now()
    default_from = (today - timedelta(days=7)).date().isoformat()
    default_to = today.date().isoformat()

    date_from = request.args.get("from") or default_from
    date_to = request.args.get("to") or default_to  # inclusive in UI; we’ll use < to+1 day in SQL

    # pagination
    page = int(request.args.get("page") or "1")
    per_page = int(request.args.get("per_page") or "50")
    if per_page not in (25, 50, 100, 200):
        per_page = 50
    if page < 1:
        page = 1

    offset = (page - 1) * per_page

    # stores for filter dropdown
    stores = get_accessible_stores()

    # -------------------------
    # Build WHERE
    # -------------------------
    where = []
    params = []

    company_id = getattr(g, "current_company_id", None)
    if company_id:
        where.append("company_id = %s")
        params.append(company_id)

    # date range: [from 00:00, to+1 00:00)
    where.append("created_at >= %s")
    params.append(date_from)

    where.append("created_at < (%s::date + INTERVAL '1 day')")
    params.append(date_to)

    if store_id:
        where.append("store_id = %s")
        params.append(int(store_id))

    if action:
        where.append("action = %s")
        params.append(action)

    if module:
        where.append("module = %s")
        params.append(module)

    if only_errors:
        where.append("status_code >= 400")

    # NEW: only slow logs (meta.elapsed_ms >= 3000)
    if only_slow:
        where.append("meta ? 'elapsed_ms'")
        where.append("(meta->>'elapsed_ms')::numeric >= %s")
        params.append(slow_ms)

    if q:
        # lightweight search: message/entity/request_id/actor_email
        where.append(
            """
          (
            COALESCE(message,'') ILIKE %s
            OR COALESCE(entity_table,'') ILIKE %s
            OR COALESCE(entity_id,'') ILIKE %s
            OR COALESCE(request_id,'') ILIKE %s
            OR COALESCE(actor_email,'') ILIKE %s
          )
        """
        )
        like = f"%{q}%"
        params.extend([like, like, like, like, like])

    where_sql = " AND ".join([w.strip() for w in where]) if where else "TRUE"

    # -------------------------
    # Query (list)
    # -------------------------
    tz = "Asia/Tokyo"  # later: from company/user setting

    sql = f"""
    SELECT
        id,
        created_at,
        (created_at AT TIME ZONE %s) AS created_at_local,
        %s AS tz_label,

        company_id, store_id,
        actor_email, actor_name,
        request_id, method, path, status_code,
        action, module, entity_table, entity_id,
        message,
        old_data, new_data, meta
    FROM sys_work_logs
    WHERE {where_sql}
    ORDER BY created_at DESC, id DESC
    LIMIT %s OFFSET %s
    """
    rows = db.execute(sql, [tz, tz, *params, per_page, offset]).fetchall()

    # total for pager
    cnt_sql = f"SELECT COUNT(*) AS cnt FROM sys_work_logs WHERE {where_sql}"
    total = db.execute(cnt_sql, params).fetchone()["cnt"]
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "rpt/work_logs.html",
        stores=stores,
        selected_store_id=selected_store_id,
        rows=rows,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        date_from=date_from,
        date_to=date_to,
        action=action,
        module=module,
        q=q,
        only_errors=only_errors,
        # NEW: pass through for checkbox state
        only_slow=only_slow,
        slow_ms=slow_ms,
    )