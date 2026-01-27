from __future__ import annotations

from datetime import datetime, timedelta
from flask import render_template, request

from . import reports_bp, get_db


@reports_bp.route("/work-logs", methods=["GET"])
def work_logs():
    db = get_db()

    # -------------------------
    # Filters (GET params)
    # -------------------------
    store_id = request.args.get("store_id") or ""
    action = request.args.get("action") or ""
    module = request.args.get("module") or ""
    q = request.args.get("q") or ""
    only_errors = request.args.get("only_errors") == "1"

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
    stores = db.execute("SELECT id, name FROM mst_stores ORDER BY code").fetchall()

    # -------------------------
    # Build WHERE
    # -------------------------
    where = []
    params = []

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

    if q:
        # lightweight search: message/entity/request_id/actor_email
        where.append("""
          (
            COALESCE(message,'') ILIKE %s
            OR COALESCE(entity_table,'') ILIKE %s
            OR COALESCE(entity_id,'') ILIKE %s
            OR COALESCE(request_id,'') ILIKE %s
            OR COALESCE(actor_email,'') ILIKE %s
          )
        """)
        like = f"%{q}%"
        params.extend([like, like, like, like, like])

    where_sql = " AND ".join([w.strip() for w in where]) if where else "TRUE"

    # -------------------------
    # Query (list)
    # -------------------------
    tz = "Asia/Tokyo"   # later: from company/user setting

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
        selected_store_id=store_id,
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
    )