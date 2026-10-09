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


def _spark(vals):
    """文字 sparkline（近 N 日走勢），相對 min-max 縮放，看膨脹/收縮形狀。"""
    blocks = '▁▂▃▄▅▆▇█'
    vals = [v for v in vals if v is not None]
    if not vals:
        return '—'
    mn, mx = min(vals), max(vals)
    if mx - mn < 1e-9:
        return blocks[0] * len(vals)
    return ''.join(blocks[min(7, int((v - mn) / (mx - mn) * 8))] for v in vals)


def _breadth_rows(c):
    """台股廣度：站上季線(60MA)/年線(200MA)比例＋近5日變化＋近20日走勢。"""
    L = []
    try:
        rows = c.execute('SELECT date, pct60, pct200 FROM market_breadth ORDER BY date DESC LIMIT 21'
                         ).fetchall()
    except Exception:
        rows = []
    if not rows:
        L.append('| 廣度｜季線/年線 | 資料建置中 | — |')
        return L
    rows = list(reversed(rows))
    bd = rows[-1][0]
    p60 = [r[1] for r in rows]
    p200 = [r[2] for r in rows]
    d5_60 = p60[-1] - p60[-6] if len(p60) >= 6 else None
    d5_200 = p200[-1] - p200[-6] if len(p200) >= 6 else None
    sp60 = _spark(p60[-20:])
    sp200 = _spark(p200[-20:])
    d5t = lambda d: f'{d:+.1f}pct' if d is not None else '—'
    dir60 = '膨脹' if (d5_60 or 0) > 0 else '收縮'
    dir200 = '膨脹' if (d5_200 or 0) > 0 else '收縮'
    L.append(f'| 廣度｜季線(60MA) | {p60[-1]:.1f}% 站上季線（{bd}）｜近5日 {d5t(d5_60)}｜近20日 {sp60} | '
             f'{dir60}＝廣度{"改善（多頭擴散）" if dir60 == "膨脹" else "轉弱（僅靠權值撐）"} |')
    L.append(f'| 廣度｜年線(200MA) | {p200[-1]:.1f}% 站上年線（{bd}）｜近5日 {d5t(d5_200)}｜近20日 {sp200} | '
             f'{dir200}＝廣度{"改善（多頭擴散）" if dir200 == "膨脹" else "轉弱（僅靠權值撐）"} |')
    return L

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
    # 美元指數
    ddx, vdx = _latest_le(c, 'macro_daily', 'usdx', D)
    sdx = _series(c, 'macro_daily', 'usdx', D, 3)
    dx_txt = '—'
    if vdx is not None and len(sdx) >= 2:
        chg = sdx[-1][1] - sdx[-2][1]
        dx_txt = f'單日 {chg:+.2f} 點（{sdx[-2][0]}→{ddx}）'
    # Brent 原油（使用者觀察區間 $98–102）
    dbr, vbr = _latest_le(c, 'macro_daily', 'brent', D)
    sbr = _series(c, 'macro_daily', 'brent', D, 3)
    br_txt = '—'
    if vbr is not None and len(sbr) >= 2:
        chg = sbr[-1][1] - sbr[-2][1]
        br_txt = f'單日 {chg:+.2f}（{sbr[-2][0]}→{dbr}）'
    band = ''
    if vbr is not None:
        band = ('，**突破 $102 上緣** ⚠' if vbr > 102
                else '，**跌破 $98 下緣** ⚠' if vbr < 98
                else '，在 $98–102 區間內')
    L.append('| 指標 | 水準 | 變化／狀態 |')
    L.append('|---|---|---|')
    L.append(f'| [美國 10Y 公債殖利率](https://www.wantgoo.com/global/us10-yr) | {f"{v10:.2f}%" if v10 is not None else "—"}（{d10 or "無"}） | 單日 {bps_txt}{alert10} |')
    L.append(f'| [美元指數](https://www.wantgoo.com/global/usdindex) | {f"{vdx:.2f}" if vdx is not None else "—"}（{ddx or "無"}） | {dx_txt} |')
    L.append(f'| [Brent 原油期貨](https://finance.yahoo.com/quote/BZ%3DF/) | {f"${vbr:.2f}" if vbr is not None else "—"}（{dbr or "無"}） | {br_txt}{band} |')
    L.append(f'| [10Y 期限溢價（ACM）](https://www.newyorkfed.org/research/data_indicators/term-premia-tabs) | {tp_txt}{alert_tp} | 心理面：愈高代表市場愈謹慎 |')
    L.append(f'| [10Y-2Y 利差](https://fred.stlouisfed.org/series/T10Y2Y) | {spread_txt} | 一句話：曲線形狀看景氣預期 |')
    L.append('')
    L.append(TRANSMISSION)
    L.append('')
    return L


