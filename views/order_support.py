"""
Order Support Screen — 7-day forward view of ordering needs.

For each supplier, shows:
- Next delivery dates in the 7-day window
- Order deadlines
- Items with current stock vs est_order_qty
- Warnings for holiday gaps
"""

import json
from datetime import date, timedelta
from flask import render_template, request, redirect, url_for, g, jsonify

from utils.access_scope import (
    get_accessible_stores,
    normalize_accessible_store_id,
)
from views.reports.audit_log import log_event


DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
DAY_LABELS = {'mon': '月', 'tue': '火', 'wed': '水', 'thu': '木', 'fri': '金', 'sat': '土', 'sun': '日'}


def _date_to_day_key(d):
    """Convert a date to day key (mon, tue, ...)."""
    return DAY_KEYS[d.weekday()]


def _get_delivery_dates(schedule, start_date, num_days, holidays_set, min_deadline_date=None):
    """
    Get all delivery dates in a date range, excluding supplier holidays.
    Returns list of (delivery_date, deadline_date, deadline_time).

    If min_deadline_date is set, skip deliveries whose deadline has passed
    (operator can no longer place an order for them).
    """
    if not schedule:
        return []

    results = []
    for i in range(num_days):
        d = start_date + timedelta(days=i)
        day_key = _date_to_day_key(d)

        if day_key in schedule and str(d) not in holidays_set:
            info = schedule[day_key]
            deadline_days = info.get('deadline_days', 1) or 0
            deadline_time = info.get('deadline_time') or None

            # Calculate deadline date
            deadline_date = d - timedelta(days=deadline_days)

            # If deadline falls on a supplier holiday, shift earlier
            while str(deadline_date) in holidays_set:
                deadline_date -= timedelta(days=1)

            # Skip past-deadline deliveries (non-actionable for ordering)
            if min_deadline_date and deadline_date < min_deadline_date:
                continue

            results.append({
                'delivery_date': d,
                'delivery_day_label': DAY_LABELS.get(_date_to_day_key(d), ''),
                'deadline_date': deadline_date,
                'deadline_day_label': DAY_LABELS.get(_date_to_day_key(deadline_date), ''),
                'deadline_time': deadline_time,
            })

    return results


def _find_next_delivery_after(schedule, after_date, holidays_set, max_days=60):
    """Find the next delivery date AFTER the given window (for gap warnings)."""
    for i in range(1, max_days + 1):
        d = after_date + timedelta(days=i)
        day_key = _date_to_day_key(d)
        if day_key in schedule and str(d) not in holidays_set:
            return d
    return None


