from flask import Blueprint

report_bp = Blueprint("report", __name__, url_prefix="/report")

@report_bp.route("/")
def index():
    return "report screen"

