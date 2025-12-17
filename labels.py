from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


LABEL_DIR = Path(__file__).resolve().parent / "labels"


def _current_lang() -> str:
    """
    Priority:
      1) LABEL_LANG (explicit)
      2) APP_LANG
      3) default: ja
    """
    lang = (os.environ.get("LABEL_LANG") or os.environ.get("APP_LANG") or "ja").strip().lower()
    if lang.startswith("en"):
        return "en"
    if lang.startswith("ja"):
        return "ja"
    return lang  # allow custom later


@lru_cache(maxsize=32)
def _load_labels(lang: str) -> dict[str, str]:
    path = LABEL_DIR / f"{lang}.json"
    if not path.exists():
        # Fallback: ja
        path = LABEL_DIR / "ja.json"
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # only keep string values
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def label(key: str, default: str | None = None, lang: str | None = None) -> str:
    """
    Safe label lookup:
      - If key missing: return default if provided, else return key itself.
      - Never raises (to avoid template 500s).
    """
    use_lang = (lang or _current_lang()).strip().lower()
    labels = _load_labels(use_lang)
    if key in labels:
        return labels[key]
    if default is not None:
        return default
    return key
