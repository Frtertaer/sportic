#!/bin/bash
cd /opt/cs2-analyst
python3 -m py_compile line_anomaly.py line_steam_resolve.py && echo SYNTAX_OK
cat > /etc/systemd/system/cs2-line-resolve.service <<EOF
[Unit]
Description=Grade winline steam follows vs bo3 (win-rate track)
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=/opt/cs2-analyst
ExecStart=/usr/bin/python3 /opt/cs2-analyst/line_steam_resolve.py
EOF
cat > /etc/systemd/system/cs2-line-resolve.timer <<EOF
[Unit]
Description=Grade steam follows every 30 min
[Timer]
OnBootSec=8min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now cs2-line-resolve.timer 2>&1 | tail -1
echo "=== run resolver now (grade pending + show track) ==="
python3 line_steam_resolve.py
