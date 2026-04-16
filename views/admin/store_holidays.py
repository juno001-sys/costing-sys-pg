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

        # Year/month for calendar view
        today = date.today()
        year = request.args.get("year", today.year, type=int)
        month = request.args.get("month", today.month, type=int)

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

        # Build calendar data for the displayed month
        cal = calendar.Calendar(firstweekday=0)  # Monday first
        month_days = cal.monthdayscalendar(year, month)

        holiday_dates = {str(h["holiday_date"]) for h in holidays}
        holiday_names = {str(h["holiday_date"]): h["name"] for h in holidays}

        # Prev/next month
        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        return render_template(
            "admin/store_holidays.html",
            stores=stores,
            selected_store_id=selected_store_id,
            year=year,
            month=month,
            month_days=month_days,
            holiday_dates=holiday_dates,
            holiday_names=holiday_names,
            holidays=holidays,
            prev_year=prev_year,
            prev_month=prev_month,
            next_year=next_year,
            next_month=next_month,
        )

    @app.route("/admin/store-holidays/toggle", methods=["POST"])
    def store_holidays_toggle():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        store_id = request.form.get("store_id", type=int)
        date_str = request.form.get("date")
        name = (request.form.get("name") or "").strip() or None

        if not store_id or not date_str:
            flash("店舗と日付は必須です。")
            return redirect(url_for("store_holidays"))

        # Check if exists
        existing = db.execute(
            "SELECT id FROM store_holidays WHERE store_id = %s AND holiday_date = %s",
            (store_id, date_str),
        ).fetchone()

        if existing:
            # Remove
            db.execute("DELETE FROM store_holidays WHERE id = %s", (existing["id"],))
            db.commit()
        else:
            # Add
            db.execute(
                "INSERT INTO store_holidays (store_id, holiday_date, name, company_id) VALUES (%s, %s, %s, %s)",
                (store_id, date_str, name, company_id),
            )
            db.commit()

        year, month = date_str.split("-")[0], date_str.split("-")[1]
        return redirect(
            url_for("store_holidays", store_id=store_id, year=year, month=month)
        )

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
