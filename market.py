"""
Market snapshot from free, no-key endpoints.
Yahoo Finance chart API first (it rate-limits GitHub runner IPs), then CNBC's
public quote feed; CoinGecko for BTC. Any instrument that fails everywhere
comes back with price None and is shown as "n/a".
"""

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (NewsDigestBot/1.0)"}

# (label, yahoo symbol, cnbc symbol, decimals)
INSTRUMENTS = [
    ("S&P 500", "^GSPC", ".SPX", 2),
    ("Nasdaq", "^IXIC", ".IXIC", 2),
    ("Nifty 50", "^NSEI", ".NSEI", 2),
    ("TSX", "^GSPTSE", ".GSPTSE", 2),
    ("USD/INR", "INR=X", "INR=", 3),
    ("USD/CAD", "CAD=X", "CAD=", 4),
    ("US Dollar Index", "DX-Y.NYB", ".DXY", 2),
    ("Gold", "GC=F", "@GC.1", 2),
    ("WTI Crude", "CL=F", "@CL.1", 2),
]
CNBC_BTC = "BTC.CM="
CNBC_API = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"

_yahoo_blocked = False


def _yahoo_quote(symbol: str):
    """Return (price, prev_close) from the Yahoo chart endpoint."""
    global _yahoo_blocked
    if _yahoo_blocked:
        return None, None
    for host in ("query1", "query2"):
        try:
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
            resp = requests.get(url, params={"range": "5d", "interval": "1d"},
                                headers=HEADERS, timeout=10)
            if resp.status_code == 429:
                print(f"[MARKET] Yahoo rate-limited via {host}")
                continue
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
    if not _yahoo_blocked:
        _yahoo_blocked = True
        print("[MARKET] Yahoo unavailable — switching to CNBC for the rest")
    return None, None


def _num(text):
    try:
        return float(str(text).replace(",", "").replace("%", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def _cnbc_quotes(symbols):
    """{cnbc symbol: (price, change_pct)} from CNBC's public quote feed (one request)."""
    try:
        resp = requests.get(CNBC_API, headers=HEADERS, timeout=15, params={
            "symbols": "|".join(symbols), "requestMethod": "itv", "noform": 1,
            "partnerId": 2, "fund": 1, "exthrs": 1, "output": "json",
        })
        resp.raise_for_status()
        quotes = resp.json()["FormattedQuoteResult"]["FormattedQuote"]
        if isinstance(quotes, dict):
            quotes = [quotes]
        out = {}
        for q in quotes:
            price = _num(q.get("last"))
            change = 0.0 if q.get("change_pct") == "UNCH" else _num(q.get("change_pct"))
            if price is not None:
                out[q.get("symbol")] = (price, change)
        return out
    except Exception as e:
        print(f"[MARKET] CNBC: {e}")
        return {}


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
    rows, missing = [], []
    for label, symbol, cnbc, decimals in INSTRUMENTS:
        price, prev = _yahoo_quote(symbol)
        change = ((price - prev) / prev * 100) if (price is not None and prev) else None
        rows.append({"label": label, "price": price, "change_pct": change, "decimals": decimals})
        if price is None:
            missing.append((len(rows) - 1, cnbc))

    price, change = _coingecko_btc()
    rows.append({"label": "BTC", "price": price, "change_pct": change, "decimals": 0})
    if price is None:
        missing.append((len(rows) - 1, CNBC_BTC))

    if missing:
        quotes = _cnbc_quotes([sym for _, sym in missing])
        for idx, sym in missing:
            if sym in quotes:
                rows[idx]["price"], rows[idx]["change_pct"] = quotes[sym]

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
