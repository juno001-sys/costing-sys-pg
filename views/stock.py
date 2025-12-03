
from flask import Blueprint

stock_bp = Blueprint("stock", __name__, url_prefix="/stock")

@stock_bp.route("/")
def index():
    return "stock screen"
