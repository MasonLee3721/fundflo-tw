#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Metrics layer for the FundFlo replacement.
All amounts in 億元 TWD unless noted; margin delta in 張.

Per-stock daily metrics (for report date D):
  當日外資  = f_amt(D)                          外資合計買賣超金額
  當日ETF   = 0.0                               v1: not estimated (see README)
  當日複合  = 當日外資 + 當日ETF
  5日滾動   = sum of 當日外資 over last 5 trading days incl. D
  5日動能   = sum(last 5) - sum(prior 5)        v1 definition (documented in README)
  5日漲幅%  = (close_D / close_{D-5td} - 1) * 100
  連買天數  = consecutive trading days ending D with 當日外資 > 0
"""
import json, sqlite3

BASE = '/home/hatch/workspace/fundflo'
DB = f'{BASE}/fundflo.db'


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def load_basket():
    return json.load(open(f'{BASE}/basket.json', encoding='utf-8'))


def trading_dates(c, end=None, limit=60):
    q = 'SELECT date FROM market_daily' + (' WHERE date <= ?' if end else '') + ' ORDER BY date DESC LIMIT ?'
    args = ([end] if end else []) + [limit]
    return [r['date'] for r in c.execute(q, args)][::-1]


def latest_date(c):
    r = c.execute('SELECT MAX(date) d FROM market_daily').fetchone()
    return r['d'] if r else None


def stock_series(c, code, dates):
    """{date: row} for one stock over given dates."""
    rows = c.execute(
        'SELECT * FROM inst_flow WHERE stock=? AND date IN (%s)' % ','.join('?' * len(dates)),
        [code] + dates).fetchall()
    return {r['date']: r for r in rows}


def stock_metrics(c, code, D):
    dates = trading_dates(c, end=D, limit=12)
    if D not in dates:
        return None
    s = stock_series(c, code, dates)
    i = dates.index(D)
    last5 = dates[max(0, i - 4):i + 1]
    prior5 = dates[max(0, i - 9):max(0, i - 4)]
    f = lambda d: (s[d]['f_amt'] or 0.0) if d in s else 0.0
    roll = sum(f(d) for d in last5)
    mom = sum(f(d) for d in last5) - sum(f(d) for d in prior5) if len(prior5) == 5 else None
    closes = {d: (s[d]['close'] or 0.0) for d in dates if d in s}
    chg5 = None
    if i >= 5 and closes.get(D) and closes.get(dates[i - 5]):
        chg5 = (closes[D] / closes[dates[i - 5]] - 1) * 100
    streak = 0
    for d in reversed(dates[:i + 1]):
        if f(d) > 0:
            streak += 1
        else:
            break
    # momentum ending D-1 (for flip detection): needs 11+ dates with D at index>=10
    mom5_prev = None
    if i >= 10 and len(prior5) == 5:
        last5p = dates[i - 5:i]
        prior5p = dates[i - 10:i - 5]
        mom5_prev = sum(f(d) for d in last5p) - sum(f(d) for d in prior5p)
    # 5-day moving average of close
    ma5 = None
    cl5 = [closes.get(d) for d in dates[max(0, i - 4):i + 1]]
    cl5 = [x for x in cl5 if x]
    if len(cl5) == 5:
        ma5 = sum(cl5) / 5
    row = s.get(D)
    t_amt = (row['t_amt'] or 0.0) if row else 0.0
    return {
        'code': code, 'date': D,
        'f_day': f(D), 'etf_day': 0.0, 'combo': f(D),
        'roll5': roll, 'mom5': mom, 'mom5_prev': mom5_prev,
        'chg5': chg5, 'streak': streak, 'ma5': ma5,
        't_day': t_amt,
        'close': closes.get(D, 0.0),
    }


def margin_delta(c, code, D):
    r = c.execute('SELECT fin_delta FROM margin WHERE date=? AND stock=?', (D, code)).fetchone()
    return (r['fin_delta'] or 0.0) if r else 0.0


def sector_metrics(c, sector, D):
    stocks = []
    for st in sector['stocks']:
        m = stock_metrics(c, st['code'], D)
        if not m:
            continue
        m['name'] = st['name']
        m['role'] = st['role']
        m['note'] = st['note']
        m['sub_theme'] = st.get('sub_theme')
        m['fin_delta'] = margin_delta(c, st['code'], D)
        stocks.append(m)
    # per-stock daily turnover & pct change for aggregates
    dates = trading_dates(c, end=D, limit=3)
    turn, pcts, fsum, tsum, dsum, finsum = 0.0, [], 0.0, 0.0, 0.0, 0.0
    for m in stocks:
        r = c.execute('SELECT turnover, spread, close, t_amt, d_amt FROM inst_flow WHERE date=? AND stock=?',
                      (D, m['code'])).fetchone()
        if r:
            turn += (r['turnover'] or 0) / 1e8
            if r['close'] and r['spread'] is not None and (r['close'] - r['spread']) > 0:
                pcts.append(r['spread'] / (r['close'] - r['spread']) * 100)
            tsum += r['t_amt'] or 0
            dsum += r['d_amt'] or 0
        fsum += m['f_day']
        finsum += m['fin_delta']
    return {
        'sector': sector, 'date': D, 'stocks': stocks,
        'n': len(stocks), 'turnover': turn,
        'chg': (sum(pcts) / len(pcts)) if pcts else 0.0,
        'f_sum': fsum, 't_sum': tsum, 'd_sum': dsum, 'fin_sum': finsum,
    }


def market_row(c, D):
    r = c.execute('SELECT * FROM market_daily WHERE date=?', (D,)).fetchone()
    return dict(r) if r else None


def sector_flow5(c, sector, D):
    """Sector-level 5d aggregates for ranking: (roll5_sum, mom5_sum, chg5_avg)."""
    roll = mom = 0.0
    chgs = []
    for st in sector['stocks']:
        m = stock_metrics(c, st['code'], D)
        if not m:
            continue
        roll += m['roll5'] or 0.0
        if m['mom5'] is not None:
            mom += m['mom5']
        if m['chg5'] is not None:
            chgs.append(m['chg5'])
    return {'roll5': roll, 'mom5': mom,
            'chg5': (sum(chgs) / len(chgs)) if chgs else None}


def prev_trading_date(c, D):
    r = c.execute('SELECT MAX(date) d FROM market_daily WHERE date < ?', (D,)).fetchone()
    return r['d'] if r and r['d'] else None


def consecutive_rank(c, D, top=15):
    """All basket stocks ranked by 外資連買天數 (then by 當日外資)."""
    basket = load_basket()
    out = []
    for s in basket['sectors']:
        for st in s['stocks']:
            m = stock_metrics(c, st['code'], D)
            if m:
                out.append({'code': st['code'], 'name': st['name'],
                            'sector': s['name'], 'streak': m['streak'], 'f_day': m['f_day']})
    seen, ranked = set(), []
    for o in sorted(out, key=lambda x: (-x['streak'], -x['f_day'])):
        if o['code'] in seen:
            continue
        seen.add(o['code'])
        ranked.append(o)
    return ranked[:top]
