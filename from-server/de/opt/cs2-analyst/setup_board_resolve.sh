#!/bin/bash
cd /opt/cs2-analyst
python3 -m py_compile board_resolve.py && echo SYNTAX_OK
cat > /etc/systemd/system/cs2-board-resolve.service <<EOF
[Unit]
Description=Grade board value/anti-sweep picks vs bo3 (win-rate track)
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=/opt/cs2-analyst
ExecStart=/usr/bin/python3 /opt/cs2-analyst/board_resolve.py
EOF
cat > /etc/systemd/system/cs2-board-resolve.timer <<EOF
[Unit]
Description=Grade board picks every 40 min
[Timer]
OnBootSec=10min
OnUnitActiveSec=40min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now cs2-board-resolve.timer 2>&1 | tail -1
echo "=== log current board picks + run resolver ==="
timeout 60 python3 prematch_board.py 2>&1 | tail -2
python3 board_resolve.py
echo "=== board pick log so far ==="
wc -l data/prematch_board_log.jsonl 2>/dev/null || echo "no picks logged yet"
