from flask import Blueprint

stock_bp = Blueprint("stock", __name__, url_prefix="/inventory")

@stock_bp.route("/count")
def inventory_count():
    return "inventory count"
