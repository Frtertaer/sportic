#!/bin/bash
cd /opt/cs2-analyst
V=/opt/kwork-monitor/.venv/bin/python
$V sofa_live.py    >> /tmp/sofa.log 2>&1
$V sofa_signal.py  >> /tmp/sofa.log 2>&1
$V sofa_resolve.py >> /tmp/sofa.log 2>&1
