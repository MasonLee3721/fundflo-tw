#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FundFlo-replacement daily ETL.
Pulls TWSE public data per trading day for the basket stocks and stores it in SQLite.

Sources (all verified reachable from this VM):
  - TWT38U  外資及陸資買賣超彙總表 (per stock; use 合計 group = cols 9/10/11)
  - TWT44U  投信買賣超彙總表 (per stock; cols 3/4/5)
  - TWT43U  自營商買賣超彙總表 (per stock; use 合計 group = cols 8/9/10)
  - MI_INDEX (type=ALLBUT0999) 每日收盤行情 table (per stock close/turnover/spread),
    大盤統計資訊 (總計(1~15) turnover), 漲跌證券數合計 (股票 上漲/下跌家數)
  - BFI82U  三大法人買賣金額統計表 (whole-market net amounts)
  - MI_MARGN (selectType=STOCK) 融資融券彙總 (per stock, one call for ALL stocks;
    融資 前日餘額/今日餘額 in 張)

Holiday handling: any endpoint returning stat != OK or empty data for a date means
no trading that day -> the date is skipped (logged), never crashes.

Usage:
  python3 etl.py --date 20260924        # ETL a single day
  python3 etl.py --latest               # ETL the latest available trading day
  python3 etl.py --backfill 30          # ETL the last 30 trading days
