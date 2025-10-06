# losers_report.py
print("🚀 Script started...")

import os
import re
import smtplib
import datetime
import random
import requests
import pandas as pd

# --- Config from env ---
FROM_EMAIL = os.getenv("FROM_EMAIL")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", FROM_EMAIL)
SMTP_PASS = os.getenv("SMTP_PASS")
TO_EMAIL = os.getenv("TO_EMAIL")
RUN_TYPE = os.getenv("RUN_TYPE", "BOTH").upper()  # NOON / PM / BOTH
NOON_TIME_LABEL = os.getenv("NOON_LABEL", "12:00 CET")
PM_TIME_LABEL = os.getenv("PM_LABEL", "16:00 CET")

# --- Filters ---
NOON_COUNTRIES = ["Germany", "France", "Switzerland", "United Kingdom"]
NOON_CHANGE_THRESHOLD = -3.0
NOON_MCAP_MIN = 3_000_000_000       # 3B
NOON_MCAP_MAX = 400_000_000_000     # 400B

PM_COUNTRIES = ["United States", "USA", "United States of America"]
PM_CHANGE_THRESHOLD = -3.0
PM_MCAP_MIN = 10_000_000_000        # 10B
PM_MCAP_MAX = 4_500_000_000_000     # 4.5T

# --- Helpers ---
def find_col(df, keywords):
    lower_map = {c: c.lower() for c in df.columns}
    for k in keywords:
        for c, lc in lower_map.items():
            if k in lc:
                return c
    return None

def parse_mcap(val):
    if pd.isna(val): return None
    if isinstance(val, (int, float)): return float(val)
    s = str(val).strip().replace(",", "")
    m = re.match(r"^\s*([\d\.]+)\s*([TMB]?)\s*$", s)
    if not m:
        m2 = re.search(r"([\d\.]+)", s)
        return float(m2.group(1)) if m2 else None
    num = float(m.group(1))
    suffix = m.group(2).upper()
    if suffix == "T": return num * 1e12
    if suffix == "B": return num * 1e9
    if suffix == "M": return num * 1e6
    return num

def format_mcap(num):
    if num is None or pd.isna(num): return ""
    num = float(num)
    if num >= 1e12: return f"{num/1e12:.2f}T"
    elif num >= 1e9: return f"{num/1e9:.2f}B"
    elif num >= 1e6: return f"{num/1e6:.2f}M"
    else: return f"{num:.0f}"

def normalize_df(df):
    cols = {}
    cols['symbol'] = find_col(df, ["symbol", "s", "ticker"])
    cols['name'] = find_col(df, ["description", "name", "title", "short_name"])
    cols['change'] = find_col(df, ["change %", "change", "change_pct"])
    cols['mcap'] = find_col(df, ["market capitalization", "market_cap_basic"])
    cols['country'] = find_col(df, ["country", "exchange"])
    cols['close'] = find_col(df, ["price", "close", "last"])

    out = pd.DataFrame()
    for k, c in cols.items():
        out[k] = df[c] if (c and c in df.columns) else None

    def parse_change(v):
        if pd.isna(v): return None
        if isinstance(v, (int, float)): return float(v)
        s = str(v).strip().replace("%", "")
        try: return float(s)
        except: 
            m = re.search(r"-?[\d\.]+", s)
            return float(m.group(0)) if m else None

    out['change_pct'] = out['change'].apply(parse_change)
    out['mcap_num'] = out['mcap'].apply(parse_mcap)
    out['country_str'] = out['country'].astype(str).fillna("").str.strip()
    out['_orig'] = df.index
    return out

