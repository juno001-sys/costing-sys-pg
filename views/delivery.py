from flask import Blueprint

delivery_bp = Blueprint("delivery", __name__, url_prefix="/delivery")

@delivery_bp.route("/")
def index():
    return "delivery screen"

