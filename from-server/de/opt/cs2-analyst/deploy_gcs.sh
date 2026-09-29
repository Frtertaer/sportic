#!/bin/bash
cd /opt/cs2-analyst
python3 patch_resolvers_gcs.py
python3 -m py_compile gcs_lookup.py gcs_results.py line_steam_resolve.py board_resolve.py line_steam_backtest.py && echo SYNTAX_OK
# gcs results scraper timer (every 25 min)
cat > /etc/systemd/system/cs2-gcs-results.service <<EOF
[Unit]
Description=Scrape g-cs.ru finished CS2 results (full coverage)
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=/opt/cs2-analyst
ExecStart=/opt/kwork-monitor/.venv/bin/python /opt/cs2-analyst/gcs_results.py
EOF
cat > /etc/systemd/system/cs2-gcs-results.timer <<EOF
[Unit]
Description=Refresh g-cs.ru results every 25 min
[Timer]
OnBootSec=4min
OnUnitActiveSec=25min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now cs2-gcs-results.timer 2>&1 | tail -1
echo "=== HONEST STEAM BACKTEST (g-cs.ru full coverage) ==="
python3 line_steam_backtest.py 2>&1 | head -30
echo "=== board picks resolver (gcs) ==="
python3 board_resolve.py 2>&1 | tail -6
