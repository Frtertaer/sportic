#!/bin/bash
cd /opt/cs2-analyst
python3 -m py_compile sofa_signal.py sofa_resolve.py && echo SYNTAX_OK || exit 1
cat > /opt/cs2-analyst/sofa_cycle.sh <<'EOF'
#!/bin/bash
cd /opt/cs2-analyst
V=/opt/kwork-monitor/.venv/bin/python
$V sofa_live.py    >> /tmp/sofa.log 2>&1
$V sofa_signal.py  >> /tmp/sofa.log 2>&1
$V sofa_resolve.py >> /tmp/sofa.log 2>&1
EOF
chmod +x /opt/cs2-analyst/sofa_cycle.sh
cat > /etc/systemd/system/cs2-sofa.service <<EOF
[Unit]
Description=SofaScore live-goals cycle (scrape->signal->resolve)
After=network-online.target
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/cs2-analyst/sofa_cycle.sh
EOF
cat > /etc/systemd/system/cs2-sofa.timer <<EOF
[Unit]
Description=SofaScore live-goals every 150s
[Timer]
OnBootSec=2min
OnUnitActiveSec=150
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now cs2-sofa.timer 2>&1 | tail -1
echo "=== тест цикла сейчас (браузер+прокси, ~1-2 мин) ==="
timeout 175 /bin/bash /opt/cs2-analyst/sofa_cycle.sh
echo "--- лог ---"
tail -14 /tmp/sofa.log
