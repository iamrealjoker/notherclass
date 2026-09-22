#!/usr/bin/env bash
# Consolidación diaria bruto→neto del agente (para cronjob, estilo docker a las 3am).
# Añade a crontab (crontab -e):
#   0 3 * * * /bin/bash /root/Desktop/web_analisis/funcionando/notherclass/agent/consolidate_daily.sh
cd /root/Desktop/web_analisis/funcionando/notherclass/agent || exit 1
echo "[consolidate_daily] $(date '+%Y-%m-%d %H:%M:%S') — inicio" >> logs/consolidate_daily.log
python3 run.py --consolidate >> logs/consolidate_daily.log 2>&1
echo "[consolidate_daily] $(date '+%Y-%m-%d %H:%M:%S') — fin" >> logs/consolidate_daily.log
