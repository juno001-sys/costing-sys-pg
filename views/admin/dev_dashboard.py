# views/admin/dev_dashboard.py
from flask import render_template, redirect, url_for, flash, g, request

from utils.sys_roles import sys_role_required


def init_dev_dashboard_views(app, get_db):

    @app.get("/dashboard/dev")
    @sys_role_required("engineer")
    def dev_dashboard():
        db = get_db()
        days = request.args.get("days", 7, type=int)
        if days not in (7, 30, 90):
            days = 7

        # ── Top pages by hit count ────────────────────────────────
        top_pages = db.execute(
            """
            SELECT
              path,
              method,
              COUNT(*)                                      AS hits,
              COUNT(*) FILTER (WHERE status_code >= 400)   AS errors,
              ROUND(AVG((meta->>'elapsed_ms')::numeric), 0) AS avg_ms,
              MAX((meta->>'elapsed_ms')::numeric)           AS max_ms
            FROM sys_work_logs
            WHERE created_at >= NOW() - (%s || ' days')::interval
              AND path NOT LIKE '/static/%%'
            GROUP BY path, method
            ORDER BY hits DESC
            LIMIT 20
            """,
            (str(days),),
        ).fetchall()

        # ── Recent errors ─────────────────────────────────────────
        recent_errors = db.execute(
            """
            SELECT
              created_at,
              method,
              path,
              status_code,
              actor_email,
              meta->>'elapsed_ms' AS elapsed_ms
            FROM sys_work_logs
            WHERE status_code >= 400
              AND created_at >= NOW() - (%s || ' days')::interval
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (str(days),),
        ).fetchall()

        # ── Hourly activity (last 7 days always) ─────────────────
        hourly = db.execute(
            """
            SELECT
              EXTRACT(DOW  FROM created_at)::int AS dow,
              EXTRACT(HOUR FROM created_at)::int AS hour,
              COUNT(*) AS hits
            FROM sys_work_logs
            WHERE created_at >= NOW() - INTERVAL '7 days'
              AND path NOT LIKE '/static/%%'
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ).fetchall()

        # Build dow×hour matrix for template
        heatmap = [[0] * 24 for _ in range(7)]
        for r in hourly:
            heatmap[r["dow"]][r["hour"]] = r["hits"]
        heatmap_max = max(v for row in heatmap for v in row) or 1

        # ── Slow requests ─────────────────────────────────────────
        slow = db.execute(
            """
            SELECT
              created_at,
              method,
              path,
              status_code,
              (meta->>'elapsed_ms')::numeric AS elapsed_ms,
              actor_email
            FROM sys_work_logs
            WHERE (meta->>'elapsed_ms')::numeric >= 800
              AND created_at >= NOW() - (%s || ' days')::interval
            ORDER BY (meta->>'elapsed_ms')::numeric DESC
            LIMIT 20
            """,
            (str(days),),
        ).fetchall()

        # ── Summary counts ────────────────────────────────────────
        summary = db.execute(
            """
            SELECT
              COUNT(*)                                    AS total_hits,
              COUNT(*) FILTER (WHERE status_code >= 400) AS total_errors,
              COUNT(*) FILTER (WHERE status_code >= 500) AS total_500s,
              COUNT(DISTINCT actor_email)
                FILTER (WHERE actor_email IS NOT NULL)   AS unique_users,
              ROUND(AVG((meta->>'elapsed_ms')::numeric), 0) AS avg_ms
            FROM sys_work_logs
            WHERE created_at >= NOW() - (%s || ' days')::interval
              AND path NOT LIKE '/static/%%'
            """,
            (str(days),),
        ).fetchone()

        return render_template(
            "admin/dev_dashboard.html",
            days=days,
            top_pages=top_pages,
            recent_errors=recent_errors,
            heatmap=heatmap,
            heatmap_max=heatmap_max,
            slow=slow,
            summary=summary,
        )
