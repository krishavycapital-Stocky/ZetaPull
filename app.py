#!/usr/bin/env python3
"""
ZetaPull Portal
  Tab 1: Expired Options — NIFTY / SENSEX (rollingoption API, date-wise output)
  Tab 2: Futures Historical — NIFTY (historical/intraday API, date-wise output
         with auto contract rollover by SM_EXPIRY_DATE)
  Tab 3: Equity Historical — Bulk daily/intraday for stocks listed in an
         uploaded CSV of SECURITY_ID + UNDERLYING_SYMBOL
Docs: https://dhanhq.co/docs/v2/expired-options-data/
      https://dhanhq.co/docs/v2/historical-data/
     
"""

import os
import csv
import glob
import threading
from collections import defaultdict
from datetime import datetime, timedelta, date as date_cls

import io
import zipfile

import requests
from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "zetapull-change-me-in-production")

BASE_URL = "https://api.dhan.co/v2"

INDEX_CONFIGS = {
    "NIFTY":  {"securityId": "13", "exchangeSegment": "NSE_FNO"},
    "SENSEX": {"securityId": "51", "exchangeSegment": "BSE_FNO"},
}

VALID_INTERVALS = {"1", "5", "15", "25", "60"}

_progress = {
    "running": False,
    "cancelled": False,
    "done": 0,
    "total": 0,
    "pct": 0,
    "msg": "",
    "logs": [],
    "files": [],
    "errors": [],
}
_lock = threading.Lock()


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        _progress["logs"].append(f"[{ts}] {msg}")
        if len(_progress["logs"]) > 500:
            _progress["logs"] = _progress["logs"][-500:]


