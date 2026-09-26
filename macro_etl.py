#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Macro ETL for the market overview report.
Sources (all public, verified reachable from this VM):
  - US 10Y / 2Y Treasury yields: FRED fredgraph.csv?id=DGS10 / DGS2 (no key)
  - 10Y term premium (ACM): NY Fed ACMTermPremium.xls, sheet 'ACM Daily', col ACMTP10
  - TWD/USD: Yahoo Finance USDTWD=X (FRED DEXTAIW and BOT are bot-blocked from here)

All series are T+1 by nature (US close / next-day publish). The overview report
labels every value with its actual data date; nothing is presented as "today".

Table: macro_daily(date TEXT PRIMARY KEY, dgs10 REAL, dgs2 REAL, acm_tp10 REAL, twd REAL)
Dates stored as YYYYMMDD.

Usage:
  python3 macro_etl.py --latest     # fetch full history from each source, upsert
  python3 macro_etl.py --backfill   # same (sources always return full history)
"""
import argparse, csv, io, json, sqlite3, subprocess, sys, time
from datetime import datetime, timezone

BASE = '/home/hatch/workspace/fundflo'
DB = f'{BASE}/fundflo.db'
UA_STR = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
TIMEOUT = 60


def fetch(url, timeout=TIMEOUT):
    """curl-based fetch (FRED systematically times out urllib from this VM)."""
    for a in (1, 2, 3):
        try:
            p = subprocess.run(
                ['curl', '-sL', '--max-time', str(timeout), '-A', UA_STR, url],
                capture_output=True, timeout=timeout + 15)
            if p.returncode == 0 and p.stdout:
                return p.stdout
            raise RuntimeError(f'curl rc={p.returncode} bytes={len(p.stdout)}')
        except Exception as e:
            if a == 3:
                print(f'  !! fetch failed: {url} -> {e}', file=sys.stderr)
                return None
            time.sleep(3 * a)
    return None


def fred_series(sid, timeout=25, tries=2):
    """FRED fredgraph.csv (no key). Flaky from this VM -> short timeout, few tries."""
    for a in range(1, tries + 1):
        try:
            p = subprocess.run(
                ['curl', '-sL', '--max-time', str(timeout), '-A', UA_STR,
                 f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}'],
                capture_output=True, timeout=timeout + 15)
            if p.returncode == 0 and p.stdout and b'DATE' in p.stdout[:100]:
                out = {}
                for row in csv.DictReader(io.StringIO(p.stdout.decode('utf-8', errors='replace'))):
                    d = (row.get('DATE') or '').strip()
                    v = (row.get('VALUE') or '').strip()
                    if d and v not in ('', '.'):
                        try:
                            out[d.replace('-', '')] = float(v)
                        except ValueError:
                            pass
                return out
            raise RuntimeError(f'bad response rc={p.returncode} bytes={len(p.stdout)}')
        except Exception as e:
            print(f'  !! FRED {sid} attempt {a}: {e}', file=sys.stderr)
            time.sleep(5)
    return {}


def yahoo_series(symbol, rng='2y'):
    """Yahoo Finance daily closes (reliable from this VM). symbol e.g. ^TNX, USDTWD=X."""
    import urllib.parse
    raw = fetch('https://query1.finance.yahoo.com/v8/finance/chart/'
                + urllib.parse.quote(symbol, safe='') + f'?interval=1d&range={rng}')
    if not raw:
        return {}
    try:
        d = json.loads(raw)['chart']['result'][0]
    except Exception as e:
        print(f'  !! yahoo {symbol}: {e}', file=sys.stderr)
        return {}
    ts = d['timestamp']
    cl = d['indicators']['quote'][0]['close']
    out = {}
    for t, c in zip(ts, cl):
        if c:
            out[datetime.fromtimestamp(t, timezone.utc).strftime('%Y%m%d')] = round(float(c), 4)
    return out


def acm_tp10():
    raw = fetch('https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls',
                timeout=90)
    if not raw:
        return {}
    try:
        import xlrd
    except ImportError:
        print('  !! xlrd not installed, skipping ACM', file=sys.stderr)
        return {}
    wb = xlrd.open_workbook(file_contents=raw)
    ws = wb.sheet_by_name('ACM Daily')
    hdr = [str(ws.cell(0, c).value) for c in range(ws.ncols)]
    ci = hdr.index('ACMTP10')
    out = {}
    for r in range(1, ws.nrows):
        dv, v = ws.cell(r, 0).value, ws.cell(r, ci).value
        if not isinstance(v, float):
            continue
        # DATE col may be an Excel float date or a 'dd-Mon-yyyy' string
        if isinstance(dv, float):
            d = datetime(*xlrd.xldate_as_tuple(dv, wb.datemode)).strftime('%Y%m%d')
        elif isinstance(dv, str) and dv.strip():
            try:
                d = datetime.strptime(dv.strip(), '%d-%b-%Y').strftime('%Y%m%d')
            except ValueError:
                continue
        else:
            continue
        out[d] = round(v, 4)
    return out


def yahoo_twd():
    return yahoo_series('USDTWD=X', '2y')


def run():
    con = sqlite3.connect(DB)
    con.execute('''CREATE TABLE IF NOT EXISTS macro_daily(
      date TEXT PRIMARY KEY, dgs10 REAL, dgs2 REAL, acm_tp10 REAL, twd REAL)''')
    print('fetching DGS10 (FRED)...', flush=True); d10 = fred_series('DGS10')
    print('fetching DGS2 (FRED)...', flush=True); d2 = fred_series('DGS2')
    if not d10:
        print('FRED 10Y failed -> Yahoo ^TNX fallback', flush=True)
        d10 = yahoo_series('^TNX', '2y')
        print(f'  ^TNX fallback rows: {len(d10)}', flush=True)
    print('fetching ACM TP10 (NY Fed)...', flush=True); tp = acm_tp10()
    print('fetching USDTWD (Yahoo)...', flush=True); fx = yahoo_twd()
    print(f'  got: DGS10={len(d10)} DGS2={len(d2)} ACM={len(tp)} TWD={len(fx)}', flush=True)
    all_dates = set(d10) | set(d2) | set(tp) | set(fx)
    n = 0
    for dt in sorted(all_dates):
        cur = con.execute('SELECT dgs10,dgs2,acm_tp10,twd FROM macro_daily WHERE date=?', (dt,)).fetchone()
        old = cur if cur else (None, None, None, None)
        vals = (d10.get(dt, old[0]), d2.get(dt, old[1]), tp.get(dt, old[2]), fx.get(dt, old[3]))
        con.execute('INSERT OR REPLACE INTO macro_daily VALUES (?,?,?,?,?)', (dt,) + vals)
        n += 1
    con.commit()
    r = con.execute('SELECT MIN(date),MAX(date),COUNT(*) FROM macro_daily').fetchone()
    print(f'  upserted {n} dates; macro_daily range {r[0]}..{r[1]} ({r[2]} rows)')
    con.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--latest', action='store_true')
    ap.add_argument('--backfill', action='store_true')
    a = ap.parse_args()
    if a.latest or a.backfill or True:
        run()
