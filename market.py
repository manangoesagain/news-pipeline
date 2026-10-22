"""
Market snapshot from free, no-key endpoints.
Yahoo Finance chart API for indices/FX/commodities, CoinGecko for BTC.
Any instrument that fails comes back with price None and is shown as "n/a".
"""

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (NewsDigestBot/1.0)"}

# (label, yahoo symbol, decimals)
INSTRUMENTS = [
    ("S&P 500", "^GSPC", 2),
    ("Nasdaq", "^IXIC", 2),
    ("Nifty 50", "^NSEI", 2),
    ("TSX", "^GSPTSE", 2),
    ("USD/INR", "INR=X", 3),
    ("USD/CAD", "CAD=X", 4),
    ("Gold", "GC=F", 2),
    ("WTI Crude", "CL=F", 2),
]


def _yahoo_quote(symbol: str):
    """Return (price, prev_close) from the Yahoo chart endpoint."""
    for host in ("query1", "query2"):
        try:
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
            resp = requests.get(url, params={"range": "5d", "interval": "1d"},
                                headers=HEADERS, timeout=10)
            resp.raise_for_status()
            meta = resp.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            # chartPreviousClose is the close before the 5d range; prefer the
            # second-to-last daily close when available
            closes = resp.json()["chart"]["result"][0]["indicators"]["quote"][0].get("close") or []
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                prev = closes[-2]
            if price is not None:
                return float(price), (float(prev) if prev else None)
        except Exception as e:
            print(f"[MARKET] {symbol} via {host}: {e}")
    return None, None


def _coingecko_btc():
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"},
            headers=HEADERS, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()["bitcoin"]
        return float(data["usd"]), float(data.get("usd_24h_change") or 0.0)
    except Exception as e:
        print(f"[MARKET] BTC via CoinGecko: {e}")
        return None, None


def get_snapshot() -> list:
    """List of {label, price, change_pct, decimals}; price/change None if unavailable."""
    rows = []
    for label, symbol, decimals in INSTRUMENTS:
        price, prev = _yahoo_quote(symbol)
        change = ((price - prev) / prev * 100) if (price is not None and prev) else None
        rows.append({"label": label, "price": price, "change_pct": change, "decimals": decimals})

    price, change = _coingecko_btc()
    if price is None:
        price, prev = _yahoo_quote("BTC-USD")
        change = ((price - prev) / prev * 100) if (price is not None and prev) else None
    rows.append({"label": "BTC", "price": price, "change_pct": change, "decimals": 0})

    ok = sum(1 for r in rows if r["price"] is not None)
    print(f"[MARKET] {ok}/{len(rows)} instruments loaded")
    return rows


def fmt_price(row) -> str:
    if row.get("price") is None:
        return "n/a"
    return f"{row['price']:,.{row.get('decimals', 2)}f}"


def fmt_change(row) -> str:
    if row.get("change_pct") is None:
        return ""
    c = row["change_pct"]
    arrow = "▲" if c > 0 else ("▼" if c < 0 else "•")
    return f"{arrow} {c:+.2f}%"


if __name__ == "__main__":
    for r in get_snapshot():
        print(f"{r['label']:<10} {fmt_price(r):>12} {fmt_change(r)}")