def init_order_support_views(app, get_db):

    @app.route("/order-support", methods=["GET"])
    def order_support():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        mst_stores = get_accessible_stores()
        selected_store_id = normalize_accessible_store_id(
            request.args.get("store_id")
        )
        store_id = str(selected_store_id) if selected_store_id else ""

        # Base date (default: today)
        base_date_str = request.args.get("base_date") or str(date.today())
        try:
            base_date = date.fromisoformat(base_date_str)
        except ValueError:
            base_date = date.today()

        # 7-day window
        window_days = 7
        date_range = [base_date + timedelta(days=i) for i in range(window_days)]

        # Extended window for gap detection (look ahead 30 days beyond the 7-day window)
        extended_end = base_date + timedelta(days=window_days + 30)

        supplier_cards = []

        if selected_store_id:
            # ── Get all active suppliers with their items ────────────
            # is_orderable filter: operator can hide a supplier from this
            # screen (auto-resets when a purchase is recorded).
            suppliers = db.execute(
                """
                SELECT DISTINCT s.id, s.code, s.name, s.order_method, s.order_url,
                       s.delivery_schedule, s.order_notes, s.holidays_off
                FROM pur_suppliers s
                JOIN mst_items i ON i.supplier_id = s.id
                WHERE s.is_active = 1 AND s.company_id = %s
                  AND s.is_orderable = TRUE
                  AND i.is_active = 1 AND i.company_id = %s
                  AND i.is_orderable = TRUE
                ORDER BY s.code
                """,
                (company_id, company_id),
            ).fetchall()

            # ── Get store holidays (for suppliers with holidays_off) ──
            store_holidays_rows = db.execute(
                """
                SELECT holiday_date FROM store_holidays
                WHERE store_id = %s AND company_id = %s
                  AND holiday_date >= %s AND holiday_date <= %s
                """,
                (selected_store_id, company_id, base_date, extended_end),
            ).fetchall()
            store_holiday_set = {str(h["holiday_date"]) for h in store_holidays_rows}

            # ── Get supplier-specific holidays ────────────────────────
            all_supplier_holidays = db.execute(
                """
                SELECT supplier_id, holiday_date
                FROM supplier_holidays
                WHERE company_id = %s
                  AND holiday_date >= %s AND holiday_date <= %s
                """,
                (company_id, base_date, extended_end),
            ).fetchall()

            holidays_by_supplier = {}
            for h in all_supplier_holidays:
                sid = h["supplier_id"]
                if sid not in holidays_by_supplier:
                    holidays_by_supplier[sid] = set()
                holidays_by_supplier[sid].add(str(h["holiday_date"]))

            # ── Get all items for the store ──────────────────────────
            items = db.execute(
                """
                SELECT i.id, i.code, i.name, i.supplier_id, i.est_order_qty, i.category
                FROM mst_items i
                WHERE i.is_active = 1 AND i.company_id = %s
                  AND i.is_orderable = TRUE
                ORDER BY i.code
                """,
                (company_id,),
            ).fetchall()

            items_by_supplier = {}
            all_item_ids = []
            for item in items:
                sid = item["supplier_id"]
                if sid not in items_by_supplier:
                    items_by_supplier[sid] = []
                items_by_supplier[sid].append(item)
                all_item_ids.append(item["id"])

            # ── Latest stock count per item + purchases after that count,
            #    collapsed into one query (was N+1: one SUM per item).
            stock_map = {}
            if all_item_ids:
                stock_rows = db.execute(
                    """
                    WITH latest AS (
                      SELECT DISTINCT ON (item_id)
                        item_id, counted_qty, count_date
                      FROM stock_counts
                      WHERE store_id = %s AND count_date <= %s
                      ORDER BY item_id, count_date DESC, id DESC
                    )
                    SELECT
                      l.item_id,
                      l.counted_qty,
                      l.count_date,
                      COALESCE(SUM(p.quantity), 0) AS qty_after
                    FROM latest l
                    LEFT JOIN purchases p
                      ON p.store_id = %s
                     AND p.item_id = l.item_id
                     AND p.is_deleted = 0
                     AND p.delivery_date > l.count_date
                    GROUP BY l.item_id, l.counted_qty, l.count_date
                    """,
                    (selected_store_id, base_date, selected_store_id),
                ).fetchall()
                stock_map = {
                    r["item_id"]: {
                        "qty": (r["counted_qty"] or 0) + (r["qty_after"] or 0),
                        "date": r["count_date"],
                    }
                    for r in stock_rows
                }

            # ── Load today's draft order qtys (one query for the store) ──
            # Key: (supplier_id, item_id) → quantity. Used to pre-fill the
            # qty inputs on the items table.
            today_date = date.today()
            draft_rows = db.execute(
                """
                SELECT d.supplier_id, i.item_id, i.quantity
                FROM pur_order_drafts d
                JOIN pur_order_draft_items i ON i.order_draft_id = d.id
                WHERE d.company_id = %s
                  AND d.store_id = %s
                  AND d.order_date = %s
                """,
                (company_id, selected_store_id, today_date),
            ).fetchall()
            draft_qty_map = {
                (r["supplier_id"], r["item_id"]): r["quantity"] for r in draft_rows
            }

            # ── Build supplier cards ─────────────────────────────────
            for supplier in suppliers:
                sid = supplier["id"]
                schedule = supplier["delivery_schedule"] or {}
                # Merge store holidays (if holidays_off) + supplier-specific holidays
                holidays_set = set(holidays_by_supplier.get(sid, set()))
                if supplier["holidays_off"]:
                    holidays_set |= store_holiday_set

                # Delivery dates in the 7-day window (deadline must be future)
                all_deliveries = _get_delivery_dates(
                    schedule, base_date, window_days, holidays_set,
                    min_deadline_date=base_date,
                )
                deliveries = all_deliveries[:3]  # Show only next 2-3 deliveries

                # Find next delivery after window (for gap warning)
                last_delivery_in_window = all_deliveries[-1]['delivery_date'] if all_deliveries else base_date
                next_after = _find_next_delivery_after(schedule, base_date + timedelta(days=window_days - 1), holidays_set)

                # Calculate gap warning
                gap_warning = None
                if all_deliveries and next_after:
                    gap_days = (next_after - all_deliveries[-1]['delivery_date']).days
                    # Normal gap = 7 / number_of_delivery_days_per_week
                    num_delivery_days = len(schedule)
                    normal_gap = (7 / num_delivery_days) if num_delivery_days > 0 else 7
                    if gap_days > normal_gap * 1.5:
                        gap_warning = {
                            'days': gap_days,
                            'next_date': next_after,
                            'last_in_window': all_deliveries[-1]['delivery_date'],
                        }
                elif not all_deliveries and schedule:
                    # No deliveries in window — try to surface up to 3 upcoming
                    # deliveries beyond the window so the card matches the
                    # 発注〆切 / 納品 layout of other suppliers. 45-day window
                    # handles weekly (21d → 3) and biweekly (42d → 3) schedules.
                    next_after_now = _find_next_delivery_after(schedule, base_date - timedelta(days=1), holidays_set)
                    if next_after_now:
                        upcoming = _get_delivery_dates(
                            schedule, next_after_now, 45, holidays_set,
                            min_deadline_date=base_date,
                        )[:3]
                        if upcoming:
                            # Deadline is now visible in the table, which is
                            # the actionable info — no banner needed. The
                            # banner is only a fallback when we can't surface
                            # dates below.
                            deliveries = upcoming
                        else:
                            gap_warning = {
                                'days': (next_after_now - base_date).days,
                                'next_date': next_after_now,
                                'last_in_window': None,
                            }

                # Normal-supplier gap-banner suppression: once the earliest
                # visible deadline has passed, the banner is stale — operator
                # can't place an order for that delivery anymore.
                if gap_warning and deliveries and deliveries[0]['deadline_date'] < base_date:
                    gap_warning = None

                # Build items list with stock info
                supplier_items = items_by_supplier.get(sid, [])
                item_rows = []
                for item in supplier_items:
                    stock_info = stock_map.get(item["id"], {})
                    current_stock = stock_info.get("qty", 0)
                    last_count_date = stock_info.get("date")
                    est_qty = item["est_order_qty"] or 0

                    if est_qty > 0:
                        if current_stock < est_qty:
                            status = "shortage"
                        elif current_stock < est_qty * 1.5:
                            status = "low"
                        else:
                            status = "ok"
                    else:
                        status = "unknown"

                    item_rows.append({
                        "id": item["id"],
                        "code": item["code"],
                        "name": item["name"],
                        "category": item["category"],
                        "current_stock": current_stock,
                        "last_count_date": last_count_date,
                        "est_order_qty": est_qty,
                        "status": status,
                        "draft_qty": draft_qty_map.get((sid, item["id"]), 0),
                    })

                # Sort: shortage first, then low, then ok
                status_order = {"shortage": 0, "low": 1, "unknown": 2, "ok": 3}
                item_rows.sort(key=lambda x: (status_order.get(x["status"], 9), x["code"]))

                # Build 7-day column data
                day_columns = []
                for d in date_range:
                    day_key = _date_to_day_key(d)
                    is_holiday = str(d) in holidays_set
                    is_delivery = any(dl['delivery_date'] == d for dl in all_deliveries)
                    is_deadline = any(dl['deadline_date'] == d for dl in all_deliveries)

                    day_columns.append({
                        'date': d,
                        'day_label': DAY_LABELS.get(day_key, ''),
                        'is_holiday': is_holiday,
                        'is_delivery': is_delivery,
                        'is_deadline': is_deadline,
                    })

                supplier_cards.append({
                    'id': sid,
                    'code': supplier["code"],
                    'name': supplier["name"],
                    'order_method': supplier["order_method"],
                    'order_url': supplier["order_url"],
                    'order_notes': supplier["order_notes"],
                    'has_schedule': bool(schedule),
                    'deliveries': deliveries,
                    'gap_warning': gap_warning,
                    'item_rows': item_rows,
                    'day_columns': day_columns,
                    'shortage_count': sum(1 for r in item_rows if r["status"] == "shortage"),
                    'low_count': sum(1 for r in item_rows if r["status"] == "low"),
                })

        # ── Sheet view: flat row-per-item with supplier/delivery info ────
        view_mode = (request.args.get("view") or "cards").strip()
        sheet_rows = []
        if view_mode == "sheet" and supplier_cards:
            from utils.item_frequency import fetch_item_frequency, bucket_order
            all_ids = [r["id"] for c in supplier_cards for r in c["item_rows"] if r.get("id")]
            freq_map = fetch_item_frequency(db, all_ids)
            for card in supplier_cards:
                next_delivery = card["deliveries"][0] if card["deliveries"] else None
                for r in card["item_rows"]:
                    f = freq_map.get(r.get("id"), {"bucket": "none", "purchase_days": 0, "per_month": 0, "rate_scale": "none", "rate_n": None})
                    sheet_rows.append({
                        "supplier_name": card["name"],
                        "supplier_id":   card["id"],
                        "deadline":      next_delivery["deadline_date"] if next_delivery else None,
                        "deadline_time": next_delivery["deadline_time"] if next_delivery else None,
                        "next_delivery": next_delivery["delivery_date"] if next_delivery else None,
                        "code":          r["code"],
                        "name":          r["name"],
                        "category":      r["category"],
                        "frequency":     f["bucket"],
                        "per_month":     f["per_month"],
                        "rate_scale":    f.get("rate_scale", "none"),
                        "rate_n":        f.get("rate_n"),
                        "current_stock": r["current_stock"],
                        "last_count_date": r["last_count_date"],
                        "est_order_qty": r["est_order_qty"],
                        "status":        r["status"],
                    })
            # Default sort: frequency DESC (very_high → high → low → none), then status, then supplier
            status_order = {"shortage": 0, "low": 1, "unknown": 2, "ok": 3}
            sheet_rows.sort(key=lambda x: (
                bucket_order(x["frequency"]),
                status_order.get(x["status"], 9),
                x["supplier_name"],
                x["code"],
            ))

        template = "pur/order_support_sheet.html" if view_mode == "sheet" else "pur/order_support.html"
        return render_template(
            template,
            mst_stores=mst_stores,
            selected_store_id=selected_store_id or "",
            base_date=base_date_str,
            date_range=date_range,
            supplier_cards=supplier_cards,
            sheet_rows=sheet_rows,
            view_mode=view_mode,
            DAY_LABELS=DAY_LABELS,
        )

    # ----------------------------------------
    # In-screen 発注対象外 toggles. Operators can hide an item or an
    # entire supplier from the order-support screen without leaving
    # the page. Both auto-reset to visible when a purchase lands
    # (see pur_purchases INSERT trigger).
    # ----------------------------------------
    def _redirect_back_to_order_support():
        return redirect(url_for(
            "order_support",
            store_id=request.form.get("store_id") or request.args.get("store_id"),
            base_date=request.form.get("base_date") or request.args.get("base_date"),
            view=request.form.get("view") or request.args.get("view"),
        ))

    @app.route("/order-support/item/<int:item_id>/hide", methods=["POST"])
    def order_support_hide_item(item_id):
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        db.execute(
            "UPDATE mst_items SET is_orderable = FALSE "
            "WHERE id = %s AND company_id = %s",
            (item_id, company_id),
        )
        try:
            log_event(
                db, action="HIDE", module="order_support",
                entity_table="mst_items", entity_id=str(item_id),
                message="Item hidden from order support",
            )
        except Exception:
            pass
        db.commit()
        return _redirect_back_to_order_support()

    @app.route("/order-support/supplier/<int:supplier_id>/order-form", methods=["GET"])
    def order_support_order_form(supplier_id):
        """Render a printable / mailto / web-ref / phone-ref order form
        for one supplier, based on the supplier's order_method. Reads
        today's draft_order_items to fill the qty column."""
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        try:
            store_id = int(request.args.get("store_id") or 0)
        except (TypeError, ValueError):
            store_id = 0
        if not (company_id and store_id):
            return "missing store_id", 400

        supplier = db.execute(
            """
            SELECT id, code, name, email, fax, phone, company_phone,
                   contact_person, contact_phone, address,
                   order_method, order_url, order_notes, delivery_schedule,
                   holidays_off
            FROM pur_suppliers
            WHERE id = %s AND company_id = %s AND is_active = 1
            """,
            (supplier_id, company_id),
        ).fetchone()
        if not supplier:
            return "supplier not found", 404

        store = db.execute(
            "SELECT id, code, name FROM mst_stores WHERE id = %s AND company_id = %s",
            (store_id, company_id),
        ).fetchone()
        company = db.execute(
            "SELECT id, code, name FROM mst_companies WHERE id = %s",
            (company_id,),
        ).fetchone()

        today_date = date.today()

        draft_items = db.execute(
            """
            SELECT di.item_id, di.quantity,
                   i.code  AS item_code,
                   i.name  AS item_name,
                   i.unit  AS item_unit,
                   i.category
            FROM pur_order_drafts d
            JOIN pur_order_draft_items di ON di.order_draft_id = d.id
            JOIN mst_items i ON i.id = di.item_id
            WHERE d.company_id = %s
              AND d.store_id = %s
              AND d.supplier_id = %s
              AND d.order_date = %s
              AND di.quantity > 0
            ORDER BY i.code
            """,
            (company_id, store_id, supplier_id, today_date),
        ).fetchall()

        # Next-delivery date to pre-fill 納品希望日 on the form.
        # Reuse the supplier's delivery_schedule + holidays.
        schedule = supplier["delivery_schedule"] or {}
        holiday_rows = db.execute(
            """
            SELECT holiday_date FROM supplier_holidays
            WHERE supplier_id = %s AND company_id = %s
              AND holiday_date >= %s AND holiday_date <= %s
            """,
            (supplier_id, company_id, today_date, today_date + timedelta(days=45)),
        ).fetchall()
        holidays_set = {str(h["holiday_date"]) for h in holiday_rows}
        if supplier["holidays_off"]:
            store_holiday_rows = db.execute(
                """
                SELECT holiday_date FROM store_holidays
                WHERE store_id = %s AND company_id = %s
                  AND holiday_date >= %s AND holiday_date <= %s
                """,
                (store_id, company_id, today_date, today_date + timedelta(days=45)),
            ).fetchall()
            holidays_set |= {str(h["holiday_date"]) for h in store_holiday_rows}

        delivery_candidates = _get_delivery_dates(
            schedule, today_date, 45, holidays_set, min_deadline_date=today_date,
        )
        next_delivery = delivery_candidates[0] if delivery_candidates else None

        method = (supplier["order_method"] or "").lower().strip()

        # Plain-text body used both by the mailto: link and the copy-paste panel.
        mail_lines = [
            f"{supplier['name']} ご担当者様",
            "",
            "お世話になっております。",
            f"{company['name'] if company else ''} {store['name'] if store else ''} です。",
            "以下の通り発注いたします。",
            "",
        ]
        if next_delivery:
            mail_lines.append(f"納品希望日：{next_delivery['delivery_date'].strftime('%Y-%m-%d')}（{next_delivery['delivery_day_label']}）")
            mail_lines.append("")
        mail_lines.append("---")
        for it in draft_items:
            unit_tail = f" ×{it['item_unit']}" if it['item_unit'] else ""
            code_head = f"[{it['item_code']}] " if it['item_code'] else ""
            mail_lines.append(f"{code_head}{it['item_name']} — {it['quantity']}{unit_tail}")
        mail_lines.append("---")
        if supplier["order_notes"]:
            mail_lines.append("")
            mail_lines.append(f"備考：{supplier['order_notes']}")
        mail_lines.append("")
        mail_lines.append("何卒よろしくお願いいたします。")
        mail_body_plain = "\n".join(mail_lines)

        return render_template(
            "pur/order_form.html",
            supplier=supplier,
            store=store,
            company=company,
            order_date=today_date,
            draft_items=draft_items,
            next_delivery=next_delivery,
            method=method,
            mail_body_plain=mail_body_plain,
        )

    @app.route("/order-support/draft/save", methods=["POST"])
    def order_support_draft_save():
        """Upsert a single (store, supplier, item, today) draft qty. JSON body:
        {store_id, supplier_id, item_id, quantity}.
        Quantity = 0 removes the row; header is kept (operator may add more
        items later the same day)."""
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        current_user = getattr(g, "current_user", None) or {}
        operator_id = current_user.get("id")

        payload = request.get_json(silent=True) or {}
        try:
            store_id = int(payload.get("store_id") or 0)
            supplier_id = int(payload.get("supplier_id") or 0)
            item_id = int(payload.get("item_id") or 0)
            quantity = int(payload.get("quantity") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400

        if not (company_id and store_id and supplier_id and item_id):
            return jsonify({"ok": False, "error": "missing_fields"}), 400
        if quantity < 0:
            return jsonify({"ok": False, "error": "negative_qty"}), 400

        # Verify the item + supplier belong to the current company (defense in depth)
        ok_row = db.execute(
            """
            SELECT 1
            FROM mst_items i
            JOIN pur_suppliers s ON s.id = %s AND s.company_id = i.company_id
            WHERE i.id = %s AND i.company_id = %s
            """,
            (supplier_id, item_id, company_id),
        ).fetchone()
        if not ok_row:
            return jsonify({"ok": False, "error": "not_found"}), 404

        today_date = date.today()

        # Upsert header
        header_row = db.execute(
            """
            INSERT INTO pur_order_drafts
              (company_id, store_id, supplier_id, order_date, operator_id, status, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'draft', NOW())
            ON CONFLICT (company_id, store_id, supplier_id, order_date)
            DO UPDATE SET operator_id = EXCLUDED.operator_id,
                          updated_at  = NOW()
            RETURNING id
            """,
            (company_id, store_id, supplier_id, today_date, operator_id),
        ).fetchone()
        header_id = header_row["id"]

        if quantity == 0:
            db.execute(
                "DELETE FROM pur_order_draft_items WHERE order_draft_id = %s AND item_id = %s",
                (header_id, item_id),
            )
        else:
            db.execute(
                """
                INSERT INTO pur_order_draft_items (order_draft_id, item_id, quantity)
                VALUES (%s, %s, %s)
                ON CONFLICT (order_draft_id, item_id)
                DO UPDATE SET quantity = EXCLUDED.quantity
                """,
                (header_id, item_id, quantity),
            )

        db.commit()
        return jsonify({"ok": True, "quantity": quantity})

    @app.route("/order-support/supplier/<int:supplier_id>/hide", methods=["POST"])
    def order_support_hide_supplier(supplier_id):
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        db.execute(
            "UPDATE pur_suppliers SET is_orderable = FALSE "
            "WHERE id = %s AND company_id = %s",
            (supplier_id, company_id),
        )
        try:
            log_event(
                db, action="HIDE", module="order_support",
                entity_table="pur_suppliers", entity_id=str(supplier_id),
                message="Supplier hidden from order support",
            )
        except Exception:
            pass
        db.commit()
        return _redirect_back_to_order_support()
