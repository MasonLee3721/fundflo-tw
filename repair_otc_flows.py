#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OTC flow repair （外科式，只動上櫃股的流量與金額欄）。

背景：TPEx 3insti 端點不穩，etl_date 重跑時若抓取失敗，上櫃股該日流量會被
INSERT OR REPLACE 覆寫為 0（2026-10-21 籃子拆分回補時踩到）。本腳本對
inst_flow 全部日期重抓 TPEx 三大法人表（curl 多次重試），只 UPDATE 上櫃
籃子股的 f/t/d buy/sell/net，並以 close（DB 既有值優先，缺值用 SectorDetect
price_daily 真實歷史收盤）重算 f_amt/t_amt/d_amt。不動 close/turnover/spread、
上市股、market_daily、margin。
"""
import json
import sqlite3
import subprocess
import sys
import time

BASE = '/home/hatch/workspace/fundflo'
DB = f'{BASE}/fundflo.db'
SD_DB = '/home/hatch/workspace/sector-detect/sector_detect.db'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'


def fetch(url):
    for a in range(1, 6):
        try:
            p = subprocess.run(['curl', '-sL', '--max-time', '120', '-A', UA, url],
                               capture_output=True, timeout=150)
            if p.returncode == 0 and p.stdout:
                return json.loads(p.stdout.decode('utf-8'))
            raise RuntimeError(f'rc={p.returncode} bytes={len(p.stdout)}')
        except Exception as e:
            if a == 5:
                print(f'  !! fetch failed after retries: {e}', file=sys.stderr, flush=True)
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


def main():
    basket = json.load(open(f'{BASE}/basket.json', encoding='utf-8'))
    codes = sorted({st['code'] for s in basket['sectors'] for st in s['stocks']})
    sd = sqlite3.connect(SD_DB)
    market = dict(sd.execute(
        'SELECT stock, market FROM universe WHERE stock IN (%s)' % ','.join('?' * len(codes)),
        codes))
    otc = [c for c in codes if market.get(c) == 'TPEx']
    sd_close = {}
    for d, st, cl in sd.execute(
            'SELECT date, stock, close FROM price_daily WHERE stock IN (%s)' % ','.join('?' * len(otc)),
            otc):
        sd_close[(d, st)] = cl
    sd.close()
    print(f'OTC basket stocks: {len(otc)}; SD closes loaded: {len(sd_close)}', flush=True)

    con = sqlite3.connect(DB)
    dates = [r[0] for r in con.execute('SELECT DISTINCT date FROM inst_flow ORDER BY date')]
    print(f'dates: {len(dates)} ({dates[0]}..{dates[-1]})', flush=True)
    failed = []
    for ymd in dates:
        url = (f'https://www.tpex.org.tw/web/stock/3insti/daily_trade/'
               f'3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={roc(ymd)}')
        tpex3 = fetch(url)
        flows = {}
        if tpex3:
            for t in tpex3.get('tables', []):
                for r in t.get('data', []):
                    code = str(r[0]).strip()
                    flows[code] = (num(r[8]), num(r[9]), num(r[10]),
                                   num(r[11]), num(r[12]), num(r[13]),
                                   num(r[20]), num(r[21]), num(r[22]))
                break
        if len(flows) < 500:
            print(f'  {ymd}: SKIP (table rows={len(flows)}, treated as fetch failure)', flush=True)
            failed.append(ymd)
            continue
        upd = 0
        for code in otc:
            row = con.execute('SELECT close FROM inst_flow WHERE date=? AND stock=?',
                              (ymd, code)).fetchone()
            if row is None:
                continue
            fb, fs, fn, tb, ts, tn, db_, ds, dn = flows.get(code, (0.0,) * 9)
            close = row[0] if row[0] is not None else sd_close.get((ymd, code))
            amt = (lambda net: net * close / 1e8) if close else (lambda net: None)
            con.execute(
                '''UPDATE inst_flow SET f_buy=?, f_sell=?, f_net=?, t_buy=?, t_sell=?, t_net=?,
                   d_buy=?, d_sell=?, d_net=?, f_amt=?, t_amt=?, d_amt=? WHERE date=? AND stock=?''',
                (fb, fs, fn, tb, ts, tn, db_, ds, dn, amt(fn), amt(tn), amt(dn), ymd, code))
            upd += 1
        con.commit()
        nz = sum(1 for c in otc if flows.get(c, (0, 0, 0))[2] != 0)
        print(f'  {ymd}: updated {upd} OTC rows, {nz} with foreign net != 0', flush=True)
        time.sleep(1)
    con.close()
    print(f'repair done. failed dates: {failed}', flush=True)


if __name__ == '__main__':
    main()
