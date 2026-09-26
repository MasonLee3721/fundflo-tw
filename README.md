# FundFlo Replacement — Daily Fund-Flow ETL + Report Pipeline

Replicates the user's FundFlo 市場觀察報告 from **public data only** (TWSE/TPEx + FRED/NY Fed/Yahoo).
Basket: **38 sectors × 399 entries / 357 unique stocks**, in `basket.json`
(source of truth for editing = the Google Drive Excel `台股38族群代表股_20260926.xlsx`;
re-snapshot after edits).

## Daily output (HTML only, no Markdown)
1. `reports/YYYYMMDD/OVERVIEW.html` — 大盤總覽：國際總經（美 10Y、ACM 期限溢價、10Y-2Y、台幣）
   → 台股大盤（三大法人、成交、漲跌家數、融資餘額變化）→ 38 族群資金排序
   → 競爭者觀察 → 未來上漲候選 → 盲點。
2. `reports/YYYYMMDD/SEC_*.html` — 只有「有訊號」族群才產完整報告：
   當日外資淨流入排名前 8，或 5 日動能由負翻正。

## Files

| File | Purpose |
|---|---|
| `etl.py` | Daily TWSE pull → SQLite. `--date YYYYMMDD` / `--latest` / `--backfill N` |
| `macro_etl.py` | Daily macro pull (US 10Y/2Y, ACM term premium, TWD) → `macro_daily`. All T+1 |
| `compute.py` | Metric library (per-stock 5-day windows, sector aggregates, 連買榜 ranking) |
| `overview.py` | Market overview generator. `--date D --out path`; `--signals` lists signal sectors |
| `report.py` | FundFlo-format HTML report for one sector: `--sector SEC_08 --date D --format html` |
| `repair_otc.py` | One-time: estimate OTC historical amounts with latest close (after backfill) |
| `run_daily.sh` | Cron-ready: 台股 ETL → 總經 ETL → OVERVIEW → 有訊號族群報告，進 `reports/YYYYMMDD/` |
| `basket.json` | Canonical sector→stock mapping (`sub_theme` reserved, currently null) |
| `fundflo.db` | SQLite storage (30 trading days: 20260814–20260924) |

## Data sources (all verified reachable from this VM)

| Data | Endpoint | Notes |
|---|---|---|
| 外資/投信/自營 per-stock | `rwd/zh/fund/TWT38U` / `TWT44U` / `TWT43U` (上市); TPEx `3insti/daily_trade/3itrade_hedge_result.php` (上櫃, g2=外資合計/g3=投信/g6=自營合計) | ~1/3 of basket stocks are OTC; TPEx overlay fills them, else they would read zero |
| Quotes | TWSE `MI_INDEX?type=ALLBUT0999`; TPEx `aftertrading/daily_close_quotes/stk_quote_result.php` | 收盤價 / 成交金額(元) / 漲跌價差, all stocks in one call each |
| 融資餘額 | TWSE `MI_MARGN?selectType=STOCK`; TPEx `margin_trading/margin_balance/margin_bal_result.php` | unit **張**; 上市「合計」＋上櫃逐檔加總 → 上市＋上櫃融資餘額總量 |
| 大盤底色 | MI_INDEX 大盤統計 `總計(1~15)`; 漲跌證券數合計; `fund/BFI82U?dayDate=..&type=day` | 注意：rwd 版 BFI82U 會忽略 date 參數，必須用 legacy 版 |
| 美 10Y/2Y | FRED `fredgraph.csv?id=DGS10/DGS2`（首選）；備援 Yahoo `^TNX` | FRED 自 2026-09-26 起對本環境不穩，自動備援；2Y 缺失時利差顯示 — |
| 10Y 期限溢價 | NY Fed `ACMTermPremium.xls`（ACM Daily / ACMTP10；DATE 欄為字串，需 xlrd） | 心理面指標 |
| 台幣 | Yahoo `USDTWD=X` | 參考匯率，非央行收盤價 |

**Verified 2026-09-24 against the original report**: 外資 −329.65億 / 投信 −128.23億 /
自營 +13.38億 / 總成交 7,755.91億 / 漲家 386 / 跌家 546 — all match exactly.

## DB schema (`fundflo.db`)

- `inst_flow(date, stock, f_buy,f_sell,f_net, t_buy,t_sell,t_net, d_buy,d_sell,d_net, close, turnover, spread, f_amt,t_amt,d_amt)`
  share counts in 股； amounts in **億元** = 買賣超股數 × 收盤價 / 1e8. f=外資合計, t=投信, d=自營商合計.
- `margin(date, stock, fin_prev, fin_today, fin_delta)` — 融資餘額 in **張**.
- `market_daily(date, f_net,t_net,d_net, total_turnover, up_count, down_count, fin_tot, fin_tot_prev)`
  amounts in 億元； fin_tot = 上市＋上櫃融資餘額合計（張）.
- `macro_daily(date, dgs10, dgs2, acm_tp10, twd)` — 總經日序列（T+1）.

## Metric definitions

- **當日外資（億）** = 當日外資買賣超金額；**當日ETF = 0** (v1)；**當日複合** = 外資＋ETF.
- **5日滾動** = 近 5 交易日外資淨額合計；**5日動能** = 近 5 日合計 − 前 5 日合計（v1 近似）.
- **5日漲幅%** = 當日收盤 ÷ 5 交易日前收盤 − 1；**連買天數** = 外資連續淨買超天數.
- **有訊號族群** = 當日外資淨流入前 8 或 5 日動能由負翻正.
- **上漲候選** = 5 日動能由負翻正 ＋ 連買 ≥3 天 ＋ 收盤站上 5 日線.

## Known limitations

- OTC 歷史 close 為 NULL（TPEx 行情端點忽略日期參數），歷史金額用最新收盤估算（`repair_otc.py`），5日漲幅顯示 —；每日 `--latest` 為精確值.
- 當日ETF 欄全 0：不做主被動判讀（v2 規劃見 skill references/limitations.md）.
- 總經皆 T+1；台幣為 Yahoo 參考匯率.
- 2809 京城銀、2888 新光金已無行情（疑似下市），待使用者以盤感決定是否剔除.

Cron needs explicit user approval before installing:
`30 15 * * 1-5 <dir>/run_daily.sh >> <dir>/cron.log 2>&1` (Asia/Taipei, weekdays).
