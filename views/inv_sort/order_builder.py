def build_item_order_by(sort_cfg: dict | None, default_order_by: str, sortable_map: dict) -> str:
    """
    - sort_cfg None -> return default_order_by EXACTLY (no behavior change)
    - sortable_map maps keys -> SQL fragments (safe whitelist)
    """
    if not sort_cfg:
        return default_order_by

    parts = []

    k1 = sortable_map.get(sort_cfg["sort_key"])
    if k1:
        parts.append(f"{k1} {sort_cfg['sort_dir']}")

    k2_key = sort_cfg.get("sort_key2") or ""
    k2 = sortable_map.get(k2_key)
    if k2:
        parts.append(f"{k2} {sort_cfg.get('sort_dir2', 'asc')}")

    # stable tie-breaker (safe if alias exists)
    if "item_code" in sortable_map:
        parts.append(f"{sortable_map['item_code']} asc")

    return ", ".join(parts) if parts else default_order_by
