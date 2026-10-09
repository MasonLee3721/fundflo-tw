#!/usr/bin/env python3
"""台股全市場廣度 ETL：每日計算上市櫃普通股站上季線(60MA)／年線(200MA)的比例。

資料源：SectorDetect 全市場 price_daily（還原價）。
結果存 fundflo.db 的 market_breadth 表（date, n, pct60, pct200），冪等可重跑。

用法：
  python3 breadth_etl.py --backfill   # 全歷史回填（初次）
  python3 breadth_etl.py --update     # 每日增量（取 price_daily 最新 300 個交易日重算後 upsert）
"""
import argparse
import sqlite3
import sys

SEC_DB = '/home/hatch/workspace/sector-detect/sector_detect.db'
FUND_DB = '/home/hatch/workspace/fundflo/fundflo.db'
TAIL_DAYS = 300  # 增量更新時取的尾部交易日數（>200，確保 MA200 可算）


def compute_breadth(full=False):
    import pandas as pd
    scon = sqlite3.connect(SEC_DB)
    if full:
        q = 'SELECT date, stock, close FROM price_daily ORDER BY stock, date'
        df = pd.read_sql(q, scon)
    else:
        tail0 = pd.read_sql(
            'SELECT DISTINCT date FROM price_daily ORDER BY date DESC LIMIT %d' % TAIL_DAYS, scon)
        d0 = tail0['date'].min()
        df = pd.read_sql('SELECT date, stock, close FROM price_daily WHERE date >= ? '
                         'ORDER BY stock, date', scon, params=(d0,))
    scon.close()
    if df.empty:
        return df
    df = df.dropna(subset=['close'])
    # 先在完整歷史上算均線，再過濾（避免先過濾截斷 rolling 窗口）
    g = df.groupby('stock')['close']
    df['ma60'] = g.transform(lambda s: s.rolling(60, min_periods=60).mean())
    df['ma200'] = g.transform(lambda s: s.rolling(200, min_periods=200).mean())
    # 同一宇宙比較季線/年線：只取兩條均線都有效的列（即有 ≥200 個收盤價的股票）
    df = df.dropna(subset=['ma60', 'ma200']).copy()
    df['a60'] = (df['close'] > df['ma60']).astype(int)
    df['a200'] = (df['close'] > df['ma200']).astype(int)
    out = (df.groupby('date')
             .agg(n=('stock', 'size'), pct60=('a60', 'mean'), pct200=('a200', 'mean'))
             .reset_index())
    out['pct60'] = (out['pct60'] * 100).round(2)
    out['pct200'] = (out['pct200'] * 100).round(2)
    return out[['date', 'n', 'pct60', 'pct200']]


def save(df):
    fcon = sqlite3.connect(FUND_DB)
    fcon.execute('CREATE TABLE IF NOT EXISTS market_breadth('
                 'date TEXT PRIMARY KEY, n INTEGER, pct60 REAL, pct200 REAL)')
    rows = [(r['date'], int(r['n']), float(r['pct60']), float(r['pct200']))
            for _, r in df.iterrows()]
    fcon.executemany('INSERT OR REPLACE INTO market_breadth(date,n,pct60,pct200) VALUES(?,?,?,?)',
                     rows)
    fcon.commit()
    n = fcon.execute('SELECT COUNT(*) FROM market_breadth').fetchone()[0]
    fcon.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill', action='store_true')
    ap.add_argument('--update', action='store_true')
    a = ap.parse_args()
    try:
        df = compute_breadth(full=a.backfill or not (a.backfill or a.update))
    except Exception as e:
        print(f'breadth etl skipped: {e}', file=sys.stderr)
        return 0
    if df.empty:
        print('breadth etl: no data')
        return 0
    n = save(df)
    lo, hi = df['date'].min(), df['date'].max()
    print(f'breadth upserted {len(df)} dates ({lo}..{hi}); market_breadth now {n} rows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
