from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash

from db import get_db

bp = Blueprint("admin_profit_settings", __name__, url_prefix="/admin")


@bp.route("/stores/<int:store_id>/profit-settings", methods=["GET", "POST"])
def profit_settings(store_id):
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        cur.execute(
            """
            INSERT INTO mst_profit_settings
              (store_id, effective_from, fl_ratio, food_ratio, utility_ratio, fixed_cost_yen)
            VALUES
              (%s, %s, %s, %s, %s, %s)
            """,
            (
                store_id,
                request.form["effective_from"],
                request.form["fl_ratio"],
                request.form["food_ratio"],
                request.form["utility_ratio"],
                request.form["fixed_cost_yen"],
            ),
        )
        db.commit()

        flash("利益推計設定を保存しました")
        return redirect(url_for("admin_profit_settings.profit_settings", store_id=store_id))

    # store-specific + global fallback
    cur.execute(
        """
        SELECT
          effective_from,
          fl_ratio,
          food_ratio,
          utility_ratio,
          fixed_cost_yen,
          store_id
        FROM mst_profit_settings
        WHERE store_id = %s OR store_id IS NULL
        ORDER BY
          (store_id IS NOT NULL) DESC,
          effective_from DESC
        """,
        (store_id,),
    )
    rows = cur.fetchall()

    return render_template(
        "mst/profit_settings.html",
        store_id=store_id,
        settings=rows,
        today=date.today(),
    )