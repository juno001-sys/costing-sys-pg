"""
Sys-admin role split — Super Admin / Engineer / Sales / Accounting.

This is the sys-admin equivalent of the company-side function roles
(Chief Admin / Admin / Operator / Supervisor). Same Google-style spirit:

  super_admin   = full access to every sys-admin screen
                  + the only role that can promote/demote other sys-admins
  engineer      = developer dashboard, error logs, system internals
  sales         = customer health, contract tier setting,
                  client onboarding tooling
  accounting    = invoices, payments, billing reports

This module is import-safe BEFORE the migration runs:
get_current_sys_role() falls back to 'super_admin' for every user that
has is_system_admin = TRUE if the sys_role column doesn't exist yet
(deny-nothing fallback for the transition window).
"""
from __future__ import annotations

from functools import wraps
from typing import Optional

from flask import flash, g, redirect, request, url_for


# Canonical role list. Add new roles here as the team grows.
SYS_ROLES = ("super_admin", "engineer", "sales", "accounting")

# Default access matrix per sys-admin screen.
# Update when a new sys-admin route is added; super_admin always has access
# implicitly (sys_role_required adds it automatically), so don't list it.
SCREEN_ROLES: dict[str, tuple[str, ...]] = {
    "admin_system_home":                ("engineer", "sales", "accounting"),
    "admin_system_company_features":    ("sales",),
    "admin_system_company_features_save": ("sales",),
    "admin_system_health_overview":     ("sales", "engineer"),
    "admin_system_health_company":      ("sales", "engineer"),
    "admin_system_health_store":        ("sales", "engineer"),
    "admin_system_invoices":            ("accounting",),
    "admin_system_invoice_mark_paid":   ("accounting",),
    "admin_system_invoices_generate":   ("accounting",),
    "dev_dashboard":                    ("engineer",),
    "admin_system_company_new":         (),  # super_admin only
    # Sys-role assignment UI — super_admin only (empty extra list)
    "admin_system_assign_sys_role":     (),
}


def get_current_sys_role() -> Optional[str]:
    """Return the current logged-in sys-admin's sys_role, or None if not
    a sys-admin. Defaults to 'super_admin' for sys-admins on a database
    that hasn't been migrated yet."""
    user = getattr(g, "current_user", None)
    if not user or not user.get("is_system_admin"):
        return None
    return (user.get("sys_role") or "super_admin").strip().lower()


def sys_role_required(*allowed_roles: str):
    """Decorator: route accessible only to specified sys roles.
    Super Admin is ALWAYS allowed (no need to list it).

    Usage:
        @sys_role_required('sales', 'engineer')
        def some_view(): ...
    """
    allowed = set(allowed_roles) | {"super_admin"}

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if user is None:
                return redirect(url_for("login", next=request.full_path))
            if not user.get("is_system_admin"):
                flash("System admin only.")
                return redirect(url_for("index"))
            role = get_current_sys_role()
            if role not in allowed:
                flash(f"Access denied — this screen requires one of: {', '.join(sorted(allowed))}.")
                return redirect(url_for("admin_system_home"))
            return fn(*args, **kwargs)
        return wrapper
    return deco


def can_access_screen(endpoint: str) -> bool:
    """Helper for templates — used by the admin tabs partial to hide
    tabs the current sys-admin doesn't have access to."""
    role = get_current_sys_role()
    if role is None:
        return False
    if role == "super_admin":
        return True
    extra_roles = SCREEN_ROLES.get(endpoint, ())
    return role in extra_roles
