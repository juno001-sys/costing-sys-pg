"""Admin screens for CSV import configuration: store aliases + CSV profiles.

Both screens are Chief-Admin only and scoped to the caller's current company.
"""
from __future__ import annotations

from flask import render_template, request, redirect, url_for, flash, g, jsonify

from utils.access_scope import is_chief_admin
from views.reports.audit_log import log_event
from views.pur_delivery_paste import CMS_FIELDS, CMS_FIELD_KEYS, _normalize_name


def init_csv_import_admin_views(app, get_db):
    admin_required = app.extensions["admin_required"]

    # ========================================================================
    # Store aliases
    # ========================================================================
    @app.route("/admin/store-aliases", methods=["GET", "POST"], endpoint="admin_store_aliases")
    @admin_required
    def admin_store_aliases():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        if not company_id:
            flash("Company context is missing.")
            return redirect(url_for("index"))
        if not is_chief_admin():
            flash("この設定はチーフ管理者のみが変更できます。")
            return redirect(url_for("admin_users"))

        if request.method == "POST":
            action = (request.form.get("action") or "").strip()
            actor_id = (getattr(g, "current_user", {}) or {}).get("id")

            if action == "add":
                store_id_raw = (request.form.get("store_id") or "").strip()
                alias_text   = (request.form.get("alias_text") or "").strip()
                if not store_id_raw or not alias_text:
                    flash("店舗と別名テキストを入力してください。")
                    return redirect(url_for("admin_store_aliases"))
                try:
                    store_id = int(store_id_raw)
                except ValueError:
                    flash("店舗IDが不正です。")
                    return redirect(url_for("admin_store_aliases"))
                # Verify store belongs to this company
                owns = db.execute(
                    "SELECT 1 FROM mst_stores WHERE id = %s AND company_id = %s",
                    (store_id, company_id),
                ).fetchone()
                if not owns:
                    flash("指定された店舗がこの会社にありません。")
                    return redirect(url_for("admin_store_aliases"))
                try:
                    db.execute(
                        """
                        INSERT INTO mst_store_aliases
                            (company_id, store_id, alias_text,
                             normalized_alias, created_by_user_id)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (company_id, normalized_alias) DO NOTHING
                        """,
                        (company_id, store_id, alias_text,
                         _normalize_name(alias_text), actor_id),
                    )
                    db.commit()
                    log_event(db, action="STORE_ALIAS_ADD", module="admin",
                              entity_table="mst_store_aliases",
                              entity_id=str(store_id),
                              message=f"alias '{alias_text}' added for store {store_id}",
                              company_id=company_id)
                    flash(f"✅ 別名「{alias_text}」を追加しました。")
                except Exception as e:
                    db.rollback()
                    flash(f"追加に失敗しました: {e}")
                return redirect(url_for("admin_store_aliases"))

            if action == "delete":
                alias_id_raw = (request.form.get("alias_id") or "").strip()
                try:
                    alias_id = int(alias_id_raw)
                except ValueError:
                    flash("別名IDが不正です。")
                    return redirect(url_for("admin_store_aliases"))
                db.execute(
                    "DELETE FROM mst_store_aliases WHERE id = %s AND company_id = %s",
                    (alias_id, company_id),
                )
                db.commit()
                log_event(db, action="STORE_ALIAS_DELETE", module="admin",
                          entity_table="mst_store_aliases",
                          entity_id=str(alias_id),
                          message=f"alias id {alias_id} deleted",
                          company_id=company_id)
                flash("✅ 別名を削除しました。")
                return redirect(url_for("admin_store_aliases"))

            flash("不明な操作です。")
            return redirect(url_for("admin_store_aliases"))

        # GET
        stores = db.execute(
            """
            SELECT id, code, name FROM mst_stores
            WHERE company_id = %s AND COALESCE(is_active, 1) = 1
            ORDER BY code
            """,
            (company_id,),
        ).fetchall()
        aliases = db.execute(
            """
            SELECT a.id, a.store_id, a.alias_text, a.normalized_alias,
                   a.created_at, s.code AS store_code, s.name AS store_name
            FROM mst_store_aliases a
            JOIN mst_stores s ON s.id = a.store_id
            WHERE a.company_id = %s
            ORDER BY s.code, a.alias_text
            """,
            (company_id,),
        ).fetchall()
        return render_template(
            "admin/store_aliases.html",
            stores=stores,
            aliases=aliases,
        )

    # ========================================================================
    # CSV import profiles (matrix editor)
    # ========================================================================
    @app.route("/admin/csv-profiles", methods=["GET", "POST"], endpoint="admin_csv_profiles")
    @admin_required
    def admin_csv_profiles():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        if not company_id:
            flash("Company context is missing.")
            return redirect(url_for("index"))
        if not is_chief_admin():
            flash("この設定はチーフ管理者のみが変更できます。")
            return redirect(url_for("admin_users"))

        if request.method == "POST":
            action = (request.form.get("action") or "").strip()
            actor_id = (getattr(g, "current_user", {}) or {}).get("id")

            if action == "create_profile":
                name = (request.form.get("name") or "").strip()
                if not name:
                    flash("プロファイル名を入力してください。")
                    return redirect(url_for("admin_csv_profiles"))
                try:
                    db.execute(
                        """
                        INSERT INTO csv_import_profiles
                            (company_id, name, description, encoding)
                        VALUES (%s, %s, %s, 'cp932')
                        """,
                        (company_id, name,
                         request.form.get("description") or ""),
                    )
                    db.commit()
                    log_event(db, action="CSV_PROFILE_CREATE", module="admin",
                              entity_table="csv_import_profiles",
                              message=f"profile '{name}' created",
                              company_id=company_id)
                    flash(f"✅ プロファイル「{name}」を作成しました。")
                except Exception as e:
                    db.rollback()
                    flash(f"作成に失敗しました（同名プロファイルが既にある場合はこのエラーになります）: {e}")
                return redirect(url_for("admin_csv_profiles"))

            if action == "delete_profile":
                pid_raw = (request.form.get("profile_id") or "").strip()
                try:
                    pid = int(pid_raw)
                except ValueError:
                    flash("プロファイルIDが不正です。")
                    return redirect(url_for("admin_csv_profiles"))
                db.execute(
                    "DELETE FROM csv_import_profiles WHERE id = %s AND company_id = %s",
                    (pid, company_id),
                )
                db.commit()
                log_event(db, action="CSV_PROFILE_DELETE", module="admin",
                          entity_table="csv_import_profiles",
                          entity_id=str(pid),
                          message=f"profile id {pid} deleted",
                          company_id=company_id)
                flash("✅ プロファイルを削除しました。")
                return redirect(url_for("admin_csv_profiles"))

            if action == "save_mappings":
                # Bulk save: for every cell in the matrix, upsert or delete
                # based on whether the text is empty.
                pids_raw = request.form.getlist("profile_id")
                for pid_raw in pids_raw:
                    try:
                        pid = int(pid_raw)
                    except ValueError:
                        continue
                    # Verify this profile belongs to the current company
                    owns = db.execute(
                        "SELECT 1 FROM csv_import_profiles WHERE id = %s AND company_id = %s",
                        (pid, company_id),
                    ).fetchone()
                    if not owns:
                        continue
                    for field in CMS_FIELD_KEYS:
                        csv_text = (request.form.get(f"map__{pid}__{field}") or "").strip()
                        if csv_text:
                            db.execute(
                                """
                                INSERT INTO csv_import_mappings
                                    (profile_id, cms_field, csv_header_text)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (profile_id, cms_field) DO UPDATE SET
                                    csv_header_text = EXCLUDED.csv_header_text
                                """,
                                (pid, field, csv_text),
                            )
                        else:
                            db.execute(
                                "DELETE FROM csv_import_mappings WHERE profile_id = %s AND cms_field = %s",
                                (pid, field),
                            )
                db.commit()
                log_event(db, action="CSV_PROFILE_MAPPING_UPDATE", module="admin",
                          entity_table="csv_import_mappings",
                          message="bulk mapping update",
                          company_id=company_id)
                flash("✅ マッピングを保存しました。")
                return redirect(url_for("admin_csv_profiles"))

            flash("不明な操作です。")
            return redirect(url_for("admin_csv_profiles"))

        # GET — load profiles + their mappings into a matrix
        profiles = db.execute(
            """
            SELECT id, name, description, encoding
            FROM csv_import_profiles
            WHERE company_id = %s AND is_active = 1
            ORDER BY name
            """,
            (company_id,),
        ).fetchall()
        profile_rows = [dict(p) for p in profiles]
        pids = [p["id"] for p in profile_rows]
        mappings_by_profile = {pid: {} for pid in pids}
        if pids:
            map_rows = db.execute(
                """
                SELECT profile_id, cms_field, csv_header_text
                FROM csv_import_mappings
                WHERE profile_id = ANY(%s)
                """,
                (pids,),
            ).fetchall()
            for r in map_rows:
                mappings_by_profile[r["profile_id"]][r["cms_field"]] = r["csv_header_text"]

        return render_template(
            "admin/csv_profiles.html",
            profiles=profile_rows,
            mappings_by_profile=mappings_by_profile,
            cms_fields=CMS_FIELDS,
        )