def twd_section(c, D):
    L = ['## 二、[台幣匯率技術面](https://www.wantgoo.com/global/usdtwd)（Yahoo 參考匯率，非央行收盤價；數字愈大＝台幣愈貶）']
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
    up, down = mkt.get('up_count'), mkt.get('down_count')
    if up and down:
        ratio = up / down
        L.append(f'| [市場寬度](https://www.wantgoo.com/stock/market-breadth-index) | '
                 f'上漲 {up} 家｜下跌 {down} 家，漲跌比 {ratio:.2f} | '
                 f'{"漲多跌少" if up > down else "跌多漲少"}；'
                 f'均線寬度（20/60/240日，85%過熱／15%超賣）看連結圖 |')
    # ADL 騰落線：Σ(上漲−下跌)，只看方向與轉折，不看絕對值
    adl_rows = c.execute(
        'SELECT date, up_count, down_count FROM market_daily '
        'WHERE up_count IS NOT NULL AND down_count IS NOT NULL AND date <= ? '
        'ORDER BY date', (D,)).fetchall()
    if adl_rows:
        nets = [(d, u - dn) for d, u, dn in adl_rows]
        adl = sum(n for _, n in nets)
        chg5 = sum(n for _, n in nets[-5:])
        streak, sign = 0, None
        for _, n in reversed(nets):
            s = 1 if n > 0 else -1 if n < 0 else 0
            if sign is None:
                sign, streak = s, 1
            elif s == sign and s != 0:
                streak += 1
            else:
                break
        trend = f'連{"升" if sign > 0 else "跌"}{streak}日' if sign else '持平'
        L.append(f'| [騰落線 ADL](https://www.wantgoo.com/stock/market-breadth-index) | '
                 f'{len(nets)}日累計 {adl:+,}，近5日 {chg5:+,}（{trend}） | '
                 f'看方向不看絕對值；與指數背離時留意 |')
    # 台股廣度：站上季線/年線比例＋趨勢（膨脹/收縮）
    L += _breadth_rows(c)
    if fin_chg is not None:
        L.append(f'| 融資餘額變化（上市＋上櫃） | {fin_chg:+.1f} 萬張（{fin_pct:+.2f}%） | '
                 f'{"槓桿升溫" if fin_chg > 0 else "槓桿降溫"} |')
    else:
        L.append('| 融資餘額變化 | — | 資料未回補 |')
    L.extend(_struct_rows(c, D))
    L.append('')
    return L


