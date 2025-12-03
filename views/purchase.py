
from flask import Blueprint, render_template

purchase_bp = Blueprint("purchase", __name__, url_prefix="/purchases")

@purchase_bp.route("/")
def index():
    return "purchase screen"
