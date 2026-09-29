#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/cs2-analyst

printf '%s\n' \
  'ac51ca1d1db7d5911de4e9dfebec4741574b061f81f2ed3cb2db5d742d927de8  web.py' \
  'b2d05773ba886e8a3181c50fa3fec768fe6a187a1644b2e1c06dd2e761a7cdee  web_ui.py' \
  '95bb02c7c05524b0d6db700bc6b9d0402943bb85a6f61b23c9e22a8f2b6b97e5  .web_ui.candidate.py' \
  | sha256sum -c -

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="web_ui.py.bak.${stamp}"
cp -a web_ui.py "$backup"

restore() {
  set +e
  cp -a "$backup" .web_ui.rollback.py
  mv -Tf .web_ui.rollback.py web_ui.py
  systemctl restart cs2-web.service
  systemctl is-active cs2-web.service
}

smoke() {
  systemctl is-active --quiet cs2-web.service || return 1
  curl -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 30 http://127.0.0.1:8920/api \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert {"live","football","ts"} <= d.keys()' \
    || return 1
  curl -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 30 http://127.0.0.1:8920/ \
    | python3 -c 'import sys; s=sys.stdin.read(); assert "id=signals" in s and "</html>" in s and "grok:render" not in s.lower()' \
    || return 1
  curl -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 30 http://127.0.0.1:8920/football \
    | python3 -c 'import sys; s=sys.stdin.read(); assert "<main id=main>" in s and "aria-current=page" in s and "</html>" in s' \
    || return 1
}

mv -Tf .web_ui.candidate.py web_ui.py
if ! systemctl restart cs2-web.service || ! smoke; then
  echo 'Deployment smoke failed; restoring previous renderer.' >&2
  restore
  exit 1
fi

printf 'BACKUP=%s\n' "$backup"
printf 'DEPLOYED_SHA=%s\n' "$(sha256sum web_ui.py | awk '{print $1}')"
journalctl -u cs2-web.service --since '-2 minutes' --no-pager -n 80
