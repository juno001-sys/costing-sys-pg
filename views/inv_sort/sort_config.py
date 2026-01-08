def get_item_sort_config(conn, store_id: int):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sort_key, sort_dir, sort_key2, sort_dir2
            FROM inventory_item_sort_config
            WHERE store_id = %s
        """, (store_id,))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "sort_key": row[0],
        "sort_dir": row[1],
        "sort_key2": row[2],
        "sort_dir2": row[3],
    }


def save_item_sort_config(conn, store_id: int, cfg: dict):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO inventory_item_sort_config
              (store_id, sort_key, sort_dir, sort_key2, sort_dir2, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (store_id)
            DO UPDATE SET
              sort_key   = EXCLUDED.sort_key,
              sort_dir   = EXCLUDED.sort_dir,
              sort_key2  = EXCLUDED.sort_key2,
              sort_dir2  = EXCLUDED.sort_dir2,
              updated_at = NOW()
        """, (
            store_id,
            cfg["sort_key"],
            cfg["sort_dir"],
            cfg.get("sort_key2"),
            cfg.get("sort_dir2"),
        ))
