SORTABLE_COLUMNS = {
    "item_code": "i.code",
    "item_name": "i.name",
}


def build_order_by(sort_cfg: dict | None) -> str:
    # Legacy behavior MUST remain i.code if no config
    if not sort_cfg:
        return "i.code"

    parts = []

    k1 = SORTABLE_COLUMNS.get(sort_cfg["sort_key"])
    if k1:
        parts.append(f"{k1} {sort_cfg['sort_dir']}")

    k2 = SORTABLE_COLUMNS.get(sort_cfg.get("sort_key2") or "")
    if k2:
        parts.append(f"{k2} {sort_cfg.get('sort_dir2','asc')}")

    parts.append("i.code asc")
    return ", ".join(parts)
