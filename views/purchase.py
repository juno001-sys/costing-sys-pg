from flask import Blueprint

purchase_bp = Blueprint("purchase", __name__, url_prefix="/purchases")


# 仕入れ入力（home.html の new_purchase に合わせる）
@purchase_bp.route("/new")
def new_purchase():
    return "new purchase screen"


# 仕入れレポート（home.html の purchase_report）
@purchase_bp.route("/report")
def purchase_report():
    return "purchase report"
