def get_sort_config(db, store_id: int):
    row = db.execute(
        """
        SELECT sort_key, sort_dir, sort_key2, sort_dir2
        FROM inventory_item_sort_config
        WHERE store_id = %s
        """,
        (store_id,),
    ).fetchone()

    if not row:
        return None

    return {
        "sort_key": row["sort_key"],
        "sort_dir": row["sort_dir"],
        "sort_key2": row["sort_key2"],
        "sort_dir2": row["sort_dir2"],
    }


def save_sort_config(db, store_id: int, cfg: dict):
    db.execute(
        """
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
        """,
        (
            store_id,
            cfg["sort_key"],
            cfg["sort_dir"],
            cfg.get("sort_key2"),
            cfg.get("sort_dir2"),
        ),
    )
