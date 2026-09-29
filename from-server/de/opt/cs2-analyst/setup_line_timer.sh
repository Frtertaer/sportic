#!/bin/bash
cat > /etc/systemd/system/cs2-line-anomaly.service <<EOF
[Unit]
Description=CS2 winline line-movement anomaly (steam/договорняк) scanner
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=/opt/cs2-analyst
ExecStart=/usr/bin/python3 /opt/cs2-analyst/line_anomaly.py --live
EOF
cat > /etc/systemd/system/cs2-line-anomaly.timer <<EOF
[Unit]
Description=Scan winline line movement every 12 min
[Timer]
OnBootSec=5min
OnUnitActiveSec=12min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now cs2-line-anomaly.timer 2>&1 | tail -1
echo "=== fresh steam just logged ==="
tail -n 3 /opt/cs2-analyst/data/line_steam_log.jsonl 2>/dev/null
echo "=== timer ==="
systemctl list-timers | grep line-anomaly
