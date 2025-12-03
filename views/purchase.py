from flask import Blueprint

purchase_bp = Blueprint("purchase", __name__, url_prefix="/purchase")

@purchase_bp.route("/new")
def new_purchase():
    return "new purchase"
