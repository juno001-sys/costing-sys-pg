"""
Sys-admin internal references / runbooks.

Separate from the operator-facing /help section. Lives under
/admin/system/help and is gated by sys_role_required so operators
never see it.

Chapters are Jinja templates under templates/admin/system_help/<slug>.html
so they can embed live url_for() links and stay in sync with the code.

V1 chapters (2026-04-21):
  index                       — TOC
  sys_admin_overview          — the 4 sys roles, what each can do
  onboarding_client           — full new-client setup workflow
  health_interpretation       — Client Health badges, what to do per status
  patch_deployment            — local + dev → smoke test → PROD workflow
"""
from __future__ import annotations

from flask import abort, render_template

from utils.sys_roles import sys_role_required


# Chapter registry — single source of truth for sidebar TOC + routing.
# slug must match templates/admin/system_help/<slug>.html
CHAPTERS = [
    # slug,                       title,                                  group
    ("index",                     "References Home",                      "intro"),
    ("sys_admin_overview",        "Sys Admin Roles & Permissions",        "concepts"),
    ("health_interpretation",     "Client Health: Reading the Badges",    "concepts"),
    ("onboarding_client",         "Onboarding a New Client Company",      "workflows"),
    ("patch_deployment",          "Patch Deployment Workflow",            "workflows"),
]
VALID_SLUGS = {c[0] for c in CHAPTERS}


def init_admin_system_help_views(app, get_db):

    @app.get("/admin/system/help")
    @sys_role_required("engineer", "sales", "accounting")
    def admin_system_help_index():
        return render_template(
            "admin/system_help/index.html",
            chapters=CHAPTERS,
            active="index",
        )

    @app.get("/admin/system/help/<slug>")
    @sys_role_required("engineer", "sales", "accounting")
    def admin_system_help_topic(slug):
        if slug not in VALID_SLUGS:
            abort(404)
        return render_template(
            f"admin/system_help/{slug}.html",
            chapters=CHAPTERS,
            active=slug,
        )
