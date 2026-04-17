"""
In-app Help / User Manual.

Replaces the static PDF (static/docs/user_manual.pdf). Chapters are Jinja
templates under templates/help/ so they can embed live url_for() links
and stay in sync with the code.

Audience: non-technical users (sales staff, site operators). All text is
in labels/ja.json + labels/en.json; prose body uses per-language template
includes so non-Japanese staff can read it in English.
"""

from flask import Blueprint, render_template, abort

help_bp = Blueprint("help", __name__, url_prefix="/help")


# Chapter registry — single source of truth for sidebar TOC + routing.
# slug must match the template filename (templates/help/<slug>.html).
CHAPTERS = [
    # slug,                     i18n title key,                       group
    ("index",                   "help.chapters.index",                "intro"),
    ("nav_map",                 "help.chapters.nav_map",              "intro"),
    ("concept_count_vs_order",  "help.chapters.concept_count_vs_order", "concepts"),
    ("setup_shelves",           "help.chapters.setup_shelves",        "setup"),
    ("setup_suppliers",         "help.chapters.setup_suppliers",      "setup"),
    ("setup_items",             "help.chapters.setup_items",          "setup"),
    ("setup_revenue",           "help.chapters.setup_revenue",        "setup"),
]
VALID_SLUGS = {c[0] for c in CHAPTERS}


@help_bp.route("/")
def index():
    return render_template("help/index.html", chapters=CHAPTERS, active="index")


@help_bp.route("/<slug>")
def topic(slug):
    if slug not in VALID_SLUGS:
        abort(404)
    return render_template(f"help/{slug}.html", chapters=CHAPTERS, active=slug)


def init_help_views(app):
    app.register_blueprint(help_bp)
