import json
import calendar
from datetime import date, datetime
from flask import render_template, request, redirect, url_for, flash, g, jsonify


def init_store_holidays_views(app, get_db):

    @app.route("/admin/store-holidays", methods=["GET"])
    def store_holidays():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        stores = db.execute(
            "SELECT id, name FROM mst_stores WHERE company_id = %s ORDER BY id",
            (company_id,),
        ).fetchall()

        selected_store_id = request.args.get("store_id", type=int)
        if not selected_store_id and stores:
            selected_store_id = stores[0]["id"]

        # Year for calendar view
        today = date.today()
        year = request.args.get("year", today.year, type=int)

        # Fetch holidays for this store for the displayed year
        holidays = []
        if selected_store_id:
            holidays = db.execute(
                """
                SELECT id, holiday_date, name
                FROM store_holidays
                WHERE store_id = %s AND company_id = %s
                  AND EXTRACT(YEAR FROM holiday_date) = %s
                ORDER BY holiday_date
                """,
                (selected_store_id, company_id, year),
            ).fetchall()

        holiday_dates = {str(h["holiday_date"]) for h in holidays}
        holiday_names = {str(h["holiday_date"]): h["name"] for h in holidays}

        # Build yearly calendar: 12 months, each with weeks
        cal = calendar.Calendar(firstweekday=0)  # Monday first
        yearly_calendar = []
        for m in range(1, 13):
            yearly_calendar.append({
                "month": m,
                "weeks": cal.monthdayscalendar(year, m),
            })

        return render_template(
            "admin/store_holidays.html",
            stores=stores,
            selected_store_id=selected_store_id,
            year=year,
            yearly_calendar=yearly_calendar,
            holiday_dates=holiday_dates,
            holiday_names=holiday_names,
            holidays=holidays,
        )

    @app.route("/admin/store-holidays/toggle", methods=["POST"])
    def store_holidays_toggle():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        store_id = request.form.get("store_id", type=int)
        date_str = request.form.get("date")
        name = (request.form.get("name") or "").strip() or None
        redirect_url = request.form.get("redirect_url")

        if not store_id or not date_str:
            flash("店舗と日付は必須です。")
            return redirect(redirect_url or url_for("store_holidays"))

        # Check if exists
        existing = db.execute(
            "SELECT id FROM store_holidays WHERE store_id = %s AND holiday_date = %s",
            (store_id, date_str),
        ).fetchone()

        if existing:
            db.execute("DELETE FROM store_holidays WHERE id = %s", (existing["id"],))
            db.commit()
        else:
            db.execute(
                "INSERT INTO store_holidays (store_id, holiday_date, name, company_id) VALUES (%s, %s, %s, %s)",
                (store_id, date_str, name, company_id),
            )
            db.commit()

        if redirect_url:
            return redirect(redirect_url)

        year = date_str.split("-")[0]
        return redirect(url_for("store_holidays", store_id=store_id, year=year))

    @app.route("/admin/store-holidays/bulk", methods=["POST"])
    def store_holidays_bulk():
        """Bulk add Japanese public holidays for a given year."""
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        store_id = request.form.get("store_id", type=int)
        year = request.form.get("year", type=int)
        preset = request.form.get("preset")

        if not store_id or not year:
            flash("店舗と年は必須です。")
            return redirect(url_for("store_holidays"))

        dates_to_add = []

        if preset == "public_holidays":
            # Japanese public holidays for the year
            dates_to_add = _japanese_holidays(year)
        elif preset == "yearend":
            # 年末年始 12/29 - 1/3
            for d in range(29, 32):
                dates_to_add.append((f"{year}-12-{d:02d}", "年末年始"))
            for d in range(1, 4):
                dates_to_add.append((f"{year + 1}-01-{d:02d}", "年末年始"))
        elif preset == "obon":
            # お盆 8/13 - 8/16
            for d in range(13, 17):
                dates_to_add.append((f"{year}-08-{d:02d}", "お盆"))

        added = 0
        for date_str, name in dates_to_add:
            try:
                db.execute(
                    """
                    INSERT INTO store_holidays (store_id, holiday_date, name, company_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (store_id, holiday_date) DO NOTHING
                    """,
                    (store_id, date_str, name, company_id),
                )
                added += 1
            except Exception:
                pass
        db.commit()
        flash(f"{added}件の休日を追加しました。")

        return redirect(url_for("store_holidays", store_id=store_id, year=year))


    # ── Supplier Holidays API ─────────────────────────────────────────────

    @app.route("/api/supplier-holidays", methods=["GET"])
    def api_supplier_holidays():
        """Return supplier holidays for a given supplier_id and year as JSON."""
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        supplier_id = request.args.get("supplier_id", type=int)
        year = request.args.get("year", date.today().year, type=int)

        if not supplier_id:
            return jsonify([])

        rows = db.execute(
            """
            SELECT id, holiday_date, name
            FROM supplier_holidays
            WHERE supplier_id = %s AND company_id = %s
              AND EXTRACT(YEAR FROM holiday_date) = %s
            ORDER BY holiday_date
            """,
            (supplier_id, company_id, year),
        ).fetchall()

        return jsonify([
            {"id": r["id"], "date": str(r["holiday_date"]), "name": r["name"] or ""}
            for r in rows
        ])

    @app.route("/suppliers/holidays/toggle", methods=["POST"])
    def supplier_holidays_toggle():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        supplier_id = request.form.get("supplier_id", type=int)
        date_str = request.form.get("date")
        name = (request.form.get("name") or "").strip() or None
        redirect_url = request.form.get("redirect_url") or request.referrer or url_for("store_holidays")

        if not supplier_id or not date_str:
            flash("仕入先と日付は必須です。")
            return redirect(redirect_url)

        existing = db.execute(
            "SELECT id FROM supplier_holidays WHERE supplier_id = %s AND holiday_date = %s",
            (supplier_id, date_str),
        ).fetchone()

        if existing:
            db.execute("DELETE FROM supplier_holidays WHERE id = %s", (existing["id"],))
        else:
            db.execute(
                "INSERT INTO supplier_holidays (supplier_id, holiday_date, name, company_id) VALUES (%s, %s, %s, %s)",
                (supplier_id, date_str, name, company_id),
            )
        db.commit()
        return redirect(redirect_url)

    @app.route("/suppliers/holidays/bulk", methods=["POST"])
    def supplier_holidays_bulk():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        supplier_id = request.form.get("supplier_id", type=int)
        year = request.form.get("year", type=int)
        preset = request.form.get("preset")
        redirect_url = request.form.get("redirect_url") or request.referrer or url_for("store_holidays")

        if not supplier_id or not year:
            flash("仕入先と年は必須です。")
            return redirect(redirect_url)

        dates_to_add = []
        if preset == "public_holidays":
            dates_to_add = _japanese_holidays(year)
        elif preset == "yearend":
            for d in range(29, 32):
                dates_to_add.append((f"{year}-12-{d:02d}", "年末年始"))
            for d in range(1, 4):
                dates_to_add.append((f"{year + 1}-01-{d:02d}", "年末年始"))
        elif preset == "obon":
            for d in range(13, 17):
                dates_to_add.append((f"{year}-08-{d:02d}", "お盆"))

        added = 0
        for ds, nm in dates_to_add:
            try:
                db.execute(
                    """
                    INSERT INTO supplier_holidays (supplier_id, holiday_date, name, company_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (supplier_id, holiday_date) DO NOTHING
                    """,
                    (supplier_id, ds, nm, company_id),
                )
                added += 1
            except Exception:
                pass
        db.commit()
        flash(f"{added}件の休日を追加しました。")
        return redirect(redirect_url)


def _japanese_holidays(year):
    """Return list of (date_str, name) for Japanese public holidays."""
    holidays = [
        (f"{year}-01-01", "元日"),
        (f"{year}-01-13", "成人の日"),
        (f"{year}-02-11", "建国記念の日"),
        (f"{year}-02-23", "天皇誕生日"),
        (f"{year}-03-20", "春分の日"),
        (f"{year}-04-29", "昭和の日"),
        (f"{year}-05-03", "憲法記念日"),
        (f"{year}-05-04", "みどりの日"),
        (f"{year}-05-05", "こどもの日"),
        (f"{year}-07-20", "海の日"),
        (f"{year}-08-11", "山の日"),
        (f"{year}-09-15", "敬老の日"),
        (f"{year}-09-23", "秋分の日"),
        (f"{year}-10-13", "スポーツの日"),
        (f"{year}-11-03", "文化の日"),
        (f"{year}-11-23", "勤労感謝の日"),
    ]
    return holidays
