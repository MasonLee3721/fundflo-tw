#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""補新股（1815/5340/5475）缺漏的 TPEx 融資餘額歷史（只 INSERT margin 表）。"""
import json
import sqlite3
import subprocess
import sys
import time

DB = '/home/hatch/workspace/fundflo/fundflo.db'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
CODES = ['1815', '5340', '5475']


def fetch(url):
    for a in range(1, 6):
        try:
            p = subprocess.run(['curl', '-sL', '--max-time', '120', '-A', UA, url],
                               capture_output=True, timeout=150)
            if p.returncode == 0 and p.stdout:
                return json.loads(p.stdout.decode('utf-8'))
            raise RuntimeError(f'rc={p.returncode}')
        except Exception as e:
            if a == 5:
                print(f'  !! failed: {e}', file=sys.stderr, flush=True)
                return None
            time.sleep(8 * a)


def num(s):
    s = str(s or '').replace(',', '').strip()
    if s in ('', '--', '---'):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def roc(ymd):
    return f"{int(ymd[:4]) - 1911}/{ymd[4:6]}/{ymd[6:]}"


con = sqlite3.connect(DB)
dates = [r[0] for r in con.execute('SELECT DISTINCT date FROM inst_flow ORDER BY date')]
missing = [d for d in dates
           if con.execute("SELECT 1 FROM margin WHERE date=? AND stock='1815'", (d,)).fetchone() is None]
print(f'missing dates: {len(missing)}', flush=True)
failed = []
for ymd in missing:
    url = (f'https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/'
           f'margin_bal_result.php?l=zh-tw&d={roc(ymd)}&o=daily')
    data = fetch(url)
    tbl = {}
    if data:
        for t in data.get('tables', []):
            if '融資融券餘額' in (t.get('title') or ''):
                for r in t.get('data', []):
                    tbl[str(r[0]).strip()] = (num(r[2]), num(r[6]))
                break
    if len(tbl) < 500:
        print(f'  {ymd}: SKIP (rows={len(tbl)})', flush=True)
        failed.append(ymd)
        continue
    n = 0
    for code in CODES:
        if code in tbl:
            prev, today = tbl[code]
            con.execute('INSERT OR REPLACE INTO margin VALUES (?,?,?,?,?)',
                        (ymd, code, prev, today, today - prev))
            n += 1
    con.commit()
    print(f'  {ymd}: inserted {n}/3', flush=True)
    time.sleep(1)
con.close()
print(f'margin repair done. failed: {failed}', flush=True)