def fetch_rolling_option(token, index_cfg, from_date, to_date, interval,
                         expiry_flag, expiry_code, strike, option_type):
    """One call to /charts/rollingoption. Returns (rows, err)."""
    url = f"{BASE_URL}/charts/rollingoption"
    payload = {
        "exchangeSegment": index_cfg["exchangeSegment"],
        "interval": str(interval),
        "securityId": str(index_cfg["securityId"]),
        "instrument": "OPTIDX",
        "expiryFlag": expiry_flag,
        "expiryCode": int(expiry_code),
        "strike": strike,
        "drvOptionType": option_type,
        "requiredData": ["open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"],
        "fromDate": from_date,
        "toDate": to_date,
    }
    headers = {"access-token": token, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except Exception as e:
        return None, f"network: {e}"
    try:
        data = resp.json()
    except Exception:
        return None, f"http {resp.status_code}: non-json response"
    if not resp.ok:
        return None, f"http {resp.status_code}: {data.get('remarks') or data}"

    key = "ce" if option_type == "CALL" else "pe"
    od = (data.get("data") or {}).get(key) or {}
    ts_list = od.get("timestamp") or []
    if not ts_list:
        return [], None  # empty but successful

    rows = []
    for i, ts in enumerate(ts_list):
        ist = datetime.utcfromtimestamp(ts) + timedelta(hours=5, minutes=30)
        def g(name, j=i):
            arr = od.get(name) or []
            return arr[j] if j < len(arr) else ""
        rows.append({
            "datetime": ist.strftime("%Y-%m-%d %H:%M:%S"),
            "open": g("open"),
            "high": g("high"),
            "low": g("low"),
            "close": g("close"),
            "volume": g("volume"),
            "oi": g("oi"),
            "iv": g("iv"),
            "strike_price": g("strike"),
            "spot": g("spot"),
        })
    return rows, None


def chunk_dates(from_date, to_date, days=29):
    """Split [from_date, to_date] into chunks no larger than `days`."""
    fd = datetime.strptime(from_date, "%Y-%m-%d").date()
    td = datetime.strptime(to_date, "%Y-%m-%d").date()
    out = []
    cur = fd
    while cur <= td:
        nxt = min(cur + timedelta(days=days), td)
        out.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt + timedelta(days=1)
    return out


def build_strike_list(strike_range):
    """e.g. 3 -> ['ATM-3','ATM-2','ATM-1','ATM','ATM+1','ATM+2','ATM+3']"""
    n = int(strike_range)
    n = max(0, min(10, n))
    strikes = []
    for i in range(-n, 0):
        strikes.append(f"ATM{i}")  # negative produces e.g. ATM-3
    strikes.append("ATM")
    for i in range(1, n + 1):
        strikes.append(f"ATM+{i}")
    return strikes


def safe_filename(s):
    return s.replace("+", "p").replace("-", "m")


def run_download(params):
    """Background worker."""
    try:
        token = params["token"]
        index = params["index"]
        interval = params["interval"]
        from_date = params["fromDate"]
        to_date = params["toDate"]
        expiry_flag = params["expiryFlag"]
        expiry_code = params["expiryCode"]
        strike_range = int(params["strikeRange"])
        opt_choice = params["optionType"]  # CALL / PUT / BOTH
        out_dir = params["outputFolder"]

        cfg = INDEX_CONFIGS[index]
        strikes = build_strike_list(strike_range)
        option_types = ["CALL", "PUT"] if opt_choice == "BOTH" else [opt_choice]
        chunks = chunk_dates(from_date, to_date, days=29)

        # Output folder: <out_dir>/<index>/<expiry_flag>_E<expiry_code>_<interval>m/<from>_<to>
        run_folder = os.path.join(
            out_dir,
            index,
            f"{expiry_flag}_E{expiry_code}_{interval}m",
            f"{from_date}_to_{to_date}",
        )
        os.makedirs(run_folder, exist_ok=True)
        log(f"Output folder: {run_folder}")

        total = len(strikes) * len(option_types) * len(chunks)
        with _lock:
            _progress["total"] = total
            _progress["done"] = 0
            _progress["pct"] = 0
            _progress["files"] = []
            _progress["errors"] = []

        date_buckets = defaultdict(list)  # "YYYY-MM-DD" -> list of rows

        for strike in strikes:
            for opt_type in option_types:
                if _progress["cancelled"]:
                    log("Cancelled by user.")
                    return
                fetched = 0
                for c_from, c_to in chunks:
                    if _progress["cancelled"]:
                        log("Cancelled by user.")
                        return
                    log(f"{strike} {opt_type}  {c_from} → {c_to}")
                    # toDate is non-inclusive per Dhan docs — send next day
                    api_to = (datetime.strptime(c_to, "%Y-%m-%d").date()
                              + timedelta(days=1)).strftime("%Y-%m-%d")
                    rows, err = fetch_rolling_option(
                        token, cfg, c_from, api_to, interval,
                        expiry_flag, expiry_code, strike, opt_type,
                    )
                    if err:
                        msg = f"ERR {strike} {opt_type} {c_from}: {err}"
                        log(msg)
                        with _lock:
                            _progress["errors"].append(msg)
                    elif rows:
                        for r in rows:
                            r["strike_label"] = strike
                            r["option_type"] = opt_type
                            date_buckets[r["datetime"][:10]].append(r)
                        fetched += len(rows)

                    with _lock:
                        _progress["done"] += 1
                        _progress["pct"] = int(_progress["done"] * 100 / max(total, 1))
                        _progress["msg"] = f"{strike} {opt_type}"

                if fetched == 0:
                    log(f"  (no rows for {strike} {opt_type})")

        if _progress["cancelled"]:
            log("Cancelled by user.")
            return

        # Write one CSV per date — all strikes & option types combined
        fieldnames = ["datetime", "strike_label", "option_type",
                      "open", "high", "low", "close", "volume",
                      "oi", "iv", "strike_price", "spot"]
        strike_order = {s: i for i, s in enumerate(strikes)}
        for date_str in sorted(date_buckets.keys()):
            rows = date_buckets[date_str]
            rows.sort(key=lambda r: (r["datetime"],
                                     strike_order.get(r["strike_label"], 999),
                                     r["option_type"]))
            fname = f"{index}_{date_str}_{interval}m.csv"
            fpath = os.path.join(run_folder, fname)
            with open(fpath, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
            log(f"  wrote {date_str}: {len(rows)} rows → {fname}")
            with _lock:
                _progress["files"].append(fpath)

        log(f"Done. {len(date_buckets)} date files written.")
    except Exception as e:
        log(f"FATAL: {e}")
        with _lock:
            _progress["errors"].append(str(e))
    finally:
        with _lock:
            _progress["running"] = False


# ════════════════════════════════════════════════════════════════════════════
#  FUTURES (NIFTY) — historical / intraday with auto contract rollover
# ════════════════════════════════════════════════════════════════════════════

NIFTY_FUT_UNDERLYING_ID = "26000"
FUT_VALID_INTERVALS = {"D", "1", "5", "15", "25", "60"}
DEFAULT_MASTER_GLOBS = [
    os.path.expanduser("~/Downloads/api-scrip-master-detailed*.csv"),
    os.path.expanduser("~/Downloads/**/api-scrip-master-detailed*.csv"),
]


def load_nifty_futures_contracts(master_path_override=""):
    """
    Scan one or more Dhan 'api-scrip-master-detailed*.csv' snapshots and
    return a sorted, de-duplicated list of NIFTY FUTIDX contracts.

    Each entry: {securityId, displayName, expiry (date), exchangeSegment, source}
    """
    paths = []
    if master_path_override.strip():
        p = os.path.expanduser(master_path_override.strip())
        if os.path.isfile(p):
            paths.append(p)
        elif os.path.isdir(p):
            paths.extend(glob.glob(os.path.join(p, "api-scrip-master-detailed*.csv")))
    if not paths:
        for pattern in DEFAULT_MASTER_GLOBS:
            paths.extend(glob.glob(pattern, recursive=True))
    # Dedup while preserving order
    paths = list(dict.fromkeys(os.path.realpath(p) for p in paths))

    seen = {}  # securityId -> contract dict (first hit wins; later only fills source)
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    if (r.get("INSTRUMENT") != "FUTIDX"
                            or r.get("UNDERLYING_SECURITY_ID") != NIFTY_FUT_UNDERLYING_ID):
                        continue
                    sid = (r.get("SECURITY_ID") or "").strip()
                    if not sid or sid in seen:
                        continue
                    exp_str = (r.get("SM_EXPIRY_DATE") or "").strip()
                    try:
                        exp = datetime.strptime(exp_str[:10], "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    seen[sid] = {
                        "securityId": sid,
                        "displayName": (r.get("DISPLAY_NAME") or "").strip(),
                        "expiry": exp,
                        "exchangeSegment": "NSE_FNO",
                        "source": os.path.basename(p),
                    }
        except Exception as e:
            log(f"  master read error {p}: {e}")

    contracts = sorted(seen.values(), key=lambda c: c["expiry"])
    return contracts, paths


def fetch_historical(token, security_id, exchange_segment, instrument,
                     from_date, to_date, interval):
    """
    Call /charts/historical (daily) or /charts/intraday (minute) for futures.
    interval = 'D' uses historical; numeric uses intraday.
    Returns (rows, err) where rows: list of dicts.
    """
    is_daily = str(interval).upper() == "D"
    url = f"{BASE_URL}/charts/historical" if is_daily else f"{BASE_URL}/charts/intraday"
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": exchange_segment,
        "instrument": instrument,
        "oi": True,
        "fromDate": from_date if is_daily else f"{from_date} 09:15:00",
        "toDate":   to_date   if is_daily else f"{to_date} 15:30:00",
    }
    if not is_daily:
        payload["interval"] = str(interval)

    headers = {"access-token": token, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except Exception as e:
        return None, f"network: {e}"
    try:
        data = resp.json()
    except Exception:
        return None, f"http {resp.status_code}: non-json"
    if not resp.ok:
        return None, f"http {resp.status_code}: {data.get('remarks') or data}"

    body = data.get("data") if isinstance(data.get("data"), dict) else data
    ts_list = body.get("timestamp") or []
    if not ts_list:
        return [], None

    rows = []
    for i, ts in enumerate(ts_list):
        ist = datetime.utcfromtimestamp(ts) + timedelta(hours=5, minutes=30)
        dt_str = ist.strftime("%Y-%m-%d") if is_daily else ist.strftime("%Y-%m-%d %H:%M:%S")
        def g(name, j=i, src=body):
            arr = src.get(name) or []
            return arr[j] if j < len(arr) else ""
        rows.append({
            "datetime": dt_str,
            "open":   g("open"),
            "high":   g("high"),
            "low":    g("low"),
            "close":  g("close"),
            "volume": g("volume"),
            "oi":     g("open_interest"),
        })
    return rows, None


def assign_contracts_per_date(from_date, to_date, contracts):
    """
    For each calendar date in [from_date, to_date], pick the contract with the
    earliest expiry >= that date. Returns:
      contract_to_dates: {securityId: (contract_dict, list_of_date_objs)}
      uncovered_dates:   list of dates with no available contract
    """
    fd = datetime.strptime(from_date, "%Y-%m-%d").date()
    td = datetime.strptime(to_date, "%Y-%m-%d").date()
    contract_to_dates = {}
    uncovered = []
    d = fd
    while d <= td:
        chosen = next((c for c in contracts if c["expiry"] >= d), None)
        if chosen is None:
            uncovered.append(d)
        else:
            entry = contract_to_dates.setdefault(chosen["securityId"], (chosen, []))
            entry[1].append(d)
        d += timedelta(days=1)
    return contract_to_dates, uncovered


def chunk_date_list(dates, max_days):
    """Split a sorted list of date objects into contiguous chunks of <= max_days."""
    if not dates:
        return []
    out = []
    cur = [dates[0]]
    for d in dates[1:]:
        if (d - cur[0]).days + 1 > max_days or (d - cur[-1]).days > 1:
            out.append(cur)
            cur = [d]
        else:
            cur.append(d)
    out.append(cur)
    return out


def run_futures_download(params):
    """Background worker for futures."""
    try:
        token = params["token"]
        from_date = params["fromDate"]
        to_date = params["toDate"]
        interval = params["interval"]
        out_dir = params["outputFolder"]
        master_override = params.get("masterPath", "")

        contracts, master_paths = load_nifty_futures_contracts(master_override)
        if not contracts:
            log("ERROR: no NIFTY futures contracts found in scrip master.")
            log("Place an 'api-scrip-master-detailed.csv' from Dhan in ~/Downloads/, "
                "or set the Master CSV path field.")
            with _lock:
                _progress["errors"].append("No futures contracts in master CSV")
            return

        log(f"Loaded {len(contracts)} unique NIFTY futures contracts from "
            f"{len(master_paths)} master file(s): {[os.path.basename(p) for p in master_paths]}")
        for c in contracts:
            log(f"  {c['displayName']} (secId {c['securityId']}, exp {c['expiry']}, from {c['source']})")

        ctod, uncovered = assign_contracts_per_date(from_date, to_date, contracts)
        if uncovered:
            months = sorted({d.strftime("%Y-%m") for d in uncovered})
            log(f"WARNING: no contract covers {len(uncovered)} date(s) in months: {months}")
            log("  → Refresh the Dhan scrip master CSV for those periods or expand the range.")

        if not ctod:
            log("Nothing to download — no contract covers any date in the range.")
            return

        # Each minute call is capped at 90 days by Dhan; daily we use 365 to be safe.
        max_days = 365 if str(interval).upper() == "D" else 90

        # Compute total chunks for progress
        plan = []  # list of (contract, chunk_dates)
        for sid, (contract, dates) in ctod.items():
            for chunk in chunk_date_list(sorted(dates), max_days):
                plan.append((contract, chunk))

        run_folder = os.path.join(
            out_dir, "NIFTY_FUT",
            f"{'D' if str(interval).upper() == 'D' else interval + 'm'}",
            f"{from_date}_to_{to_date}",
        )
        os.makedirs(run_folder, exist_ok=True)
        log(f"Output folder: {run_folder}")

        with _lock:
            _progress["total"] = len(plan)
            _progress["done"] = 0
            _progress["pct"] = 0
            _progress["files"] = []
            _progress["errors"] = []

        date_buckets = defaultdict(list)

        for contract, chunk in plan:
            if _progress["cancelled"]:
                log("Cancelled by user.")
                return
            c_from = chunk[0].strftime("%Y-%m-%d")
            c_to = chunk[-1].strftime("%Y-%m-%d")
            log(f"{contract['displayName']} (secId {contract['securityId']})  {c_from} → {c_to}")
            rows, err = fetch_historical(
                token, contract["securityId"], contract["exchangeSegment"],
                "FUTIDX", c_from, c_to, interval,
            )
            if err:
                msg = f"ERR {contract['displayName']} {c_from}: {err}"
                log(msg)
                with _lock:
                    _progress["errors"].append(msg)
            elif rows:
                allowed = {d.strftime("%Y-%m-%d") for d in chunk}
                kept = 0
                for r in rows:
                    dpart = r["datetime"][:10]
                    if dpart not in allowed:
                        continue  # rows beyond this contract's assigned window
                    r["contract_expiry"] = contract["expiry"].strftime("%Y-%m-%d")
                    r["contract_name"] = contract["displayName"]
                    r["security_id"] = contract["securityId"]
                    date_buckets[dpart].append(r)
                    kept += 1
                log(f"  fetched {len(rows)} rows, kept {kept}")

            with _lock:
                _progress["done"] += 1
                _progress["pct"] = int(_progress["done"] * 100 / max(len(plan), 1))
                _progress["msg"] = f"{contract['displayName']}"

        if _progress["cancelled"]:
            log("Cancelled by user.")
            return

        suffix = "daily" if str(interval).upper() == "D" else f"{interval}m"
        fieldnames = ["datetime", "contract_expiry", "contract_name", "security_id",
                      "open", "high", "low", "close", "volume", "oi"]
        for date_str in sorted(date_buckets.keys()):
            rows = sorted(date_buckets[date_str], key=lambda r: r["datetime"])
            fname = f"NIFTY_FUT_{date_str}_{suffix}.csv"
            fpath = os.path.join(run_folder, fname)
            with open(fpath, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
            log(f"  wrote {date_str}: {len(rows)} rows → {fname}")
            with _lock:
                _progress["files"].append(fpath)

        log(f"Done. {len(date_buckets)} date files written.")
    except Exception as e:
        log(f"FATAL: {e}")
        with _lock:
            _progress["errors"].append(str(e))
    finally:
        with _lock:
            _progress["running"] = False


# ──────────────────────────────────────── Routes ──────────────────────────────

@app.route("/")
def index_page():
    return render_template_string(TEMPLATE)


@app.route("/futures/contracts")
def futures_contracts():
    """Return current NIFTY futures contracts found in local master CSV(s)."""
    master_override = request.args.get("masterPath", "")
    contracts, paths = load_nifty_futures_contracts(master_override)
    return jsonify({
        "ok": True,
        "masters": [os.path.basename(p) for p in paths],
        "count": len(contracts),
        "contracts": [
            {"securityId": c["securityId"], "displayName": c["displayName"],
             "expiry": c["expiry"].strftime("%Y-%m-%d"), "source": c["source"]}
            for c in contracts
        ],
    })


@app.route("/start_futures", methods=["POST"])
def start_futures():
    data = request.get_json(force=True)

    required = ["token", "interval", "fromDate", "toDate", "outputFolder"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        return jsonify({"ok": False, "error": f"Missing fields: {', '.join(missing)}"}), 400

    if str(data["interval"]).upper() not in FUT_VALID_INTERVALS:
        return jsonify({"ok": False, "error": f"interval must be one of {sorted(FUT_VALID_INTERVALS)}"}), 400
    try:
        datetime.strptime(data["fromDate"], "%Y-%m-%d")
        datetime.strptime(data["toDate"], "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "Dates must be YYYY-MM-DD"}), 400
    if data["fromDate"] > data["toDate"]:
        return jsonify({"ok": False, "error": "fromDate must be <= toDate"}), 400

    out_dir = os.path.expanduser(data["outputFolder"])
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Cannot create output folder: {e}"}), 400
    data["outputFolder"] = out_dir

    with _lock:
        if _progress["running"]:
            return jsonify({"ok": False, "error": "A download is already running"}), 409
        _progress.update({
            "running": True, "cancelled": False,
            "done": 0, "total": 0, "pct": 0,
            "msg": "starting...", "logs": [], "files": [], "errors": [],
        })

    log(f"Starting NIFTY FUT  {data['interval']}  {data['fromDate']}→{data['toDate']}")
    if data.get("clientId"):
        log(f"clientId: {data['clientId']}")
    if data.get("masterPath"):
        log(f"masterPath: {data['masterPath']}")

    threading.Thread(target=run_futures_download, args=(data,), daemon=True).start()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════════════════════
#  EQUITY — bulk historical / intraday for stocks from an uploaded CSV
# ════════════════════════════════════════════════════════════════════════════

EQUITY_VALID_INTERVALS = {"D", "1", "5", "15", "25", "60"}


def parse_equity_csv(csv_text):
    """
    Parse a CSV containing SECURITY_ID (required) and UNDERLYING_SYMBOL (optional).
    Column matching is case-insensitive and tolerant of common aliases.
    Returns (rows, error). rows = [{securityId, symbol}, ...]
    """
    import io
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
    except Exception as e:
        return None, f"Cannot parse CSV: {e}"
    if not reader.fieldnames:
        return None, "CSV has no header row"

    # Resolve columns case-insensitively
    norm = {h.strip().upper(): h for h in reader.fieldnames if h}
    sid_aliases = ["SECURITY_ID", "SECURITYID", "SEC_ID", "SECID"]
    sym_aliases = ["UNDERLYING_SYMBOL", "SYMBOL", "TRADING_SYMBOL", "TICKER"]
    sid_col = next((norm[a] for a in sid_aliases if a in norm), None)
    sym_col = next((norm[a] for a in sym_aliases if a in norm), None)
    if not sid_col:
        return None, f"CSV must have a SECURITY_ID column. Found: {reader.fieldnames}"

    rows, seen = [], set()
    for r in reader:
        sid = str(r.get(sid_col, "")).strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        sym = str(r.get(sym_col, "")).strip() if sym_col else ""
        rows.append({"securityId": sid, "symbol": sym or sid})
    if not rows:
        return None, "CSV has no rows with a SECURITY_ID"
    return rows, None


def fetch_equity(token, security_id, from_date, to_date, interval):
    """
    Equity historical/intraday call. Wraps fetch_historical with NSE_EQ + EQUITY.
    """
    return fetch_historical(
        token=token,
        security_id=security_id,
        exchange_segment="NSE_EQ",
        instrument="EQUITY",
        from_date=from_date,
        to_date=to_date,
        interval=interval,
    )


def run_equity_download(params):
    """Background worker for equity bulk download."""
    try:
        token = params["token"]
        from_date = params["fromDate"]
        to_date = params["toDate"]
        interval = params["interval"]
        out_dir = params["outputFolder"]
        symbols = params["symbols"]  # list of {securityId, symbol}

        is_daily = str(interval).upper() == "D"
        max_days = 365 if is_daily else 90
        suffix = "daily" if is_daily else f"{interval}m"

        run_folder = os.path.join(
            out_dir, "equity", suffix, f"{from_date}_to_{to_date}",
        )
        os.makedirs(run_folder, exist_ok=True)
        log(f"Output folder: {run_folder}")
        log(f"{len(symbols)} symbol(s) to download")

        # Build chunk list per symbol
        chunks = chunk_dates(from_date, to_date, days=max_days)
        total = len(symbols) * len(chunks)
        with _lock:
            _progress["total"] = total
            _progress["done"] = 0
            _progress["pct"] = 0
            _progress["files"] = []
            _progress["errors"] = []

        fieldnames = ["datetime", "symbol", "security_id",
                      "open", "high", "low", "close", "volume", "oi"]

        for sym_row in symbols:
            if _progress["cancelled"]:
                log("Cancelled by user.")
                return
            sid = sym_row["securityId"]
            symbol = sym_row["symbol"]
            all_rows = []

            for c_from, c_to in chunks:
                if _progress["cancelled"]:
                    log("Cancelled by user.")
                    return
                log(f"{symbol} (secId {sid})  {c_from} → {c_to}")
                rows, err = fetch_equity(token, sid, c_from, c_to, interval)
                if err:
                    msg = f"ERR {symbol} {c_from}: {err}"
                    log(msg)
                    with _lock:
                        _progress["errors"].append(msg)
                elif rows:
                    for r in rows:
                        r["symbol"] = symbol
                        r["security_id"] = sid
                    all_rows.extend(rows)

                with _lock:
                    _progress["done"] += 1
                    _progress["pct"] = int(_progress["done"] * 100 / max(total, 1))
                    _progress["msg"] = f"{symbol}"

            if all_rows:
                safe_sym = "".join(ch if ch.isalnum() else "_" for ch in symbol).strip("_")
                fname = f"{safe_sym}_{sid}_{suffix}.csv"
                fpath = os.path.join(run_folder, fname)
                with open(fpath, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fieldnames)
                    w.writeheader()
                    w.writerows(all_rows)
                log(f"  wrote {len(all_rows)} rows → {fname}")
                with _lock:
                    _progress["files"].append(fpath)
            else:
                log(f"  (no rows for {symbol})")

        log(f"Done. {len([f for f in _progress['files']])} files written.")
    except Exception as e:
        log(f"FATAL: {e}")
        with _lock:
            _progress["errors"].append(str(e))
    finally:
        with _lock:
            _progress["running"] = False


@app.route("/equity/parse_csv", methods=["POST"])
def equity_parse_csv():
    """Preview-parse a CSV body and return the parsed (securityId, symbol) list."""
    data = request.get_json(force=True)
    csv_text = data.get("csvText", "")
    if not csv_text.strip():
        return jsonify({"ok": False, "error": "Empty CSV"}), 400
    rows, err = parse_equity_csv(csv_text)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True, "count": len(rows), "rows": rows[:200]})


@app.route("/start_equity", methods=["POST"])
def start_equity():
    data = request.get_json(force=True)

    required = ["token", "interval", "fromDate", "toDate", "outputFolder", "csvText"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        return jsonify({"ok": False, "error": f"Missing fields: {', '.join(missing)}"}), 400

    if str(data["interval"]).upper() not in EQUITY_VALID_INTERVALS:
        return jsonify({"ok": False, "error": f"interval must be one of {sorted(EQUITY_VALID_INTERVALS)}"}), 400
    try:
        datetime.strptime(data["fromDate"], "%Y-%m-%d")
        datetime.strptime(data["toDate"], "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "Dates must be YYYY-MM-DD"}), 400
    if data["fromDate"] > data["toDate"]:
        return jsonify({"ok": False, "error": "fromDate must be <= toDate"}), 400

    symbols, err = parse_equity_csv(data["csvText"])
    if err:
        return jsonify({"ok": False, "error": err}), 400

    out_dir = os.path.expanduser(data["outputFolder"])
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Cannot create output folder: {e}"}), 400

    job = {
        "token": data["token"],
        "fromDate": data["fromDate"],
        "toDate": data["toDate"],
        "interval": data["interval"],
        "outputFolder": out_dir,
        "symbols": symbols,
    }

    with _lock:
        if _progress["running"]:
            return jsonify({"ok": False, "error": "A download is already running"}), 409
        _progress.update({
            "running": True, "cancelled": False,
            "done": 0, "total": 0, "pct": 0,
            "msg": "starting...", "logs": [], "files": [], "errors": [],
        })

    log(f"Starting EQUITY  {data['interval']}  {data['fromDate']}→{data['toDate']}  "
        f"({len(symbols)} symbols)")
    if data.get("clientId"):
        log(f"clientId: {data['clientId']}")

    threading.Thread(target=run_equity_download, args=(job,), daemon=True).start()
    return jsonify({"ok": True, "symbolCount": len(symbols)})


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(force=True)

    required = ["token", "index", "interval", "fromDate", "toDate",
                "expiryFlag", "expiryCode", "strikeRange", "optionType", "outputFolder"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        return jsonify({"ok": False, "error": f"Missing fields: {', '.join(missing)}"}), 400

    if data["index"] not in INDEX_CONFIGS:
        return jsonify({"ok": False, "error": "index must be NIFTY or SENSEX"}), 400
    if str(data["interval"]) not in VALID_INTERVALS:
        return jsonify({"ok": False, "error": f"interval must be one of {sorted(VALID_INTERVALS)}"}), 400
    if data["expiryFlag"] not in {"WEEK", "MONTH"}:
        return jsonify({"ok": False, "error": "expiryFlag must be WEEK or MONTH"}), 400
    if data["optionType"] not in {"CALL", "PUT", "BOTH"}:
        return jsonify({"ok": False, "error": "optionType must be CALL/PUT/BOTH"}), 400
    try:
        datetime.strptime(data["fromDate"], "%Y-%m-%d")
        datetime.strptime(data["toDate"], "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "Dates must be YYYY-MM-DD"}), 400
    if data["fromDate"] > data["toDate"]:
        return jsonify({"ok": False, "error": "fromDate must be <= toDate"}), 400

    out_dir = os.path.expanduser(data["outputFolder"])
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Cannot create output folder: {e}"}), 400
    data["outputFolder"] = out_dir

    with _lock:
        if _progress["running"]:
            return jsonify({"ok": False, "error": "A download is already running"}), 409
        _progress.update({
            "running": True, "cancelled": False,
            "done": 0, "total": 0, "pct": 0,
            "msg": "starting...", "logs": [], "files": [], "errors": [],
        })

    log(f"Starting {data['index']} {data['expiryFlag']} E{data['expiryCode']} "
        f"{data['interval']}m  {data['fromDate']}→{data['toDate']}  "
        f"ATM±{data['strikeRange']}  {data['optionType']}")
    if data.get("clientId"):
        log(f"clientId: {data['clientId']}")

    threading.Thread(target=run_download, args=(data,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/progress")
def progress():
    with _lock:
        return jsonify(_progress)


@app.route("/cancel", methods=["POST"])
def cancel():
    with _lock:
        if _progress["running"]:
            _progress["cancelled"] = True
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Nothing running"}), 400


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index_page"))


@app.route("/download")
def download():
    with _lock:
        files = list(_progress["files"])
    if not files:
        return "No files available. Run a download first.", 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in files:
            if os.path.isfile(fpath):
                zf.write(fpath, os.path.basename(fpath))
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="zetapull_output.zip",
                     mimetype="application/zip")


# ───────────────────────────────── Template ───────────────────────────────────

TEMPLATE = r"""
<!doctype html>
<html><head>
<meta charset="utf-8"><title>ZetaPull</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
         background: #0f1419; color: #d7dee8; }
  header { padding: 12px 22px 0; background: #161d27; border-bottom: 1px solid #243044;
           display: flex; align-items: flex-start; justify-content: space-between; }
  header h1 { margin: 0 0 10px; font-size: 16px; font-weight: 600; }
  .header-left { flex: 1; }
  .logout-btn { margin-top: 4px; padding: 6px 14px; font-size: 12px; font-weight: 600;
                background: transparent; color: #94a0b6; border: 1px solid #2a3548;
                border-radius: 5px; cursor: pointer; text-decoration: none; white-space: nowrap;
                transition: background 0.15s, color 0.15s; }
  .logout-btn:hover { background: #b03030; color: #fff; border-color: #b03030; }
  header small { color: #7d8aa0; }
  .tabs { display: flex; gap: 2px; }
  .tab { padding: 8px 16px; font-size: 13px; cursor: pointer;
         background: #0f1520; color: #94a0b6; border: 1px solid #243044;
         border-bottom: none; border-radius: 6px 6px 0 0; margin-bottom: -1px; }
  .tab.active { background: #131923; color: #e6ecf5; border-bottom: 1px solid #131923; }
  .wrap { display: grid; grid-template-columns: 380px 1fr; gap: 0; height: calc(100vh - 90px); }
  .panel { padding: 18px 22px; overflow-y: auto; }
  .panel.left { background: #131923; border-right: 1px solid #243044; }
  .pane { display: none; }
  .pane.active { display: block; }
  label { display: block; font-size: 11.5px; color: #94a0b6;
          text-transform: uppercase; letter-spacing: 0.4px; margin-top: 12px; margin-bottom: 4px; }
  input, select { width: 100%; background: #0c1119; color: #e6ecf5;
                  border: 1px solid #2a3548; border-radius: 5px;
                  padding: 8px 10px; font-size: 13.5px; font-family: inherit; }
  input:focus, select:focus { outline: none; border-color: #4a7fd9; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  button { margin-top: 18px; padding: 10px 14px; font-size: 14px;
           border: none; border-radius: 5px; cursor: pointer; font-weight: 600; }
  .go  { background: #2962ff; color: #fff; width: 100%; }
  .go:hover  { background: #1e4dd1; }
  .stop { background: #b03030; color: #fff; width: 100%; }
  .stop:hover { background: #8a2424; }
  .ghost { background: transparent; color: #94a0b6; border: 1px solid #2a3548; }
  .ghost:hover { background: #1a212e; color: #e6ecf5; }
  .progress { height: 10px; background: #1a212e; border-radius: 5px; overflow: hidden; margin-top: 8px; }
  .bar { height: 100%; background: #2962ff; width: 0; transition: width 0.3s; }
  .status { display: flex; justify-content: space-between; font-size: 12px;
            color: #94a0b6; margin-top: 6px; }
  pre.logs { background: #0a0e15; border: 1px solid #1c2434; border-radius: 5px;
             padding: 10px; font-size: 12px; line-height: 1.45;
             height: calc(100vh - 280px); overflow-y: auto;
             font-family: ui-monospace, SF Mono, monospace; white-space: pre-wrap; color: #b6c4d6; }
  .err { color: #ff7676; }
  .ok { color: #6dd28a; }
  .files { font-size: 12px; margin-top: 8px; color: #6dd28a; max-height: 110px; overflow-y: auto; }
  .hint { font-size: 11px; color: #6b778c; margin-top: 3px; }
  .contracts { font-size: 11.5px; color: #b6c4d6; background: #0a0e15;
               border: 1px solid #1c2434; border-radius: 5px; padding: 8px;
               margin-top: 6px; max-height: 160px; overflow-y: auto;
               font-family: ui-monospace, SF Mono, monospace; }
  .contracts .none { color: #ff9b6b; }
</style>
</head><body>

<header>
  <div class="header-left">
    <h1>ZetaPull
      <small>· Expired Options · NIFTY Futures with auto-rollover</small>
    </h1>
    <div class="tabs">
      <div id="tab-opt" class="tab active" onclick="showTab('opt')">Expired Options</div>
      <div id="tab-fut" class="tab" onclick="showTab('fut')">Futures</div>
      <div id="tab-eq"  class="tab" onclick="showTab('eq')">Equity Data</div>
    </div>
  </div>
  <a href="/logout" class="logout-btn">Logout</a>
</header>

<div class="wrap">
  <div class="panel left">

    <!-- ───────────────── OPTIONS PANE ───────────────── -->
    <div id="pane-opt" class="pane active">
      <label>Index</label>
      <select id="index">
        <option value="NIFTY">NIFTY (secId 13)</option>
        <option value="SENSEX">SENSEX (secId 51)</option>
      </select>

      <label>Client ID</label>
      <input id="clientId" placeholder="Dhan client ID">

      <label>API Access Token</label>
      <input id="token" type="password" placeholder="access-token">

      <div class="row">
        <div><label>From Date</label><input id="fromDate" type="date"></div>
        <div><label>To Date</label><input id="toDate" type="date"></div>
      </div>

      <div class="row">
        <div>
          <label>Time Frame</label>
          <select id="interval">
            <option value="1">1 min</option>
            <option value="5" selected>5 min</option>
            <option value="15">15 min</option>
            <option value="25">25 min</option>
            <option value="60">60 min</option>
          </select>
        </div>
        <div>
          <label>Expiry Flag</label>
          <select id="expiryFlag">
            <option value="WEEK">WEEK</option>
            <option value="MONTH" selected>MONTH</option>
          </select>
        </div>
      </div>

      <div class="row">
        <div>
          <label>Expiry Code</label>
          <input id="expiryCode" type="number" min="1" max="6" value="1">
          <div class="hint">1 = nearest expiry</div>
        </div>
        <div>
          <label>Strike Range (ATM ± N)</label>
          <input id="strikeRange" type="number" min="0" max="10" value="3">
          <div class="hint">0 = ATM only, max 10</div>
        </div>
      </div>

      <label>Option Type</label>
      <select id="optionType">
        <option value="BOTH" selected>Both CALL &amp; PUT</option>
        <option value="CALL">CALL only</option>
        <option value="PUT">PUT only</option>
      </select>

      <label>Download Location</label>
      <input id="outputFolder" value="~/Downloads/dhan_expired_options">
      <div class="hint">One CSV per date, all strikes &amp; CALL/PUT combined</div>

      <button class="go" id="goBtn" onclick="startOptions()">▶ Start Download</button>
    </div>

    <!-- ───────────────── FUTURES PANE ───────────────── -->
    <div id="pane-fut" class="pane">
      <label>Underlying</label>
      <select id="fut_underlying" disabled>
        <option value="NIFTY">NIFTY (UNDERLYING_SECURITY_ID 26000)</option>
      </select>

      <label>Client ID</label>
      <input id="fut_clientId" placeholder="Dhan client ID">

      <label>API Access Token</label>
      <input id="fut_token" type="password" placeholder="access-token">

      <div class="row">
        <div><label>From Date</label><input id="fut_fromDate" type="date"></div>
        <div><label>To Date</label><input id="fut_toDate" type="date"></div>
      </div>

      <label>Time Frame</label>
      <select id="fut_interval">
        <option value="D">Daily</option>
        <option value="1">1 min</option>
        <option value="5" selected>5 min</option>
        <option value="15">15 min</option>
        <option value="25">25 min</option>
        <option value="60">60 min</option>
      </select>
      <div class="hint">Daily → /charts/historical. Minute → /charts/intraday (90-day chunks).</div>

      <label>Master CSV Path
        <span style="float:right; font-weight: normal;">
          <a href="#" onclick="loadContracts(); return false;"
             style="color:#4a7fd9; font-size: 11px;">↻ Refresh</a>
        </span>
      </label>
      <input id="fut_masterPath" placeholder="leave blank → scans ~/Downloads/api-scrip-master-detailed*.csv">
      <div class="hint">Dhan's scrip master is a current snapshot. Drop multiple
        dated snapshots in <code>~/Downloads/</code> to cover historical contracts.</div>

      <div id="fut_contracts" class="contracts">Click ↻ Refresh to load NIFTY futures contracts…</div>

      <label>Download Location</label>
      <input id="fut_outputFolder" value="~/Downloads/dhan_nifty_futures">
      <div class="hint">One CSV per date, with auto contract rollover at each SM_EXPIRY_DATE</div>

      <button class="go" id="fut_goBtn" onclick="startFutures()">▶ Start Download</button>
    </div>

    <!-- ───────────────── EQUITY PANE ───────────────── -->
    <div id="pane-eq" class="pane">
      <label>Client ID</label>
      <input id="eq_clientId" placeholder="Dhan client ID">

      <label>API Access Token</label>
      <input id="eq_token" type="password" placeholder="access-token">

      <label>Symbols CSV (must contain SECURITY_ID column)</label>
      <input id="eq_csvFile" type="file" accept=".csv,text/csv"
             onchange="loadEquityCsv()">
      <div class="hint">Optional column: UNDERLYING_SYMBOL (used to name output files).</div>
      <div id="eq_csvPreview" class="contracts">Pick a CSV to preview symbols…</div>

      <div class="row">
        <div><label>From Date</label><input id="eq_fromDate" type="date"></div>
        <div><label>To Date</label><input id="eq_toDate" type="date"></div>
      </div>

      <label>Time Frame</label>
      <select id="eq_interval">
        <option value="D">Daily (EOD)</option>
        <option value="1">1 min</option>
        <option value="5" selected>5 min</option>
        <option value="15">15 min</option>
        <option value="25">25 min</option>
        <option value="60">60 min</option>
      </select>
      <div class="hint">Daily → /charts/historical. Minute → /charts/intraday (90-day chunks).</div>

      <label>Download Location</label>
      <input id="eq_outputFolder" value="~/Downloads/dhan_equity">
      <div class="hint">One CSV per symbol: <code>SYMBOL_SECID_5m.csv</code></div>

      <button class="go" id="eq_goBtn" onclick="startEquity()">▶ Start Download</button>
    </div>

    <button class="stop" id="stopBtn" onclick="cancelRun()" style="display:none">■ Cancel</button>
  </div>

  <div class="panel">
    <div class="progress"><div id="bar" class="bar"></div></div>
    <div class="status">
      <span id="pct">0%</span>
      <span id="counts">0 / 0</span>
    </div>
    <div id="msg" class="status"><span id="msgTxt">idle</span></div>
    <div id="files" class="files"></div>
    <div id="downloadWrap" style="display:none; margin-top:14px;">
      <a id="downloadBtn" href="/download"
         style="display:inline-block; background:#1e7e34; color:#fff; font-size:14px;
                font-weight:600; padding:10px 20px; border-radius:6px;
                text-decoration:none; border:none; cursor:pointer;">
        ⬇️ Download All CSV Files
      </a>
    </div>
    <pre id="logs" class="logs">Logs will appear here…</pre>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let pollTimer = null;

function showTab(name) {
  for (const t of ['opt','fut','eq']) {
    $('tab-' + t).classList.toggle('active', t === name);
    $('pane-' + t).classList.toggle('active', t === name);
  }
  $('goBtn').style.display     = (name === 'opt') ? 'block' : 'none';
  $('fut_goBtn').style.display = (name === 'fut') ? 'block' : 'none';
  $('eq_goBtn').style.display  = (name === 'eq')  ? 'block' : 'none';
  if (name === 'fut' && !window._contractsLoaded) loadContracts();
}

let _equityCsvText = '';
function loadEquityCsv() {
  const f = $('eq_csvFile').files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = e => {
    _equityCsvText = e.target.result;
    fetch('/equity/parse_csv', {method:'POST',
                                headers:{'Content-Type':'application/json'},
                                body: JSON.stringify({csvText: _equityCsvText})})
      .then(r => r.json())
      .then(j => {
        if (!j.ok) {
          $('eq_csvPreview').innerHTML = '<span class="none">' + j.error + '</span>';
          _equityCsvText = '';
          return;
        }
        const lines = j.rows.map(r =>
          '• ' + r.securityId.padEnd(8) + '  ' + r.symbol
        );
        const head = '<b>' + j.count + ' symbol(s)' +
                     (j.count > j.rows.length ? ' (showing first ' + j.rows.length + ')' : '') +
                     ':</b><br>';
        $('eq_csvPreview').innerHTML = head + lines.join('<br>');
      });
  };
  reader.readAsText(f);
}

function startEquity() {
  if (!_equityCsvText) { alert('Pick a symbols CSV first'); return; }
  const body = {
    clientId: $('eq_clientId').value.trim(),
    token: $('eq_token').value.trim(),
    fromDate: $('eq_fromDate').value,
    toDate: $('eq_toDate').value,
    interval: $('eq_interval').value,
    outputFolder: $('eq_outputFolder').value.trim(),
    csvText: _equityCsvText,
  };
  if (!body.token) { alert('Access token is required'); return; }
  if (!body.fromDate || !body.toDate) { alert('From/To dates required'); return; }
  if (!body.outputFolder) { alert('Download location required'); return; }
  postStart('/start_equity', body, 'eq_goBtn');
}

function startOptions() {
  const body = {
    index: $('index').value,
    clientId: $('clientId').value.trim(),
    token: $('token').value.trim(),
    fromDate: $('fromDate').value,
    toDate: $('toDate').value,
    interval: $('interval').value,
    expiryFlag: $('expiryFlag').value,
    expiryCode: $('expiryCode').value,
    strikeRange: $('strikeRange').value,
    optionType: $('optionType').value,
    outputFolder: $('outputFolder').value.trim(),
  };
  if (!body.token) { alert('Access token is required'); return; }
  if (!body.fromDate || !body.toDate) { alert('From/To dates required'); return; }
  if (!body.outputFolder) { alert('Download location required'); return; }
  postStart('/start', body, 'goBtn');
}

function startFutures() {
  const body = {
    clientId: $('fut_clientId').value.trim(),
    token: $('fut_token').value.trim(),
    fromDate: $('fut_fromDate').value,
    toDate: $('fut_toDate').value,
    interval: $('fut_interval').value,
    outputFolder: $('fut_outputFolder').value.trim(),
    masterPath: $('fut_masterPath').value.trim(),
  };
  if (!body.token) { alert('Access token is required'); return; }
  if (!body.fromDate || !body.toDate) { alert('From/To dates required'); return; }
  if (!body.outputFolder) { alert('Download location required'); return; }
  postStart('/start_futures', body, 'fut_goBtn');
}

function postStart(url, body, goBtnId) {
  fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify(body)})
    .then(r => r.json())
    .then(j => {
      if (!j.ok) { alert('Error: ' + j.error); return; }
      $(goBtnId).style.display = 'none';
      $('stopBtn').style.display = 'block';
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(poll, 700);
    });
}

function cancelRun() {
  fetch('/cancel', {method:'POST'}).then(r => r.json());
}

function loadContracts() {
  const mp = $('fut_masterPath').value.trim();
  const url = '/futures/contracts' + (mp ? '?masterPath=' + encodeURIComponent(mp) : '');
  $('fut_contracts').textContent = 'loading…';
  fetch(url).then(r => r.json()).then(j => {
    window._contractsLoaded = true;
    if (!j.ok || !j.contracts.length) {
      $('fut_contracts').innerHTML =
        '<span class="none">No NIFTY futures contracts found.<br>' +
        'Place api-scrip-master-detailed.csv in ~/Downloads/ or set the Master CSV Path.</span>';
      return;
    }
    const lines = j.contracts.map(c =>
      '• ' + c.expiry + '  ' + c.displayName.padEnd(20) +
      '  secId ' + c.securityId + '  (' + c.source + ')'
    );
    $('fut_contracts').innerHTML =
      '<b>' + j.count + ' contract(s) from ' + j.masters.length + ' master file(s):</b><br>' +
      lines.join('<br>');
  }).catch(e => {
    $('fut_contracts').innerHTML = '<span class="none">Error: ' + e + '</span>';
  });
}

function poll() {
  fetch('/progress').then(r => r.json()).then(p => {
    $('bar').style.width = p.pct + '%';
    $('pct').textContent = p.pct + '%';
    $('counts').textContent = p.done + ' / ' + p.total;
    $('msgTxt').textContent = p.msg || (p.running ? 'working...' : 'idle');
    $('logs').textContent = (p.logs || []).join('\n') || 'No logs yet';
    $('logs').scrollTop = $('logs').scrollHeight;

    if (p.files && p.files.length) {
      $('files').innerHTML = '<b>Files written:</b><br>' + p.files.map(f => '• ' + f).join('<br>');
    }
    if (!p.running) {
      $('goBtn').style.display     = $('tab-opt').classList.contains('active') ? 'block' : 'none';
      $('fut_goBtn').style.display = $('tab-fut').classList.contains('active') ? 'block' : 'none';
      $('eq_goBtn').style.display  = $('tab-eq').classList.contains('active')  ? 'block' : 'none';
      $('stopBtn').style.display = 'none';
      clearInterval(pollTimer);
      pollTimer = null;
      if (p.files && p.files.length) {
        $('downloadWrap').style.display = 'block';
      }
    } else {
      $('downloadWrap').style.display = 'none';
    }
  });
}

// Sensible date defaults
(function() {
  const iso = x => x.toISOString().slice(0,10);
  const today = new Date();
  const lastMonth = new Date(); lastMonth.setDate(today.getDate() - 30);
  $('toDate').value = iso(today);
  $('fromDate').value = iso(lastMonth);
  $('fut_toDate').value = iso(today);
  $('fut_fromDate').value = iso(lastMonth);
  $('eq_toDate').value = iso(today);
  $('eq_fromDate').value = iso(lastMonth);
})();
poll();
</script>
</body></html>
"""


if __name__ == "__main__":
    port = 5083
    print(f"\n  Expired Options Downloader → http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
