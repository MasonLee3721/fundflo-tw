#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-time repair (documented approximation).

The TPEx quote endpoint ignores the date param and always returns the latest
day, so etl.py deliberately leaves OTC `close` NULL on historical dates (a
wrong close is worse than none). That also leaves f_amt/t_amt/d_amt NULL.

This script estimates those amounts with each stock's LATEST close
(TWSE MI_INDEX + TPEx quote tables, fetched once). `close` itself stays NULL,
so 5日漲幅 keeps showing — : no fabricated price history.

Usage: python3 repair_otc.py   (run once after a historical backfill)
"""
import json, sqlite3, subprocess, sys, time
from urllib.request import Request, urlopen

BASE = '/home/hatch/workspace/fundflo'
DB = f'{BASE}/fundflo.db'
UA_STR = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'


def fetch(url):
    for a in (1, 2, 3, 4, 5):
        try:
            p = subprocess.run(['curl', '-sL', '--max-time', '120', '-A', UA_STR, url],
                               capture_output=True, timeout=150)
            if p.returncode == 0 and p.stdout:
                return json.loads(p.stdout.decode('utf-8'))
            raise RuntimeError(f'rc={p.returncode} bytes={len(p.stdout)}')
        except Exception as e:
            if a == 5:
                raise
            print(f'  retry {a}: {e}', file=sys.stderr)
            time.sleep(10)


def num(s):
    s = str(s or '').replace(',', '').strip()
    try:
        return float(s) if s not in ('', '--', '---') else 0.0
    except ValueError:
        return 0.0


def latest_closes():
    closes = {}
    mi = fetch('https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=ALLBUT0999')
    if mi:
        for t in mi.get('tables', []):
            if '每日收盤行情' in (t.get('title') or ''):
                for r in t['data']:
                    c = num(r[8])
                    if c:
                        closes[r[0].strip()] = c
    q = fetch('https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&s=0,asc,0')
    if q:
        for t in q.get('tables', []):
            if '上櫃股票行情' in (t.get('title') or ''):
                for r in t.get('data', []):
                    code = r[0].strip()
                    if code not in closes:
                        c = num(r[2])
                        if c:
                            closes[code] = c
    return closes


def main():
    closes = latest_closes()
    print(f'latest closes fetched: {len(closes)}')
    con = sqlite3.connect(DB)
    rows = con.execute('''SELECT date, stock, f_net, t_net, d_net FROM inst_flow
                          WHERE close IS NULL AND f_amt IS NULL''').fetchall()
    n, skip = 0, 0
    for d, code, f_net, t_net, d_net in rows:
        lc = closes.get(code)
        if not lc:
            skip += 1
            continue
        con.execute('''UPDATE inst_flow SET f_amt=?, t_amt=?, d_amt=?
                       WHERE date=? AND stock=?''',
                    ((f_net or 0) * lc / 1e8, (t_net or 0) * lc / 1e8,
                     (d_net or 0) * lc / 1e8, d, code))
        n += 1
    con.commit()
    print(f'estimated amounts for {n} rows using latest close (close left NULL); {skip} skipped (no quote)')
    con.close()


if __name__ == '__main__':
    main()
