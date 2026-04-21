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

Each sys admin holds a SET of roles (PostgreSQL TEXT[]). At the early
stage one person often wears multiple hats — e.g. founder is both sales
AND accounting. Multi-role keeps that explicit instead of forcing a
super_admin grant.

This module is import-safe BEFORE the migration runs:
get_current_sys_roles() falls back to ['super_admin'] for every user
that has is_system_admin = TRUE if the column doesn't exist yet
(deny-nothing fallback for the transition window).
"""
from __future__ import annotations

from functools import wraps
from typing import Iterable, List, Optional

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
    "admin_system_assign_sys_role":     (),  # super_admin only
    "admin_system_sys_admin_new":       (),  # super_admin only
    "admin_system_help_index":          ("engineer", "sales", "accounting"),
    "admin_system_help_topic":          ("engineer", "sales", "accounting"),
}


def _normalize_roles(value) -> List[str]:
    """Coerce whatever shape sys_role comes in (PG list, comma string,
    None) into a normalized list[str]. Defensive — the DB driver might
    return arrays as Python lists OR as strings depending on type info."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(r).strip().lower() for r in value if r]
    if isinstance(value, str):
        # Could be 'super_admin' (legacy varchar) OR '{a,b}' (PG array literal)
        s = value.strip()
        if s.startswith("{") and s.endswith("}"):
            inner = s[1:-1]
            return [r.strip().strip('"').lower() for r in inner.split(",") if r.strip()]
        if "," in s:
            return [r.strip().lower() for r in s.split(",") if r.strip()]
        return [s.lower()] if s else []
    return [str(value).strip().lower()]


def get_current_sys_roles() -> List[str]:
    """Return the current logged-in sys-admin's roles, or [] if not a
    sys-admin. Defaults to ['super_admin'] for sys-admins on a database
    that hasn't been migrated yet."""
    user = getattr(g, "current_user", None)
    if not user or not user.get("is_system_admin"):
        return []
    raw = user.get("sys_role") if "sys_role" in user else user.get("sys_roles")
    roles = _normalize_roles(raw)
    return roles or ["super_admin"]


def get_current_sys_role() -> Optional[str]:
    """Backward-compat: returns the FIRST role, or None. Prefer
    get_current_sys_roles() in new code."""
    roles = get_current_sys_roles()
    return roles[0] if roles else None


def is_super_admin() -> bool:
    return "super_admin" in get_current_sys_roles()


def has_any_sys_role(*roles: str) -> bool:
    """True if the current sys admin has at least one of `roles` (or is
    super_admin, which is implicit access to everything)."""
    current = set(get_current_sys_roles())
    if not current:
        return False
    if "super_admin" in current:
        return True
    return bool(current & set(roles))


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
            current_roles = set(get_current_sys_roles())
            if not (current_roles & allowed):
                flash(f"Access denied — this screen requires one of: {', '.join(sorted(allowed))}.")
                return redirect(url_for("admin_system_home"))
            return fn(*args, **kwargs)
        return wrapper
    return deco


def can_access_screen(endpoint: str) -> bool:
    """Helper for templates — used by the admin tabs partial to hide
    tabs the current sys-admin doesn't have access to."""
    roles = set(get_current_sys_roles())
    if not roles:
        return False
    if "super_admin" in roles:
        return True
    extra_roles = set(SCREEN_ROLES.get(endpoint, ()))
    return bool(roles & extra_roles)