def _struct_rows(c, D):
    """結構指標（張林忠三工具量化版）：電金強弱 / MNQ-MYM / 台指期貨籌碼。"""
    L = []
    cols = ('date,semi_idx,fin_idx,elec_fin_ratio,mnq,mym,mnq_mym_ratio,'
            'fut_for_net_oi,fut_inv_net_oi,fut_trust_net_oi,fut_retail_net_oi')
    try:
        r = c.execute(f'SELECT {cols} FROM market_struct WHERE date <= ? '
                      'ORDER BY date DESC LIMIT 1', (D,)).fetchone()
    except Exception:
        return L
    if not r:
        return L
    (d, semi, fin, efr, mnq, mym, mmr, fo, io, to, ro) = r
    if efr is not None:
        hist = c.execute('SELECT elec_fin_ratio FROM market_struct WHERE date <= ? '
                         'AND elec_fin_ratio IS NOT NULL ORDER BY date DESC LIMIT 21',
                         (D,)).fetchall()
        chg = efr - hist[-1][0] if len(hist) == 21 else None
        chg_s = f'（20日 {chg:+.3f}）' if chg is not None else ''
        trend = '資金偏電子/科技' if (chg or 0) > 0 else '資金偏金融/傳產' if (chg or 0) < 0 else '方向持平'
        L.append(f'| 電金強弱（半導體／金融） | {efr:+.3f}{chg_s}｜半導體 {semi:,.0f}／金融 {fin:,.0f} | '
                 f'{trend}；看方向不看絕對值 |')
    if mmr is not None:
        L.append(f'| 美股 MNQ/MYM 強弱 | {mmr:+.3f}｜MNQ {mnq:,.0f}／MYM {mym:,.0f} | '
                 f'上升＝科技轉強、下降＝傳產轉強 |')
    if fo is not None:
        hist = [x[0] for x in c.execute(
            'SELECT fut_for_net_oi FROM market_struct WHERE fut_for_net_oi IS NOT NULL '
            'ORDER BY date DESC LIMIT 250').fetchall()]
        pct = sum(1 for x in hist if x <= fo) / len(hist) * 100 if hist else None
        lvl = ('空單水位高' if pct is not None and pct <= 20 else
               '水位中等' if pct is not None and pct <= 80 else '水位低')
        ro_s = f'｜散戶推算 {ro:+,.0f}口' if ro is not None else ''
        L.append(f'| 台指期貨籌碼（{d}） | 外資淨 {fo:+,.0f}口（1年分位 {pct:.0f}%）{ro_s} | '
                 f'{lvl}＝潛在軋空燃料；散戶指標今年已漂移，僅觀察 |')
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


def pullback_section(c, D):
    """回跌近支撐候選：收盤拉回至 20 日線附近（-3%～+1%）、跌破 5 日線（短線回跌）、
    且外資近 5 日淨額 ≥ 0（未轉大賣）。不給支撐/壓力價位，點代號開玩股網技術線圖自行判斷。"""
    L = ['## 七、回跌近支撐候選（弱勢盤整盤短線用）']
    L.append('條件：收盤在 20 日線 -3%～+1% 區間 ＋ 跌破 5 日線（短線回跌）＋ 外資近 5 日淨額 ≥ 0（未轉大賣）。'
             '僅篩選、不做買賣建議；點代號開玩股網技術線圖自行判斷支撐壓力。')
    basket = compute.load_basket()
    sec_of, names = {}, {}
    for s in basket['sectors']:
        for st in s['stocks']:
            sec_of.setdefault(st['code'], s['name'])
            names.setdefault(st['code'], st['name'])
    cands = []
    for code in sec_of:
        rows = c.execute('SELECT close, f_amt FROM inst_flow WHERE stock=? AND date<=? ORDER BY date',
                         (code, D)).fetchall()
        rows = [r for r in rows if r['close']]
        if len(rows) < 25:
            continue
        closes = [r['close'] for r in rows]
        ma20 = sum(closes[-20:]) / 20
        ma5 = sum(closes[-5:]) / 5
        close = closes[-1]
        bias = close / ma20 - 1
        if not (-0.03 <= bias <= 0.01):
            continue
        if not (close < ma5):
            continue
        f5 = sum((r['f_amt'] or 0.0) for r in rows[-5:])
        if f5 < 0:
            continue
        chg5 = (close / closes[-6] - 1) * 100
        cands.append({'code': code, 'name': names[code], 'sector': sec_of[code],
                      'close': close, 'bias': bias, 'chg5': chg5, 'f5': f5})
    cands.sort(key=lambda x: x['bias'])
    if cands:
        L.append('| 代號 | 名稱 | 族群 | 收盤 | 乖離20MA | 5日漲跌 | 外資近5日(億) |')
        L.append('|---|---|---|---|---|---|---|')
        for x in cands:
            L.append(f'| {x["code"]} | {x["name"]} | {x["sector"]} | {x["close"]:.2f} | '
                     f'{x["bias"]:+.1%} | {R.fp(x["chg5"])} | {R.fa(x["f5"])} |')
    else:
        L.append('當日無符合條件者。')
    L.append('')
    return L


def blind_spots():
    return [
        '## 八、盲點與限制',
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
    L += pullback_section(c, D)
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
