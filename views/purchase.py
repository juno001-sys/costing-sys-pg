from flask import Blueprint, render_template

purchase_bp = Blueprint("purchase", __name__, url_prefix="/purchases")

@purchase_bp.route("/")
def index():
    return "purchase screen"


# ---------------------------------------
# home.html が呼んでいる old endpoint 互換
# ---------------------------------------
@purchase_bp.route("/new", endpoint="purchase_new_purchase")
def purchase_new_purchase():
    return "new purchase screen"
