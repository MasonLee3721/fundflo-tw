#!/bin/bash
# FundFlo-replacement daily runner (cron-ready).
# 1) Skip silently if TWSE is closed today (weekends/holidays)
# 2) ETL the latest trading day from TWSE public data (+ market margin totals)
# 3) ETL macro data (US 10Y/2Y, ACM term premium, TWD — all T+1)
# 4) Generate the market overview (always) + sector reports only for signal sectors
#    Signal = 當日外資淨流入排名前 8，或 5 日動能由負翻正 (see overview.py CFG)
# 5) Push the new reports to GitHub (accumulates under reports/<yyyymmdd>/)
set -e
cd /home/hatch/workspace/fundflo
TODAY=$(date +%Y%m%d)
if ! /usr/bin/python3 -c "
import sys; from etl import is_trading_day
sys.exit(0 if is_trading_day('$TODAY') else 1)"; then
  echo "skip $TODAY (TWSE closed)"
  exit 0
fi
D=$(/usr/bin/python3 etl.py --latest 2>&1 | grep -oP 'latest trading day: \K\d+')
/usr/bin/python3 macro_etl.py --latest
/usr/bin/python3 struct_etl.py --backfill --days 5
/usr/bin/python3 breadth_etl.py --update
mkdir -p reports/$D
/usr/bin/python3 overview.py --date $D --out reports/$D/OVERVIEW.html
for s in $(/usr/bin/python3 overview.py --date $D --signals); do
  /usr/bin/python3 report.py --sector $s --date $D --format html --out reports/$D/$s.html
done
/usr/bin/python3 /home/hatch/workspace/skills/github/bin/gh_api.py push MasonLee3721/fundflo-tw reports/$D --message "daily $D"
echo "done $D"
