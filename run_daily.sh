#!/bin/bash
# FundFlo-replacement daily runner (cron-ready).
# 1) ETL the latest trading day from TWSE public data (+ market margin totals)
# 2) ETL macro data (US 10Y/2Y, ACM term premium, TWD — all T+1)
# 3) Generate the market overview (always) + sector reports only for signal sectors
#    Signal = 當日外資淨流入排名前 8，或 5 日動能由負翻正 (see overview.py CFG)
set -e
cd /home/hatch/workspace/fundflo
D=$(/usr/bin/python3 etl.py --latest 2>&1 | grep -oP 'latest trading day: \K\d+')
/usr/bin/python3 macro_etl.py --latest
mkdir -p reports/$D
/usr/bin/python3 overview.py --date $D --out reports/$D/OVERVIEW.html
for s in $(/usr/bin/python3 overview.py --date $D --signals); do
  /usr/bin/python3 report.py --sector $s --date $D --format html --out reports/$D/$s.html
done
echo "done $D"
