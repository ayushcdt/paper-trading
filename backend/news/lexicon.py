"""
Loughran-McDonald finance-specific sentiment lexicon.

Loads from data/lm_master_dictionary.csv (downloaded via
scripts/download_lm_dict.py) and exposes per-word sentiment flags.

Categories used:
  NEGATIVE  — bankruptcy, loss, decline, fraud, etc.
  POSITIVE  — growth, gain, beat, rally, etc.
  UNCERTAINTY — maybe, could, depend, approximately
  LITIGIOUS — lawsuit, indicted, penalty, sanction

For each article we compute a finance-specific sentiment score:
  score = (pos_hits - neg_hits) - 0.3*unc_hits - 0.5*lit_hits
  (uncertainty/litigious bias slightly negative -- they're typically bad signals)
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional


DICT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "lm_master_dictionary.csv"

_CACHE: dict[str, dict[str, int]] | None = None
_WORD_RE = re.compile(r"\b[a-zA-Z][a-zA-Z'-]+\b")


def _load() -> dict[str, dict[str, int]]:
    """Load the LM dictionary into {word: {NEGATIVE, POSITIVE, UNCERTAINTY, LITIGIOUS}}."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not DICT_PATH.exists():
        _CACHE = {}
        return _CACHE
    d: dict[str, dict[str, int]] = {}
    with DICT_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # SRAF's real CSV uses columns like Negative, Positive, Uncertainty, Litigious
        # (with numeric flags: 0 = not in category, >0 = in category, representing first-seen year)
        fieldnames = {fn.lower(): fn for fn in (reader.fieldnames or [])}
        word_col = fieldnames.get("word", "Word")
        categories = {
            "NEGATIVE":    fieldnames.get("negative", "Negative"),
            "POSITIVE":    fieldnames.get("positive", "Positive"),
            "UNCERTAINTY": fieldnames.get("uncertainty", "Uncertainty"),
            "LITIGIOUS":   fieldnames.get("litigious", "Litigious"),
        }
        for row in reader:
            word = (row.get(word_col) or "").upper().strip()
            if not word:
                continue
            flags = {}
            for cat_name, col_name in categories.items():
                raw = row.get(col_name, "0")
                try:
                    flags[cat_name] = 1 if int(float(raw)) != 0 else 0
                except Exception:
                    flags[cat_name] = 1 if raw and raw != "0" else 0
            # Only cache words that belong to at least one category
            if any(flags.values()):
                d[word] = flags
    _CACHE = d
    return d


def lexicon_stats() -> dict:
    """Return counts per category (for diagnostics)."""
    d = _load()
    stats = {"total_words": len(d)}
    for cat in ("NEGATIVE", "POSITIVE", "UNCERTAINTY", "LITIGIOUS"):
        stats[cat.lower()] = sum(1 for w in d.values() if w.get(cat))
    return stats


def score_text(text: str) -> dict:
    """
    Score a piece of text. Returns:
      {
        pos: int   (count of positive words)
        neg: int   (count of negative words)
        unc: int   (count of uncertainty words)
        lit: int   (count of litigious words)
        net: float (combined sentiment; see formula in module docstring)
      }
    """
    if not text:
        return {"pos": 0, "neg": 0, "unc": 0, "lit": 0, "net": 0.0}
    d = _load()
    if not d:
        return {"pos": 0, "neg": 0, "unc": 0, "lit": 0, "net": 0.0}

    pos = neg = unc = lit = 0
    for token in _WORD_RE.findall(text):
        flags = d.get(token.upper())
        if not flags:
            continue
        pos += flags.get("POSITIVE", 0)
        neg += flags.get("NEGATIVE", 0)
        unc += flags.get("UNCERTAINTY", 0)
        lit += flags.get("LITIGIOUS", 0)

    # Net score: positive minus negative, with smaller penalty for uncertainty/litigious
    net = (pos - neg) - 0.3 * unc - 0.5 * lit
    return {"pos": pos, "neg": neg, "unc": unc, "lit": lit, "net": round(net, 2)}


if __name__ == "__main__":
    print("Lexicon stats:", lexicon_stats())
    sample = "HDFC Bank share price falls after Q4 results miss. Concerns about asset quality drag stock to lower levels. Analysts express caution."
    print(f"Sample: {sample[:80]!r}")
    print("Score:", score_text(sample))
