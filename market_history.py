"""
Daily market history kept in _data/market_history.json (400 days, one entry
per date, last value of the day wins). Nothing here raises: failures are
logged and ignored so the digest never breaks over history.
"""

import json
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path(__file__).parent / "_data" / "market_history.json"
KEEP_DAYS = 400
CURRENCY_LABELS = ("USD/INR", "USD/CAD", "US Dollar Index")


def _load() -> list:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def record(snapshot, now=None) -> None:
    """Store today's prices; a later call on the same date replaces the earlier one."""
    try:
        now = now or datetime.now()
        date = now.strftime("%Y-%m-%d")
        prices, changes = {}, {}
        for row in snapshot or []:
            if row.get("price") is not None:
                prices[row["label"]] = row["price"]
                if row.get("change_pct") is not None:
                    changes[row["label"]] = row["change_pct"]
        if not prices:
            return
        history = [h for h in _load() if h.get("date") != date]
        history.append({"date": date, "labels": prices, "change_pct": changes})
        history.sort(key=lambda h: h.get("date", ""))
        history = history[-KEEP_DAYS:]
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[MARKET HISTORY] record failed: {e}")


def stats(label, days=7) -> dict:
    """Latest price and move over the last `days` days; None values if under 2 points."""
    out = {"latest": None, "change_pct_period": None, "days_available": 0, "direction": None}
    try:
        points = []
        for h in _load():
            price = (h.get("labels") or {}).get(label)
            if price is not None:
                points.append((h["date"], float(price)))
        if not points:
            return out
        out["latest"] = points[-1][1]
        last_date = datetime.strptime(points[-1][0], "%Y-%m-%d")
        window = [p for p in points
                  if (last_date - datetime.strptime(p[0], "%Y-%m-%d")).days <= days]
        out["days_available"] = len(window)
        if len(window) < 2 or not window[0][1]:
            return out
        pct = (window[-1][1] - window[0][1]) / window[0][1] * 100
        out["change_pct_period"] = pct
        out["direction"] = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    except Exception as e:
        print(f"[MARKET HISTORY] stats failed: {e}")
    return out


def currency_history() -> dict:
    """{label: {..stats.., 'weaker': bool|None}} for the 7-day currency moves.

    For USD/INR and USD/CAD, a rising rate means the rupee / Canadian dollar is
    weaker; 'weaker' is None for the Dollar Index (it is the USD itself) and
    when there is not enough history. 'local_move_pct' is the move in the
    non-USD currency (positive = stronger), approximated as -change.
    """
    out = {}
    for label in CURRENCY_LABELS:
        s = stats(label, 7)
        pct = s["change_pct_period"]
        if label == "US Dollar Index" or pct is None:
            s["weaker"] = None
            s["local_move_pct"] = None
        else:
            s["weaker"] = pct > 0
            s["local_move_pct"] = -pct
        out[label] = s
    return out
