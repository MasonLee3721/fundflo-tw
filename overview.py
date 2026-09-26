#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大盤總覽日報 (HTML) — 每天唯一必看的一份。
結構：國際總經 / 台幣匯率技術面 / 台股大盤 / 38族群資金排序 /
      競爭者觀察 / 未來上漲候選 / 盲點與限制 / 研究整理聲明

可調參數見 CFG。訊號定義（給 run_daily.sh 用）：
  當日外資淨流入排名前 SIGNAL_TOP_N，或 5日動能由負翻正。

Usage:
  python3 overview.py --date 20260924 --out reports/20260924/OVERVIEW.html
  python3 overview.py --date 20260924 --signals   # 只印有訊號的族群 ID（空白分隔）
"""
import argparse
import compute
import report as R

DISCLAIMER = R.DISCLAIMER

CFG = {
    'Y10_ALERT_BPS': 10,     # 10Y 單日變動警戒門檻 (bps)
    'TP_BREAKOUT_DAYS': 20,  # 期限溢價突破 N 日高點 → 警戒
    'DIVERGE_MIN': 1.0,      # 龍頭/二線分歧：各自金額門檻 (億)
    'SIGNAL_TOP_N': 8,       # 當日外資淨流入排名前 N → 有訊號
    'MIN_STREAK': 3,         # 候選：外資連買天數下限
}

LEAD_ROLES = {'龍頭霸主', '核心龍頭'}

TRANSMISSION = (
    '傳導邏輯：美債 10Y 殖利率上行 → 美元走強、資金回流美國 → 外資賣超台股、台幣走貶；'
    '台幣貶值又墊高外資匯兌成本，使其更不願匯入，形成同一方向的循環。'
    '期限溢價走高代表市場要求更高的持有長債補償（心理面轉趨謹慎），成長股通常先承壓。'
    '反之，10Y 回落＋台幣轉升，往往是外資回頭的前兆。'
)


def _latest_le(c, table, col, D, extra=''):
    q = f'SELECT date, {col} FROM {table} WHERE date <= ? AND {col} IS NOT NULL {extra} ORDER BY date DESC LIMIT 1'
    r = c.execute(q, (D,)).fetchone()
    return (r['date'], r[col]) if r else (None, None)


def _series(c, table, col, end, n):
    rows = c.execute(
        f'SELECT date, {col} FROM {table} WHERE date <= ? AND {col} IS NOT NULL ORDER BY date DESC LIMIT ?',
        (end, n)).fetchall()
    return [(r['date'], r[col]) for r in rows][::-1]


def macro_section(c, D):
    L = ['## 一、國際總經（以下皆為 T+1 資料，日期如標示）']
    # 10Y
    d10, v10 = _latest_le(c, 'macro_daily', 'dgs10', D)
    s10 = _series(c, 'macro_daily', 'dgs10', D, 3)
    bps_txt, alert10 = '—', ''
    if v10 is not None and len(s10) >= 2:
        bps = (s10[-1][1] - s10[-2][1]) * 100
        bps_txt = f'{bps:+.0f}bps（{s10[-2][0]}→{d10}）'
        if abs(bps) >= CFG['Y10_ALERT_BPS']:
            alert10 = ' ⚠達警戒（±10bps）'
    # 2Y + spread
    d2, v2 = _latest_le(c, 'macro_daily', 'dgs2', D)
    spread_txt = '—'
    if v10 is not None and v2 is not None and d10 == d2:
        sp = (v10 - v2) * 100
        s5 = _series(c, 'macro_daily', 'dgs10', D, 6)
        s5b = _series(c, 'macro_daily', 'dgs2', D, 6)
        dsp = ''
        if len(s5) >= 6 and len(s5b) >= 6:
            dsp5 = ((s5[-1][1] - s5[-6][1]) - (s5b[-1][1] - s5b[-6][1])) * 100
            dsp = '，近5日' + ('陡峭化中' if dsp5 > 5 else '平坦化中' if dsp5 < -5 else '斜率持穩')
        shape = '正斜率' if sp > 0 else '倒掛' if sp < 0 else '持平'
        spread_txt = f'10Y-2Y = {sp:+.0f}bps，曲線{shape}{dsp}'
    # term premium
    dtp, vtp = _latest_le(c, 'macro_daily', 'acm_tp10', D)
    tp_txt, alert_tp = '—', ''
    if vtp is not None:
        hist = _series(c, 'macro_daily', 'acm_tp10', D, CFG['TP_BREAKOUT_DAYS'] + 1)
        prior_high = max((v for _, v in hist[:-1]), default=None)
        breakout = prior_high is not None and vtp > prior_high and len(hist) > 1
        tp_txt = f'{vtp:.2f}%（資料日 {dtp}）' + ('，**突破20日高點** ⚠' if breakout else '，未突破20日高點')
        if breakout:
            alert_tp = ' ⚠達警戒'
    L.append('| 指標 | 水準 | 變化／狀態 |')
    L.append('|---|---|---|')
    L.append(f'| [美國 10Y 公債殖利率](https://fred.stlouisfed.org/series/DGS10) | {f"{v10:.2f}%" if v10 is not None else "—"}（{d10 or "無"}） | 單日 {bps_txt}{alert10} |')
    L.append(f'| [10Y 期限溢價（ACM）](https://www.newyorkfed.org/research/data_indicators/term-premia-tabs) | {tp_txt}{alert_tp} | 心理面：愈高代表市場愈謹慎 |')
    L.append(f'| [10Y-2Y 利差](https://fred.stlouisfed.org/series/T10Y2Y) | {spread_txt} | 一句話：曲線形狀看景氣預期 |')
    L.append('')
    L.append(TRANSMISSION)
    L.append('')
    return L


def twd_section(c, D):
    L = ['## 二、[台幣匯率技術面](https://www.investing.com/currencies/usd-twd)（Yahoo 參考匯率，非央行收盤價；數字愈大＝台幣愈貶）']
    s = _series(c, 'macro_daily', 'twd', D, 30)
    if len(s) < 2:
        L.append('無資料'); L.append('')
        return L
    d0, v0 = s[-1]
    d1, v1 = s[-2]
    chg = (v0 - v1) / v1 * 100
    direction = '貶值' if chg > 0 else '升值' if chg < 0 else '持平'
    streak = 1
    for i in range(len(s) - 1, 1, -1):
        a = (s[i][1] - s[i - 1][1])
        b = (s[i - 1][1] - s[i - 2][1])
        if (a > 0) == (b > 0) and a != 0:
            streak += 1
        else:
            break
    ma20 = None
    if len(s) >= 20:
        ma20 = sum(v for _, v in s[-20:]) / 20
    pos = ''
    if ma20:
        pos = '站上月線（偏貶格局）' if v0 > ma20 else '跌破月線（偏升格局）'
    L.append('| 項目 | 數值 |')
    L.append('|---|---|')
    L.append(f'| 收盤（{d0}） | {v0:.3f} |')
    L.append(f'| 單日變化 | {direction} {abs(chg):.2f}% |')
    L.append(f'| 連續{direction} | {streak} 天 |')
    L.append(f'| 月線（20日均） | {f"{ma20:.3f}，{pos}" if ma20 else "—"} |')
    L.append('')
    return L


def market_section(c, D):
    L = [f'## 三、台股大盤（{D}）']
    mkt = compute.market_row(c, D)
    if not mkt:
        L.append('無資料'); L.append('')
        return L
    fin_chg = None
    if mkt.get('fin_tot') is not None and mkt.get('fin_tot_prev'):
        fin_chg = (mkt['fin_tot'] - mkt['fin_tot_prev']) / 1e4  # 張→萬張
        fin_pct = (mkt['fin_tot'] - mkt['fin_tot_prev']) / mkt['fin_tot_prev'] * 100
    L.append('| 項目 | 數值 | 讀法 |')
    L.append('|---|---|---|')
    L.append(f'| 外資淨額 | {R.fa(mkt["f_net"])} 億 | {"偏賣" if mkt["f_net"] < 0 else "偏買"} |')
    L.append(f'| 投信淨額 | {R.fa(mkt["t_net"])} 億 | {"偏賣" if mkt["t_net"] < 0 else "偏買"} |')
    L.append(f'| 自營淨額 | {R.fa(mkt["d_net"])} 億 | {"偏買" if mkt["d_net"] > 0 else "偏賣"} |')
    L.append(f'| 總成交｜漲家｜跌家 | {mkt["total_turnover"]:,.2f} 億｜{mkt["up_count"]}｜{mkt["down_count"]} | '
             f'{"跌多漲少" if mkt["down_count"] > mkt["up_count"] else "漲多跌少"} |')
    if fin_chg is not None:
        L.append(f'| 融資餘額變化（上市＋上櫃） | {fin_chg:+.1f} 萬張（{fin_pct:+.2f}%） | '
                 f'{"槓桿升溫" if fin_chg > 0 else "槓桿降溫"} |')
    else:
        L.append('| 融資餘額變化 | — | 資料未回補 |')
    L.append('')
    return L


def sector_table(c, D):
    """[(sector, f_sum, roll5, mom5, chg5)] sorted by f_sum desc."""
    basket = compute.load_basket()
    out = []
    for s in basket['sectors']:
        sm = compute.sector_metrics(c, s, D)
        f5 = compute.sector_flow5(c, s, D)
        out.append({'sector': s, 'f_sum': sm['f_sum'], 'roll5': f5['roll5'],
                    'mom5': f5['mom5'], 'chg5': f5['chg5']})
    out.sort(key=lambda x: -x['f_sum'])
    return out


def rank_section(c, D, ranked):
    L = ['## 四、38 族群資金排序（按當日外資淨流入）']
    L.append('| # | 族群 | 當日外資(億) | 5日滾動(億) | 5日動能(億) | 5日漲幅 |')
    L.append('|---|---|---|---|---|---|')
    for i, r in enumerate(ranked, 1):
        L.append(f'| {i} | {r["sector"]["name"]} | {R.fa(r["f_sum"])} | {R.fa(r["roll5"])} | '
                 f'{R.fa(r["mom5"])} | {R.fp(r["chg5"])} |')
    L.append('')
    return L


def competitors_section(c, D, ranked):
    L = ['## 五、競爭者觀察']
    # (a) 正流入族群：吸金前3 vs 失血前3
    pos_secs = [r for r in ranked if r['f_sum'] > 0.005]
    L.append(f'### （a）正流入族群（{len(pos_secs)} 個）：族群內誰吸金、誰失血')
    if pos_secs:
        L.append('| 族群 | 吸金前 3 | 失血前 3 |')
        L.append('|---|---|---|')
        for r in pos_secs:
            sm = compute.sector_metrics(c, r['sector'], D)
            ss = sorted(sm['stocks'], key=lambda m: -m['f_day'])
            top3 = '、'.join(f'{m["code"]}{m["name"]}{R.fa(m["f_day"])}' for m in ss[:3] if m['f_day'] > 0.005)
            bot3 = '、'.join(f'{m["code"]}{m["name"]}{R.fa(m["f_day"])}' for m in ss[-3:] if m['f_day'] < -0.005)
            L.append(f'| {r["sector"]["name"]} | {top3 or "—"} | {bot3 or "—"} |')
    else:
        L.append('當日無正流入族群。')
    L.append('')
    # (b) 龍頭與二線分歧
    L.append('### （b）龍頭 vs 二線資金分歧')
    divs = []
    for r in ranked:
        sm = compute.sector_metrics(c, r['sector'], D)
        lead = sum(m['f_day'] for m in sm['stocks'] if m.get('role') in LEAD_ROLES)
        second = sum(m['f_day'] for m in sm['stocks'] if m.get('role') not in LEAD_ROLES)
        if lead * second < 0 and abs(lead) >= CFG['DIVERGE_MIN'] and abs(second) >= CFG['DIVERGE_MIN']:
            who = '龍頭被賣、二線被買（換股訊號）' if lead < 0 else '龍頭被買、二線被賣（集中訊號）'
            divs.append((r['sector']['name'], lead, second, who))
    if divs:
        L.append('| 族群 | 龍頭合計(億) | 二線合計(億) | 解讀 |')
        L.append('|---|---|---|---|')
        for name, lead, second, who in divs:
            L.append(f'| {name} | {R.fa(lead)} | {R.fa(second)} | {who} |')
    else:
        L.append('當日無顯著分歧族群（門檻：龍頭與二線各自 ≥1 億且方向相反）。')
    L.append('')
    # (c) 5日動能名次升降 Top5
    L.append('### （c）跨族群 5 日動能名次升降 Top 5（資金搬家）')
    Dp = compute.prev_trading_date(c, D)
    if Dp:
        def mom_rank(dd):
            rows = []
            for s in compute.load_basket()['sectors']:
                f5 = compute.sector_flow5(c, s, dd)
                rows.append((s['id'], s['name'], f5['mom5']))
            rows.sort(key=lambda x: -x[2])
            return {rid: (i + 1, nm, mo) for i, (rid, nm, mo) in enumerate(rows)}
        r0, r1 = mom_rank(Dp), mom_rank(D)
        moves = []
        for rid, (rk1, nm, mo1) in r1.items():
            rk0 = r0[rid][0]
            if rk1 != rk0:
                moves.append((abs(rk1 - rk0), rk0, rk1, nm, mo1))
        moves.sort(key=lambda x: -x[0])
        if moves:
            L.append('| 族群 | 前日名次 | 今日名次 | 升降 | 5日動能(億) |')
            L.append('|---|---|---|---|---|')
            for _, rk0, rk1, nm, mo1 in moves[:5]:
                arrow = f'↑{rk0 - rk1}' if rk1 < rk0 else f'↓{rk1 - rk0}'
                L.append(f'| {nm} | {rk0} | {rk1} | {arrow} | {R.fa(mo1)} |')
        else:
            L.append('名次無變動。')
    else:
        L.append('前一交易日無資料。')
    L.append('')
    return L


def candidates_section(c, D):
    L = ['## 六、未來上漲候選（全市場篩選）']
    L.append(f'條件：5日動能由負翻正 ＋ 外資連買 ≥{CFG["MIN_STREAK"]} 天 ＋ 收盤站上 5 日線（參數可調）')
    basket = compute.load_basket()
    sec_of, seen, cands = {}, set(), []
    for s in basket['sectors']:
        for st in s['stocks']:
            sec_of.setdefault(st['code'], s['name'])
    for code in sec_of:
        m = compute.stock_metrics(c, code, D)
        if not m:
            continue
        if (m['mom5'] is not None and m['mom5_prev'] is not None
                and m['mom5'] > 0 and m['mom5_prev'] < 0
                and m['streak'] >= CFG['MIN_STREAK']
                and m['ma5'] and m['close'] and m['close'] > m['ma5']):
            nm = next((st['name'] for s in basket['sectors'] for st in s['stocks'] if st['code'] == code), '')
            cands.append({'code': code, 'name': nm, 'sector': sec_of[code],
                          'mom_prev': m['mom5_prev'], 'mom': m['mom5'],
                          'streak': m['streak'], 'close': m['close'],
                          'ma5': m['ma5'], 'chg5': m['chg5'], 'f_day': m['f_day']})
    cands.sort(key=lambda x: -x['mom'])
    if cands:
        L.append('| 代號 | 名稱 | 族群 | 5日動能（前→今） | 連買天數 | 收盤 | 5日線 | 當日外資(億) | 5日漲幅 |')
        L.append('|---|---|---|---|---|---|---|---|---|')
        for x in cands:
            L.append(f'| {x["code"]} | {x["name"]} | {x["sector"]} | {R.fa(x["mom_prev"])}→{R.fa(x["mom"])} | '
                     f'{x["streak"]} 天 | {x["close"]:.2f} | {x["ma5"]:.2f} | {R.fa(x["f_day"])} | {R.fp(x["chg5"])} |')
    else:
        L.append('當日無符合條件者。')
    L.append('')
    return L


def blind_spots():
    return [
        '## 七、盲點與限制',
        '- v1 當日ETF 欄全為 0：未做 ETF 被動歸因，不得做「主動 vs 被動」判讀。',
        '- 上櫃歷史金額為估算值（以最新收盤價估算，沿用既有做法）；上櫃歷史 5 日漲幅缺資料時顯示 —。',
        '- 總經數據一律 T+1：10Y/期限溢價為美國前一交易日收盤，報告已標示資料日期。',
        '- 台幣匯率為 Yahoo 參考匯率（非央行收盤價），僅供技術面觀察。',
        '- 5日動能＝近5日合計−前5日合計，為 v1 近似定義（原 FundFlo 公式未公開）。',
        '',
    ]


def build(D=None):
    c = compute.con()
    D = D or compute.latest_date(c)
    ranked = sector_table(c, D)
    mkt = compute.market_row(c, D)

    L = [f'# 大盤總覽｜市場觀察日報（FundFlo 替代版 v1）']
    L.append(f'台股 as_of {D}｜總經為 T+1（日期見各表）｜來源：TWSE 公開資料 ETL、FRED、NY Fed、Yahoo Finance')
    L.append('')
    # 一句話
    npos = sum(1 for r in ranked if r['f_sum'] > 0.005)
    top = ranked[0] if ranked else None
    if mkt and top:
        L.append(f'一句話：{D} 外資{"淨賣超" if mkt["f_net"] < 0 else "淨買超"} {R.fa(mkt["f_net"])} 億的大盤底色下，'
                 f'38 個族群中 {npos} 個當日正流入，吸金第一為{top["sector"]["name"]}（{R.fa(top["f_sum"])} 億）。')
        L.append('')
    L += macro_section(c, D)
    L += twd_section(c, D)
    L += market_section(c, D)
    L += rank_section(c, D, ranked)
    L += competitors_section(c, D, ranked)
    L += candidates_section(c, D)
    L += blind_spots()
    L.append(DISCLAIMER)
    c.close()
    return '\n'.join(L) + '\n'


def signal_sectors(D=None):
    """族群 ID 清單：當日外資淨流入前 N，或 5日動能由負翻正。"""
    c = compute.con()
    D = D or compute.latest_date(c)
    ranked = sector_table(c, D)
    Dp = compute.prev_trading_date(c, D)
    mom_prev = {}
    if Dp:
        for s in compute.load_basket()['sectors']:
            mom_prev[s['id']] = compute.sector_flow5(c, s, Dp)['mom5']
    sig, seen = [], set()
    for r in ranked[:CFG['SIGNAL_TOP_N']]:
        sig.append(r['sector']['id'])
        seen.add(r['sector']['id'])
    for r in ranked:
        rid = r['sector']['id']
        mp = mom_prev.get(rid)
        if rid not in seen and mp is not None and mp < 0 and r['mom5'] > 0:
            sig.append(rid)
            seen.add(rid)
    c.close()
    return sig


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--out')
    ap.add_argument('--signals', action='store_true')
    a = ap.parse_args()
    if a.signals:
        print(' '.join(signal_sectors(a.date)))
    else:
        md = build(a.date)
        title = '大盤總覽｜市場觀察日報'
        doc = R.md_to_html(md, title)
        out = a.out or f'{compute.BASE}/overview_{a.date or compute.latest_date(compute.con())}.html'
        open(out, 'w', encoding='utf-8').write(doc)
        print('wrote', out)
