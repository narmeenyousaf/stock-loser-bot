print("🚀 Script started...")
# losers_report.py
"""
Fetch TradingView screener via 'tvscreener', filter, and email results.
Configure via environment variables (see README below).
"""

import os
import re
import sys
import smtplib
import datetime
import pandas as pd

# try import tvscreener
try:
    import tvscreener as tvs
except Exception as e:
    print("Missing tvscreener package. Run: pip install tvscreener")
    raise

# --- Config from env ---
FROM_EMAIL = os.getenv("FROM_EMAIL")              # your email address
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", FROM_EMAIL)
SMTP_PASS = os.getenv("SMTP_PASS")                # Gmail app password
TO_EMAIL = os.getenv("TO_EMAIL")              # comma-separated
RUN_TYPE = os.getenv("RUN_TYPE", "BOTH").upper()
# Optional friendly labels
NOON_TIME_LABEL = os.getenv("NOON_LABEL", "12:00 CET")
PM_TIME_LABEL = os.getenv("PM_LABEL", "16:00 CET")

# --- Filters (hard-coded per your requirements) ---
NOON_COUNTRIES = ["Germany", "France", "Switzerland", "United Kingdom"]
NOON_CHANGE_THRESHOLD = -3.0        # <= -3%
NOON_MCAP_MIN = 2_000_000_000      # 2B
NOON_MCAP_MAX = 400_000_000_000    # 400B

PM_COUNTRIES = ["USA", "United States", "United States of America"]
PM_CHANGE_THRESHOLD = -3.0
PM_MCAP_MIN = 15_000_000_000       # 15B
PM_MCAP_MAX = 400_000_000_000_000  # 400T (very large upper cap just in case)

# --- Helpers ---
def find_col(df, keywords):
    """Return the first column name whose lowercase contains any keyword."""
    lower_map = {c: c.lower() for c in df.columns}
    for k in keywords:
        for c, lc in lower_map.items():
            if k in lc:
                return c
    return None

def parse_mcap(val):
    """Parse strings like '3.2B', '450M', '1.2T' to numeric (float)."""
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    m = re.match(r"^\s*([\d\.]+)\s*([TMBtmb]?)\s*$", s)
    if not m:
        # try to extract first number
        m2 = re.search(r"([\d\.]+)", s)
        return float(m2.group(1)) if m2 else None
    num = float(m.group(1))
    suffix = m.group(2).upper()
    if suffix == "T":
        return num * 1e12
    if suffix == "B":
        return num * 1e9
    if suffix == "M":
        return num * 1e6
    return num


def format_mcap(num):
    """Format numeric market cap back into human-friendly string."""
    if num is None or pd.isna(num):
        return ""
    try:
        num = float(num)
    except:
        return str(num)
    if num >= 1e12:
        return f"{num/1e12:.2f}T"
    elif num >= 1e9:
        return f"{num/1e9:.2f}B"
    elif num >= 1e6:
        return f"{num/1e6:.2f}M"
    else:
        return f"{num:.0f}"

def normalize_df(df):
    """Return df with standardized columns: symbol,name,change,mcap,country,close (if found)."""
    cols = {}
    cols['symbol'] = find_col(df, ["symbol", "ticker"])  
    cols['name'] = find_col(df, ["description", "name", "title"])
    cols['change'] = find_col(df, ["change", "% change", "chg"])
    cols['mcap'] = find_col(df, ["market cap", "market_cap", "marketcap", "market_cap_basic"])
    cols['country'] = find_col(df, ["country", "cnt", "exchange"])
    cols['close'] = find_col(df, ["close", "last", "last price", "price"])
    
    out = pd.DataFrame()
    for k, c in cols.items():
        if c and c in df.columns:
            out[k] = df[c]
        else:
            out[k] = None

    # --- parse change safely ---
    def parse_change(v):
        if pd.isna(v): return None
        if isinstance(v, (int, float)): return float(v)
        s = str(v).strip().replace("%", "")
        try:
            return float(s)
        except:
            m = re.search(r"-?[\d\.]+", s)
            return float(m.group(0)) if m else None

    if 'change' in out.columns and out['change'].notnull().any():
        out['change_pct'] = out['change'].apply(parse_change)
    else:
        out['change_pct'] = pd.Series([None] * len(out))

    # --- market cap ---
    if 'mcap' in out.columns and out['mcap'].notnull().any():
        out['mcap_num'] = out['mcap'].apply(parse_mcap)
    else:
        out['mcap_num'] = pd.Series([None] * len(out))

    out['country_str'] = out['country'].astype(str).fillna("").str.strip()
    out['_orig'] = df.index
    return out


