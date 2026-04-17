"""
Capture screenshots of every CMS screen referenced by the in-app help manual.

Usage:
    pip install playwright && playwright install chromium
    DEV_URL=https://test-costing-sys.up.railway.app \
    DEV_USER=... DEV_PASS=... \
    python3 init/capture_help_screenshots.py

Writes to static/help/screenshots/<name>.png — one file per help figure.
Run it again whenever the UI changes; images are git-tracked so CMS users
always see current screens.

Notes
-----
- Uses a regular Normal User login (not System Admin).
- Picks the FIRST accessible store wherever a store selector exists.
- Waits for screen-specific anchor elements before each shot so we don't
  catch a half-rendered page.
- Screenshots are taken at 1440x900 viewport to match desktop default.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit(
        "playwright not installed. Run:\n"
        "  pip install playwright && playwright install chromium"
    )


DEV_URL  = os.environ.get("DEV_URL",  "https://test-costing-sys.up.railway.app").rstrip("/")
DEV_USER = os.environ.get("DEV_USER")
DEV_PASS = os.environ.get("DEV_PASS")

if not DEV_USER or not DEV_PASS:
    sys.exit("DEV_USER and DEV_PASS env vars are required.")

OUT_DIR = Path("static/help/screenshots")
OUT_DIR.mkdir(parents=True, exist_ok=True)

VIEWPORT = {"width": 1440, "height": 900}


# ─── Screens to capture ─────────────────────────────────────────────────────
# (slug, URL path (absolute or relative to DEV_URL), wait-for selector, notes)
# - URL with {store_id} will be formatted with the first accessible store id
# - wait_for may be None; then we wait for DOM content loaded only.
SHOTS = [
    # -- Concept: count vs order (2 images for side-by-side diagram) --
    ("count_screen",         "/inventory/count_v2?store_id={store_id}", "table, .card-form, .zone-section", "Inventory count v2"),
    ("order_support_sheet",  "/order-support?store_id={store_id}&view=sheet", "table.sheet-table, .no-items-msg", "Order support sheet view"),
    ("order_support_cards",  "/order-support?store_id={store_id}", ".card, table", "Order support card view"),

    # -- Setup: shelves (store edit tabs) --
    ("shelves_temp_tab",     "/inventory/store-temp-zones?store_id={store_id}", "table, .card-form", "Temp zones tab"),
    ("shelves_area_tab",     "/inventory/store-areas?store_id={store_id}",     "table, .card-form", "Areas tab"),
    ("shelves_shelf_tab",    "/inventory/shelves?store_id={store_id}",         "table, .card-form", "Shelves tab"),
    ("shelves_item_assign",  "/mst_items",                                     "table",              "Items master (shelf assignment lives in item edit)"),

    # -- Setup: suppliers --
    ("suppliers_list",       "/suppliers",                                     "table",              "Supplier list"),
    ("suppliers_edit",       None,                                             ".card-form",         "Supplier edit (first row)"),
    ("suppliers_schedule",   None,                                             ".card-form",         "Supplier edit scrolled to schedule"),

    # -- Setup: items --
    ("items_list",           "/mst_items",                                     "table",              "Items master list"),
    ("items_edit_stats",     None,                                             ".card-form",         "Item edit with stats (first item)"),
    ("items_csv_import",     "/mst_items/csv/upload",                          "form, .card-form",   "CSV import"),

    # -- Setup: revenue (reports blueprint has no URL prefix) --
    ("cost_report",          "/cost/report",                                   "table, canvas",      "Monthly cost of sales"),
    ("purchase_dashboard",   "/dashboard",                                     "canvas, table",      "Purchase dashboard with dead stock"),
]


def login(page):
    page.goto(f"{DEV_URL}/login", wait_until="domcontentloaded")
    # Normal user form is the first one on the page
    page.locator("input[name='email']").first.fill(DEV_USER)
    page.locator("input[name='password']").first.fill(DEV_PASS)
    page.locator("button:has-text('Login')").first.click()
    page.wait_for_load_state("networkidle")
    if "login" in page.url.lower():
        sys.exit("Login failed — check DEV_USER / DEV_PASS.")
    print(f"[ok] logged in as {DEV_USER}")


def first_store_id(page) -> str | None:
    """Pick the first <option value=...> in any store selector on the home page."""
    page.goto(f"{DEV_URL}/dashboard", wait_until="domcontentloaded")
    try:
        options = page.locator("select[name='store_id'] option").all()
        for opt in options:
            val = opt.get_attribute("value")
            if val and val.strip():
                print(f"[ok] using store_id={val}")
                return val
    except Exception:
        pass
    return None


def shoot(page, slug: str, url: str | None, wait_for: str | None, notes: str, store_id: str | None):
    """Navigate (if url given) and screenshot."""
    if url:
        full = url.format(store_id=store_id or "") if "{store_id}" in url else url
        if full.startswith("/"):
            full = DEV_URL + full
        print(f"[info] {slug:<22} → {full}")
        try:
            page.goto(full, wait_until="domcontentloaded", timeout=20000)
        except PWTimeout:
            print(f"[warn] timeout navigating to {full}")
            return

    if wait_for:
        try:
            page.wait_for_selector(wait_for, timeout=8000, state="visible")
        except PWTimeout:
            print(f"[warn] '{wait_for}' not visible for {slug} — shooting anyway")

    # Settle network / fonts
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PWTimeout:
        pass

    out = OUT_DIR / f"{slug}.png"
    page.screenshot(path=str(out), full_page=False)
    print(f"[ok]  saved {out}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT, locale="ja-JP")
        page = context.new_page()

        login(page)
        store_id = first_store_id(page)

        for slug, url, wait_for, notes in SHOTS:
            # Pages that need prior navigation to find the first row to edit
            if slug == "suppliers_edit" or slug == "suppliers_schedule":
                # Go to supplier list, click first "edit" link
                page.goto(f"{DEV_URL}/suppliers", wait_until="domcontentloaded")
                edit_link = page.locator("a:has-text('編集'), a:has-text('Edit')").first
                if edit_link.count():
                    edit_link.click()
                    page.wait_for_load_state("networkidle", timeout=10000)
                if slug == "suppliers_schedule":
                    # Scroll to the delivery schedule section if present
                    schedule = page.locator("text=納品スケジュール, text=Delivery Schedule").first
                    try:
                        schedule.scroll_into_view_if_needed(timeout=3000)
                    except PWTimeout:
                        pass
                shoot(page, slug, None, wait_for, notes, store_id)
                continue

            if slug == "items_edit_stats":
                # Items list → try each edit link until one doesn't 500
                page.goto(f"{DEV_URL}/mst_items", wait_until="domcontentloaded")
                edit_links = page.locator("a:has-text('編集'), a:has-text('Edit')").all()
                captured = False
                for link in edit_links[:5]:  # try at most first 5
                    try:
                        link.click()
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except PWTimeout:
                        continue
                    body_text = (page.locator("body").text_content() or "")[:200]
                    if "Internal Server Error" in body_text or "Not Found" in body_text:
                        page.go_back()
                        page.wait_for_load_state("networkidle", timeout=5000)
                        continue
                    captured = True
                    break
                if not captured:
                    print(f"[warn] no editable item found for {slug} — skipping")
                    continue
                # Scroll to the stats card (発注目安の根拠 / Stats)
                try:
                    page.locator("text=発注目安の根拠, text=Stats").first.scroll_into_view_if_needed(timeout=3000)
                except PWTimeout:
                    pass
                shoot(page, slug, None, wait_for, notes, store_id)
                continue

            shoot(page, slug, url, wait_for, notes, store_id)

        browser.close()
        print(f"\n[done] {len(SHOTS)} screenshots → {OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
