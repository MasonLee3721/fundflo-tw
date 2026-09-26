#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate a FundFlo-style market observation report (Markdown) for one sector.

Structure mirrors the original screenshots:
  一句話 / 〇大盤底色 / 一、族群→子主題 / 二、個股表 /
  三、雙領頭形態 / 四、盲點 / 研究整理聲明

v1 notes:
  - 當日ETF = 0 for all stocks (no passive-flow attribution); the report does NOT
    make passive-flow claims.
  - 5日動能 = 近5日外資淨額合計 − 前5日外資淨額合計 (documented approximation).
  - 定位 labels are rule-based; sub-theme breakdown activates when basket.json
    stocks carry sub_theme values.

Usage:
  python3 report.py --sector SEC_08 [--date 20260924] [--out sample.md]
"""
import argparse
import compute
import html as _html
import re

DISCLAIMER = '以上為研究整理，非買賣建議'

HTML_CSS = """
body{font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;max-width:980px;margin:0 auto;padding:20px 16px 48px;background:#fafafa;color:#222;line-height:1.6}
h1{font-size:1.35rem;margin:0 0 4px}
.meta{color:#888;font-size:.8rem;margin:0 0 12px}
.lead{background:#fff;border-left:4px solid #c0392b;padding:10px 14px;margin:0 0 18px;border-radius:0 6px 6px 0;box-shadow:0 1px 2px rgba(0,0,0,.06)}
h2{font-size:1.05rem;margin:22px 0 8px;padding-bottom:4px;border-bottom:2px solid #e0e0e0}
.tbl{overflow-x:auto;margin:0 0 12px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:.85rem;box-shadow:0 1px 2px rgba(0,0,0,.05)}
th,td{border:1px solid #e2e2e2;padding:7px 10px;white-space:nowrap}
th{background:#f2f2f2;font-weight:700;text-align:center}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.pos{color:#c0392b;font-weight:600}
td.neg{color:#1e8449;font-weight:600}
ul{margin:6px 0;padding-left:1.4em;font-size:.88rem}
li{margin:4px 0}
p{font-size:.9rem}
.disclaimer{margin-top:26px;color:#999;font-size:.8rem;text-align:center}
@media print{body{background:#fff}.lead{box-shadow:none}}
"""


def fa(x):
    if x is None:
        return '—'
    return f'{x:+.2f}' if abs(x) >= 0.005 else '0'


def fp(x):
    if x is None:
        return '—'
    return f'{x:+.2f}%' if abs(x) >= 0.005 else '0.00%'


def classify(m):
    """雙領頭形態 classification from (roll5, mom5, chg5, f_day)."""
    r, mo, ch, f = m['roll5'], m['mom5'], m['chg5'], m['f_day']
    if r and r > 0 and mo and mo > 0 and ch and ch > 0:
        return '趨勢已成形', '錢已逐日累積、價格已跟到 ' + fp(ch)
    if f > 0 and r and abs(r - f) / max(abs(f), 1e-9) < 0.3 and mo and mo > r and (ch or 0) < 3:
        return '資金先行、價格滯後', '這一波的錢幾乎全來自最後一日，加上動能高於滾動 → 剛啟動，價格還沒反應'
    if f > 0 and r and r < 0:
        return '單日翻多', '前幾日偏賣、當日轉為買超，持續性待觀察'
    if r and r > 0:
        return '溫和流入', '滾動為正但動能或漲幅未跟上，趨勢待確認'
    if f and f < 0:
        return '當日調節', '當日外資淨流出'
    if f and f > 0:
        return '單日流入', '當日小幅淨流入，滾動基礎尚弱'
    return '無明顯流向', '當日與近期流量皆接近零'


def position_label(f_sum, fin_amt, chg):
    if f_sum > 0 and fin_amt < 0.3 * abs(f_sum):
        return '錢線·法人主導'
    if f_sum > 0 and fin_amt >= 0.3 * abs(f_sum):
        return '槓桿型'
    if f_sum < 0 and chg > 0:
        return '價漲錢賣'
    if f_sum < 0:
        return '錢出'
    return '平盤整理'


def build(sector_id, date=None):
    c = compute.con()
    basket = compute.load_basket()
    sector = next((s for s in basket['sectors'] if s['id'] == sector_id), None)
    if not sector:
        raise ValueError(f'unknown sector {sector_id}')
    D = date or compute.latest_date(c)
    sm = compute.sector_metrics(c, sector, D)
    mkt = compute.market_row(c, D)
    rank = compute.consecutive_rank(c, D, top=15)

    stocks = sorted(sm['stocks'], key=lambda m: -m['f_day'])
    pos = [m for m in stocks if m['f_day'] > 0.005]
    zero = [m for m in stocks if abs(m['f_day']) < 0.005]
    inflow = sum(m['f_day'] for m in pos)
    top2 = pos[:2]
    conc = sum(m['f_day'] for m in top2) / inflow if inflow > 0 and len(top2) == 2 else 0
    fin_amt = sum(m['fin_delta'] * (m['close'] or 0) / 1e5 for m in stocks)  # 張→億元

    L = []
    L.append(f'# {sector["name"]}｜市場觀察報告（FundFlo 替代版 v1）')
    L.append(f'大盤 as_of {D}｜連續買賣榜 as_of {D}｜來源：TWSE 公開資料 ETL（v1 未含 ETF 被動歸因）')
    L.append('')

    # 一句話
    if len(top2) == 2:
        c1, _ = classify(top2[0]); c2, _ = classify(top2[1])
        L.append(f'一句話：{sector["name"]}當日外資{"淨流入" if sm["f_sum"] > 0 else "淨流出"} {fa(sm["f_sum"])} 億，'
                 f'集中度{"極高" if conc > 0.7 else "偏高" if conc > 0.4 else "分散"}'
                 f'（前兩檔佔正流入 {conc:.0%}）；{top2[0]["code"]} {top2[0]["name"]}是{c1}，'
                 f'{top2[1]["code"]} {top2[1]["name"]}是{c2}。')
    elif len(top2) == 1:
        c1, _ = classify(top2[0])
        L.append(f'一句話：{sector["name"]}當日外資 {fa(sm["f_sum"])} 億，正流入僅 {top2[0]["code"]} {top2[0]["name"]}一檔（{c1}），其餘皆無明顯流入。')
    else:
        L.append(f'一句話：{sector["name"]}當日外資 {fa(sm["f_sum"])} 億，無正流入領頭股，族群無資金主線。')
    L.append('')

    # 〇 大盤底色
    L.append(f'## 〇、大盤底色（{D}）')
    L.append('| 項目 | 數值 | 讀法 |')
    L.append('|---|---|---|')
    if mkt:
        L.append(f'| 外資淨額 | {fa(mkt["f_net"])} 億 | {"整體偏賣" if mkt["f_net"] < 0 else "整體偏買"} |')
        L.append(f'| 投信淨額 | {fa(mkt["t_net"])} 億 | {"偏賣" if mkt["t_net"] < 0 else "偏買"} |')
        L.append(f'| 自營淨額 | {fa(mkt["d_net"])} 億 | {"小幅偏買" if mkt["d_net"] > 0 else "偏賣"} |')
        L.append(f'| 總成交｜漲家｜跌家 | {mkt["total_turnover"]:,.2f} 億｜{mkt["up_count"]}｜{mkt["down_count"]} | '
                 f'{"跌多漲少" if mkt["down_count"] > mkt["up_count"] else "漲多跌少"} |')
        base = '「下挫＋外資淨賣」，因此各族群個股的正流入是「挑結構」的結果，這是判讀本族群的基準。' \
            if mkt['f_net'] < 0 else '「外資偏買」的大盤底色，族群正流入有順風。'
        L.append(f'底色＝{base}')
    L.append('')

    # 一、族群→子主題
    L.append('## 一、族群 → 子主題：錢落在哪')
    L.append('| 子主題 | 家數 | 成交(億) | 漲跌 | 外資 | 融資Δ(張) | 定位 |')
    L.append('|---|---|---|---|---|---|---|')
    subs = {}
    for m in stocks:
        subs.setdefault(m.get('sub_theme') or sector['name'], []).append(m)
    for name, ms in subs.items():
        n = len(ms)
        turn = 0.0
        for m in ms:
            r = c.execute('SELECT turnover FROM inst_flow WHERE date=? AND stock=?', (D, m['code'])).fetchone()
            turn += (r['turnover'] or 0) / 1e8 if r else 0
        fs = sum(m['f_day'] for m in ms)
        fd = sum(m['fin_delta'] for m in ms)
        fa_ = sum(m['fin_delta'] * (m['close'] or 0) / 1e5 for m in ms)
        # daily pct (equal-weight) for the 漲跌 column
        pcts = []
        for m in ms:
            r = c.execute('SELECT spread, close FROM inst_flow WHERE date=? AND stock=?', (D, m['code'])).fetchone()
            if r and r['close'] and r['spread'] is not None and (r['close'] - r['spread']) > 0:
                pcts.append(r['spread'] / (r['close'] - r['spread']) * 100)
        chgd = sum(pcts) / len(pcts) if pcts else 0.0
        L.append(f'| {name} | {n} | {turn:.2f} | {fp(chgd)} | {fa(fs)} | {fd:+.0f} | {position_label(fs, fa_, chgd)} |')
    if len(subs) > 1:
        L.append(f'| **族群合計** | **{sm["n"]}** | **{sm["turnover"]:.2f}** | **{fp(sm["chg"])}** | '
                 f'**{fa(sm["f_sum"])}** | **{sm["fin_sum"]:+.0f}** | — |')
    L.append('')

    # 二、個股表
    L.append('## 二、個股內部：錢的集中度')
    L.append('| 代號 | 名稱 | 當日外資 | 當日ETF | 當日複合 | 5日滾動 | 5日動能 | 5日漲幅 |')
    L.append('|---|---|---|---|---|---|---|---|')
    for m in stocks:
        L.append(f'| {m["code"]} | {m["name"]} | {fa(m["f_day"])} | {fa(m["etf_day"])} | {fa(m["combo"])} | '
                 f'{fa(m["roll5"])} | {fa(m["mom5"])} | {fp(m["chg5"])} |')
    if inflow > 0 and len(top2) == 2:
        rest = inflow - sum(m['f_day'] for m in top2)
        L.append(f'在列的 {len(pos)} 檔合計正流入 {inflow:.2f} 億；其餘 {sm["n"] - len(pos)} 檔中 {len(zero)} 檔當日零流量'
                 f'（{", ".join(m["name"] for m in zero[:8])}{"…" if len(zero) > 8 else ""}）。')
    L.append('')

    # 三、雙領頭
    L.append('## 三、兩個領頭是兩種形態')
    if len(top2) == 2:
        L.append('|  | ' + ' | '.join(f'{m["code"]} {m["name"]}' for m in top2) + ' |')
        L.append('|---|---|---|')
        L.append('| 滾動 vs 動能 | ' + ' | '.join(f'{fa(m["roll5"])} ≈ {fa(m["mom5"])}' for m in top2) + ' |')
        L.append('| 5日漲幅 | ' + ' | '.join(fp(m['chg5']) for m in top2) + ' |')
        L.append('| 形態 | ' + ' | '.join(f'{classify(m)[0]}：{classify(m)[1]}' for m in top2) + ' |')
        L.append(f'要看「還沒走完的」→ {top2[1]["code"]}；要看「已確認的」→ {top2[0]["code"]}。')
    elif len(top2) == 1:
        cn, desc = classify(top2[0])
        L.append(f'僅一檔正流入：{top2[0]["code"]} {top2[0]["name"]}（{cn}：{desc}）。')
    else:
        L.append('當日無正流入領頭股，本節從缺。')
    L.append('')

    # 四、盲點
    L.append('## 四、盲點（尚未構成主線的證據）')
    bullets = []
    if rank:
        thresh = rank[-1]['streak']
        in_top = [m for m in stocks if any(r['code'] == m['code'] and r['streak'] >= thresh for r in rank)]
        if not in_top:
            topnames = '、'.join(f'{r["code"]}×{r["streak"]}' for r in rank[:3])
            bullets.append(f'榜單缺席：連續買賣 Top 榜（{D}）本族群零檔入選（上榜的是 {topnames}…）→ 連買天數還不及榜上水準，穩定性尚未累積。')
    if top2:
        bullets.append('v1 未做 ETF 被動歸因：當日ETF 欄全為 0，報告不做「主動 vs 被動」判讀；被動底倉有無需待 v2。')
    if sm['f_sum'] > 0 and sm['t_sum'] <= 0:
        bullets.append(f'投信未跟：投信 {fa(sm["t_sum"])} 億，買方以外資為主，不是四類齊發。')
    if sm['d_sum'] != 0 and abs(sm['d_sum']) > 0.005:
        bullets.append(f'自營 {fa(sm["d_sum"])} 億，為次要買盤/賣盤。')
    if fin_amt > 0.3 * abs(sm['f_sum']) and sm['f_sum'] > 0:
        bullets.append(f'槓桿示警：族群融資Δ {sm["fin_sum"]:+.0f} 張（約 {fin_amt:.1f} 億），相對於外資 {fa(sm["f_sum"])} 億比重偏高，屬槓桿推動型，對回檔敏感。')
    elif sm['f_sum'] > 0 and fin_amt >= 0:
        bullets.append(f'槓桿分層：融資Δ {sm["fin_sum"]:+.0f} 張（約 {fin_amt:.1f} 億），相對於外資 {fa(sm["f_sum"])} 億比重低 → 法人主導、非槓桿推動。')
    if zero:
        bullets.append(f'尾端無效：{len(zero)} 檔當日零流量，擴散尚未發生。')
    L.append('\n'.join(f'- {b}' for b in bullets) if bullets else '- （無）')
    L.append('')
    L.append(DISCLAIMER)
    c.close()
    return '\n'.join(L) + '\n'


def _inline(t):
    t = _html.escape(t)
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)


def _cell_cls(c):
    c = c.strip()
    if re.match(r'^[+＋]', c):
        return 'num pos'
    if re.match(r'^[-−]', c):
        return 'num neg'
    if re.match(r'^[\d（(—]', c):
        return 'num'
    return ''


def md_to_html(md, title):
    """Minimal Markdown→HTML for the report's subset: h1/h2/paragraphs/tables/bullets/bold."""
    lines = md.split('\n')
    blocks = []
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith('# '):
            blocks.append(('h1', s[2:])); i += 1
        elif s.startswith('## '):
            blocks.append(('h2', s[3:])); i += 1
        elif s.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append(lines[i].strip()); i += 1
            rows = [r for r in rows if not re.match(r'^\|[\s:\-|]+\|$', r)]
            blocks.append(('table', rows))
        elif s.startswith('- '):
            items = []
            while i < len(lines) and lines[i].strip().startswith('- '):
                items.append(lines[i].strip()[2:]); i += 1
            blocks.append(('ul', items))
        else:
            para = [s]; i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(('#', '|', '- ')):
                para.append(lines[i].strip()); i += 1
            blocks.append(('p', ' '.join(para)))

    parts = []
    for kind, val in blocks:
        if kind == 'h1':
            parts.append(f'<h1>{_inline(val)}</h1>')
        elif kind == 'h2':
            parts.append(f'<h2>{_inline(val)}</h2>')
        elif kind == 'table':
            head = [c.strip() for c in val[0].strip().strip('|').split('|')]
            thead = ''.join(f'<th>{_inline(c)}</th>' for c in head)
            body = []
            for r in val[1:]:
                cells = [c.strip() for c in r.strip().strip('|').split('|')]
                tds = ''.join(f'<td class="{_cell_cls(c)}">{_inline(c)}</td>' for c in cells)
                body.append(f'<tr>{tds}</tr>')
            parts.append('<div class="tbl"><table><thead><tr>' + thead +
                         '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>')
        elif kind == 'ul':
            parts.append('<ul>' + ''.join(f'<li>{_inline(x)}</li>' for x in val) + '</ul>')
        else:
            if val.startswith('一句話'):
                parts.append(f'<div class="lead">{_inline(val)}</div>')
            elif 'as_of' in val:
                parts.append(f'<p class="meta">{_inline(val)}</p>')
            elif val == DISCLAIMER:
                parts.append(f'<p class="disclaimer">{_inline(val)}</p>')
            else:
                parts.append(f'<p>{_inline(val)}</p>')

    return ('<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_html.escape(title)}</title><style>{HTML_CSS}</style></head>'
            '<body>' + '\n'.join(parts) + '</body></html>')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sector', required=True)
    ap.add_argument('--date')
    ap.add_argument('--out')
    ap.add_argument('--format', choices=['md', 'html'], default='md')
    a = ap.parse_args()
    md = build(a.sector, a.date)
    if a.format == 'html':
        basket = compute.load_basket()
        sector = next((s for s in basket['sectors'] if s['id'] == a.sector), {})
        title = f'{sector.get("name", a.sector)}｜市場觀察報告'
        doc = md_to_html(md, title)
        out = a.out or f'{compute.BASE}/report_{a.sector}_{a.date or compute.latest_date(compute.con())}.html'
    else:
        doc = md
        out = a.out or f'{compute.BASE}/report_{a.sector}_{a.date or compute.latest_date(compute.con())}.md'
    open(out, 'w', encoding='utf-8').write(doc)
    print('wrote', out)