def fetch_screener_dataframe():
    """Use tvscreener StockScreener to get a DataFrame (pandas)."""
    ss = tvs.StockScreener()
    # try to fetch; tvscreener returns a pandas DataFrame
    df = ss.get()  # default size; usually returns hundreds of rows
    return df

def filter_by_rules(df_norm, countries, change_thr, mcap_min, mcap_max):
    # country match (case-insensitive contains any of list)
    pattern = "|".join([re.escape(c) for c in countries])
    cond_country = df_norm['country_str'].str.contains(pattern, case=False, na=False)
    cond_change = df_norm['change_pct'].notnull() & (df_norm['change_pct'] <= change_thr)
    cond_mcap = df_norm['mcap_num'].notnull() & (df_norm['mcap_num'] >= mcap_min) & (df_norm['mcap_num'] <= mcap_max)
    filtered = df_norm[cond_country & cond_change & cond_mcap].copy()
    # sort by change_pct ascending (biggest negative first)
    filtered = filtered.sort_values(by='change_pct')
    return filtered

def df_to_html_table(filtered, orig_df):
    """Return a small HTML table for email containing symbol, name, change, mcap, close."""
    if filtered.empty:
        return "<p>No results</p>"

    rows = []
    for idx, r in filtered.iterrows():
        i = r['_orig']
        # gather fields from orig_df if present
        sym = r.get('symbol') or orig_df.index[i] if i in orig_df.index else r.get('symbol') or ""
        name = r.get('name') or ""
        close = r.get('close') or ""
        change = r.get('change_pct') or ""
        mcap_raw = r.get('mcap') or ""
        rows.append({
            "Symbol": sym,
            "Name": name,
            "Close": close,
           "Change %": f"{change:.3f}%" if change is not None else "",
           "Market Cap": format_mcap(r.get('mcap_num'))
        })
    df_out = pd.DataFrame(rows)
    return df_out.to_html(index=False, escape=False)

def send_email_html(subject, html_body):
    """Send HTML email (simple). TO_EMAIL can be comma separated list."""
    if not (FROM_EMAIL and SMTP_PASS and TO_EMAIL):
        raise RuntimeError("Missing FROM_EMAIL / SMTP_PASS / TO_EMAIL environment variables.")

    message = f"From: {FROM_EMAIL}\r\nTo: {TO_EMAIL}\r\nSubject: {subject}\r\nMIME-Version: 1.0\r\nContent-Type: text/html\r\n\r\n{html_body}"
    recipients = [e.strip() for e in TO_EMAIL.split(",") if e.strip()]

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(FROM_EMAIL, recipients, message.encode('utf-8'))

def main():
    print("Starting fetch at", datetime.datetime.utcnow().isoformat(), "UTC")
    df = fetch_screener_dataframe()
    df_norm = normalize_df(df)
  
    noon_filtered = filter_by_rules(df_norm, NOON_COUNTRIES, NOON_CHANGE_THRESHOLD, NOON_MCAP_MIN, NOON_MCAP_MAX)
    pm_filtered = filter_by_rules(df_norm, PM_COUNTRIES, PM_CHANGE_THRESHOLD, PM_MCAP_MIN, PM_MCAP_MAX)

    parts = []
    if RUN_TYPE in ("NOON", "BOTH"):
        parts.append(f"<h2>Market Losers — {NOON_TIME_LABEL}</h2>")
        parts.append(df_to_html_table(noon_filtered, df))
    if RUN_TYPE in ("PM", "BOTH"):
        parts.append(f"<h2>Market Losers — {PM_TIME_LABEL}</h2>")
        parts.append(df_to_html_table(pm_filtered, df))

    subject = f"Daily Market Losers — {datetime.date.today().isoformat()}"
    html_body = "<html><body>" + "<br><br>".join(parts) + "</body></html>"

    send_email_html(subject, html_body)
    print("Email sent:", subject)

if __name__ == "__main__":
    main()