"""
import argparse, json, re, sqlite3, sys, time
from datetime import date, timedelta
from urllib.request import Request, urlopen

BASE = '/home/hatch/workspace/fundflo'
DB = f'{BASE}/fundflo.db'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
TIMEOUT = 20


def fetch(url):
    """GET JSON with retries. Returns parsed object or None."""
    for attempt in (1, 2, 3):
        try:
            req = Request(url, headers=UA)
            with urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            if attempt == 3:
                print(f'  !! fetch failed: {url} -> {e}', file=sys.stderr)
                return None
            time.sleep(3 * attempt)
    return None


def num(s):
    if s is None:
        return 0.0
    s = str(s).replace(',', '').strip()
    if s in ('', '--', '---'):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def init_db():
    con = sqlite3.connect(DB)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS inst_flow(
      date TEXT, stock TEXT,
      f_buy REAL, f_sell REAL, f_net REAL,
      t_buy REAL, t_sell REAL, t_net REAL,
      d_buy REAL, d_sell REAL, d_net REAL,
      close REAL, turnover REAL, spread REAL,
      f_amt REAL, t_amt REAL, d_amt REAL,
      PRIMARY KEY(date, stock));
    CREATE TABLE IF NOT EXISTS margin(
      date TEXT, stock TEXT,
      fin_prev REAL, fin_today REAL, fin_delta REAL,
      PRIMARY KEY(date, stock));
    CREATE TABLE IF NOT EXISTS market_daily(
      date TEXT PRIMARY KEY,
      f_net REAL, t_net REAL, d_net REAL,
      total_turnover REAL, up_count INTEGER, down_count INTEGER);
    ''')
    # v2 migration: whole-market margin totals (上市+上櫃融資餘額合計, 單位:張)
    for _col in ('fin_tot REAL', 'fin_tot_prev REAL'):
        try:
            con.execute(f'ALTER TABLE market_daily ADD COLUMN {_col}')
        except Exception:
            pass
    con.commit()
    return con


def quote_table(mi):
    for t in mi.get('tables', []):
        if '每日收盤行情' in (t.get('title') or ''):
            return t
    return None


def is_trading_day(yyyymmdd):
    d = fetch(f'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={yyyymmdd}&type=ALLBUT0999')
    if not d:
        return False
    q = quote_table(d)
    return bool(q and q.get('data'))


def roc_date(yyyymmdd):
    return f'{int(yyyymmdd[:4]) - 1911}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}'


def etl_date(con, yyyymmdd):
    time.sleep(0.8)
    print(f'== ETL {yyyymmdd}')
    cur = con.cursor()

    # 1) institutional flows per stock
    t38 = fetch(f'https://www.twse.com.tw/rwd/zh/fund/TWT38U?response=json&date={yyyymmdd}')
    time.sleep(0.8)
    t44 = fetch(f'https://www.twse.com.tw/rwd/zh/fund/TWT44U?response=json&date={yyyymmdd}')
    time.sleep(0.8)
    t43 = fetch(f'https://www.twse.com.tw/rwd/zh/fund/TWT43U?response=json&date={yyyymmdd}')
    time.sleep(0.8)
    mi = fetch(f'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={yyyymmdd}&type=ALLBUT0999')
    time.sleep(0.8)
    # NOTE: the /rwd/zh/fund/BFI82U variant ignores the date param and always returns
    # the latest day; the legacy /fund/BFI82U with dayDate&type=day respects it.
    bfi = fetch(f'https://www.twse.com.tw/fund/BFI82U?response=json&dayDate={yyyymmdd}&type=day')
    time.sleep(0.8)
    mgn = fetch(f'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?response=json&date={yyyymmdd}&selectType=STOCK')
    time.sleep(0.8)
    # TPEx (OTC) counterparts — TWSE endpoints only cover listed stocks (~40 of 111 basket
    # stocks are OTC and would otherwise show zero)
    rd = roc_date(yyyymmdd)
    tpex3 = fetch(f'https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={rd}')
    time.sleep(0.8)
    tpexq = fetch(f'https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d={rd}&s=0,asc,0')
    time.sleep(0.8)
    tpexm = fetch(f'https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&d={rd}&o=daily')

    if not (t38 and t38.get('stat') == 'OK' and t38.get('data')):
        print(f'  skip {yyyymmdd}: no TWT38U data (holiday or not published)')
        return False

    flows = {}
    def put(code, k, v):
        flows.setdefault(code, {})[k] = v

    for r in t38['data']:
        code = r[1].strip()
        put(code, 'f_buy', num(r[9])); put(code, 'f_sell', num(r[10])); put(code, 'f_net', num(r[11]))
    if t44 and t44.get('data'):
        for r in t44['data']:
            code = r[1].strip()
            put(code, 't_buy', num(r[3])); put(code, 't_sell', num(r[4])); put(code, 't_net', num(r[5]))
    if t43 and t43.get('data'):
        for r in t43['data']:
            code = r[0].strip()
            put(code, 'd_buy', num(r[8])); put(code, 'd_sell', num(r[9])); put(code, 'd_net', num(r[10]))

    quotes = {}
    if mi:
        q = quote_table(mi)
        if q:
            for r in q['data']:
                code = r[0].strip()
                quotes[code] = {'close': num(r[8]), 'turnover': num(r[4]), 'spread': num(r[10])}

    # TPEx overlay for OTC stocks (absent from TWSE tables)
    # 3insti groups: g2=外資合計(idx8-10), g3=投信(idx11-13), g6=自營商合計(idx20-22)
    if tpex3:
        for t in tpex3.get('tables', []):
            for r in t.get('data', []):
                code = r[0].strip()
                if code not in flows:
                    put(code, 'f_buy', num(r[8])); put(code, 'f_sell', num(r[9])); put(code, 'f_net', num(r[10]))
                    put(code, 't_buy', num(r[11])); put(code, 't_sell', num(r[12])); put(code, 't_net', num(r[13]))
                    put(code, 'd_buy', num(r[20])); put(code, 'd_sell', num(r[21])); put(code, 'd_net', num(r[22]))
            break
    # TPEx overlay for OTC stocks (absent from TWSE tables).
    # The TPEx quote endpoint ignores the date param and always returns the latest
    # day -> only overlay when its date matches, otherwise OTC closes stay NULL
    # (a wrong close is worse than none; amounts get estimated in a repair step).
    if tpexq and tpexq.get('date') == yyyymmdd:
        for t in tpexq.get('tables', []):
            if '上櫃股票行情' in (t.get('title') or ''):
                for r in t.get('data', []):
                    code = r[0].strip()
                    if code not in quotes:
                        quotes[code] = {'close': num(r[2]) or None, 'turnover': num(r[9]) or None,
                                        'spread': num(r[3]) or None}
                break
    tpex_margin = {}
    if tpexm:
        for t in tpexm.get('tables', []):
            if '融資融券餘額' in (t.get('title') or ''):
                for r in t.get('data', []):
                    code = r[0].strip()
                    prev, today = num(r[2]), num(r[6])  # 前資餘額(張), 資餘額(張)
                    tpex_margin[code] = (prev, today)
                break

    # basket codes (union of all sectors)
    basket = json.load(open(f'{BASE}/basket.json', encoding='utf-8'))
    codes = {st['code'] for s in basket['sectors'] for st in s['stocks']}

    rows = 0
    for code in codes:
        fl = flows.get(code, {})
        q = quotes.get(code, {})
        close = q.get('close')  # None when no quote (OTC on historical dates, delisted)
        f_net, t_net, d_net = fl.get('f_net', 0.0), fl.get('t_net', 0.0), fl.get('d_net', 0.0)
        amt = lambda net: net * close / 1e8 if close else None
        cur.execute('''INSERT OR REPLACE INTO inst_flow VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (yyyymmdd, code,
             fl.get('f_buy', 0), fl.get('f_sell', 0), f_net,
             fl.get('t_buy', 0), fl.get('t_sell', 0), t_net,
             fl.get('d_buy', 0), fl.get('d_sell', 0), d_net,
             close, q.get('turnover'), q.get('spread'),
             amt(f_net), amt(t_net), amt(d_net)))
        rows += 1

    # margin (unit: 張) — TWSE first, TPEx overlay for OTC
    marg = {}
    twse_mtot = (0.0, 0.0)  # (前日餘額, 今日餘額) 上市合計
    if mgn:
        for t in mgn.get('tables', []):
            if '融資融券彙總' in (t.get('title') or ''):
                for r in t['data']:
                    code = r[0].strip()
                    if code == '合計':
                        twse_mtot = (num(r[4]), num(r[5]))
                    else:
                        marg[code] = (num(r[4]), num(r[5]))
                break
    for code, (prev, today) in tpex_margin.items():
        marg.setdefault(code, (prev, today))
    for code in codes:
        if code in marg:
            prev, today = marg[code]
            cur.execute('INSERT OR REPLACE INTO margin VALUES (?,?,?,?,?)',
                        (yyyymmdd, code, prev, today, today - prev))

    # market daily
    f_m = t_m = d_m = 0.0
    if bfi and bfi.get('data'):
        for r in bfi['data']:
            nm, v = r[0], num(r[3]) / 1e8
            if nm == '外資及陸資(不含外資自營商)': f_m += v
            elif nm == '外資自營商': f_m += v
            elif nm == '投信': t_m += v
            elif nm == '自營商(自行買賣)': d_m += v
            elif nm == '自營商(避險)': d_m += v
    tot, up, down = 0.0, 0, 0
    if mi:
        for t in mi.get('tables', []):
            ti = t.get('title') or ''
            if ti.endswith('大盤統計資訊'):
                for r in t['data']:
                    if r[0].strip() == '總計(1~15)':
                        tot = num(r[1]) / 1e8
            elif ti == '漲跌證券數合計':
                for r in t['data']:
                    m = re.match(r'(\d+)', r[2].replace(',', ''))
                    if not m:
                        continue
                    if r[0].startswith('上漲'): up = int(m.group(1))
                    elif r[0].startswith('下跌'): down = int(m.group(1))
    cur.execute('INSERT OR REPLACE INTO market_daily VALUES (?,?,?,?,?,?,?,?,?)',
                (yyyymmdd, f_m, t_m, d_m, tot, up, down,
                 twse_mtot[1] + sum(v[1] for v in tpex_margin.values()),
                 twse_mtot[0] + sum(v[0] for v in tpex_margin.values())))
    con.commit()
    print(f'  stored {rows} stocks; market f={f_m:.1f} t={t_m:.1f} d={d_m:.1f} tot={tot:.1f}億 up={up} down={down}')
    return True


def latest_trading_day():
    d = date.today()
    for _ in range(15):
        ymd = d.strftime('%Y%m%d')
        if is_trading_day(ymd):
            return ymd
        d -= timedelta(days=1)
    return None


def backfill(n):
    con = init_db()
    days = []
    d = date.today()
    while len(days) < n and (date.today() - d).days < 90:
        ymd = d.strftime('%Y%m%d')
        if is_trading_day(ymd):
            days.append(ymd)
        d -= timedelta(days=1)
    days.sort()
    print(f'backfilling {len(days)} trading days: {days[0]} .. {days[-1]}')
    for ymd in days:
        try:
            etl_date(con, ymd)
        except Exception as e:
            print(f'  !! {ymd} failed: {e}', file=sys.stderr)
    con.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--latest', action='store_true')
    ap.add_argument('--backfill', type=int)
    a = ap.parse_args()
    if a.backfill:
        backfill(a.backfill)
    elif a.latest:
        ymd = latest_trading_day()
        print('latest trading day:', ymd)
        if ymd:
            con = init_db()
            etl_date(con, ymd)
            con.close()
    elif a.date:
        con = init_db()
        etl_date(con, a.date)
        con.close()
    else:
        ap.print_help()
