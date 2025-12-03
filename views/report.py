from flask import Blueprint

report_bp = Blueprint("report", __name__, url_prefix="/report")

@report_bp.route("/usage")
def usage_report():
    return "usage report"

@report_bp.route("/cost")
def cost_report():
    return "cost report"

@report_bp.route("/suppliers")
def suppliers_master():
    return "suppliers master"

@report_bp.route("/items")
def items_master():
    return "items master"