def fetch_tradingview_screener(region):
    """Fetch TradingView screener with proper filters by region."""
    url = "https://scanner.tradingview.com/screener"
    payload = {
        "filter": [
            {"left": "country", "operation": "equal", "right": region}
        ],
        "options": {"lang": "en"},
        "columns": [
            "symbol", "description", "close", "change", "change_abs",
            "market_cap_basic", "country"
        ]
    }

    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        body = r.json()
        data = body.get("data", [])
        rows = []
        for item in data:
            symbol = item.get("s")
            vals = item.get("d", [])
            row = {
                "symbol": symbol,
                "description": vals[1] if len(vals) > 1 else None,
                "close": vals[2] if len(vals) > 2 else None,
                "change": vals[3] if len(vals) > 3 else None,
                "change_abs": vals[4] if len(vals) > 4 else None,
                "market_cap_basic": vals[5] if len(vals) > 5 else None,
                "country": vals[6] if len(vals) > 6 else None
            }
            rows.append(row)
        return pd.DataFrame(rows)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch screener data for {region}: {e}")

    
    full_url = f"{url}?_={random.randint(100000,999999)}"
    r = requests.post(full_url, json=payload, timeout=30)
    r.raise_for_status()
    body = r.json()
    data = body.get("data", [])
    rows = []
    for item in data:
        symbol = item.get("s")
        vals = item.get("d", [])
        row = {}
        cols = payload["columns"]
        for i, col in enumerate(cols):
            row[col] = vals[i] if i < len(vals) else None
        row["symbol"] = symbol or row.get("symbol")
        rows.append(row)
    return pd.DataFrame(rows)

def filter_by_rules(df_norm, countries, change_thr, mcap_min, mcap_max):
    pattern = "|".join([re.escape(c) for c in countries])
    cond_country = df_norm['country_str'].str.contains(pattern, case=False, na=False)
    cond_change = df_norm['change_pct'].notnull() & (df_norm['change_pct'] <= change_thr)
    cond_mcap = df_norm['mcap_num'].notnull() & (df_norm['mcap_num'] >= mcap_min) & (df_norm['mcap_num'] <= mcap_max)
    filtered = df_norm[cond_country & cond_change & cond_mcap].copy()
    filtered = filtered.sort_values(by='change_pct')
    return filtered

def df_to_html_table(filtered, orig_df):
    if filtered.empty: return "<p>No results</p>"
    rows = []
    for idx, r in filtered.iterrows():
        i = r['_orig']
        sym = r.get('symbol') or ""
        name = r.get('name') or ""
        close_val = r.get('close')
        change = r.get('change_pct')
        rows.append({
            "Symbol": sym,
            "Name": name,
            "Close": f"{float(close_val):.2f}" if close_val not in (None, "") else "",
            "Change %": f"{change:.1f}%" if change is not None else "",
            "Market Cap": format_mcap(r.get('mcap_num'))
        })
    return pd.DataFrame(rows).to_html(index=False, escape=False)

def send_email_html(subject, html_body):
    if not (FROM_EMAIL and SMTP_PASS and TO_EMAIL):
        raise RuntimeError("Missing FROM_EMAIL / SMTP_PASS / TO_EMAIL env vars.")
    message = f"From: {FROM_EMAIL}\r\nTo: {TO_EMAIL}\r\nSubject: {subject}\r\nMIME-Version: 1.0\r\nContent-Type: text/html\r\n\r\n{html_body}"
    recipients = [e.strip() for e in TO_EMAIL.split(",") if e.strip()]
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(FROM_EMAIL, recipients, message.encode('utf-8'))

def main():
    print("Starting fetch at", datetime.datetime.utcnow().isoformat(), "UTC")
    parts = []

    # NOON (Europe)
    if RUN_TYPE in ("NOON", "BOTH"):
        for market in ["uk", "france", "germany", "switzerland"]:
            df = fetch_tradingview_screener(market)
            df_norm = normalize_df(df)
            noon_filtered = filter_by_rules(df_norm, NOON_COUNTRIES, NOON_CHANGE_THRESHOLD, NOON_MCAP_MIN, NOON_MCAP_MAX)
            parts.append(f"<h2>Market Losers — {NOON_TIME_LABEL} ({market.upper()})</h2>")
            parts.append(df_to_html_table(noon_filtered, df))

    # PM (USA)
    if RUN_TYPE in ("PM", "BOTH"):
        df = fetch_tradingview_screener("america")
        df_norm = normalize_df(df)
        pm_filtered = filter_by_rules(df_norm, PM_COUNTRIES, PM_CHANGE_THRESHOLD, PM_MCAP_MIN, PM_MCAP_MAX)
        parts.append(f"<h2>Market Losers — {PM_TIME_LABEL} (USA)</h2>")
        parts.append(df_to_html_table(pm_filtered, df))

    subject = f"Daily Market Losers — {datetime.date.today().isoformat()}"
    html_body = "<html><body>" + "<br><br>".join(parts) + "</body></html>"
    send_email_html(subject, html_body)
    print("✅ Email sent:", subject)

if __name__ == "__main__":
    main()
