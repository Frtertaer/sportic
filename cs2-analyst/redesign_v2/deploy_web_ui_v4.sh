#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/cs2-analyst

current_web_sha='ac51ca1d1db7d5911de4e9dfebec4741574b061f81f2ed3cb2db5d742d927de8'
current_ui_sha='17efeaf2aa20b06680b68f225934761da14654decd763d41a82d58113693b46c'
candidate_sha='11a31837f57ed71fa2477aa4c004bbb663ea4520e206bf2927d0d227515d21b2'
unit_sha='4f4e405b298f08c52330af404b2480915b714d53834d05d3d5d715eb7ee83a90'

exec 9>/run/lock/cs2-web-deploy.lock
flock -n 9 || { echo 'Another cs2-web deployment is active.' >&2; exit 1; }

printf '%s\n' \
  "$current_web_sha  web.py" \
  "$current_ui_sha  web_ui.py" \
  "$candidate_sha  .web_ui.v4.candidate.py" \
  "$unit_sha  /etc/systemd/system/cs2-web.service" \
  | sha256sum -c -

systemctl is-active --quiet cs2-web.service
systemctl is-active --quiet rtunnel-cs2web.service
systemctl is-active --quiet nginx.service
[[ "$(systemctl show cs2-web.service -p WorkingDirectory --value)" == '/opt/cs2-analyst' ]]
systemctl show cs2-web.service -p ExecStart --value | grep -Fq '/usr/bin/python3 -u web.py'
PYTHONDONTWRITEBYTECODE=1 python3 -B /tmp/remote_smoke_v4.py .web_ui.v4.candidate.py

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="web_ui.py.bak.${stamp}.${current_ui_sha:0:12}"
test ! -e "$backup"
cp -a web_ui.py "$backup"
printf '%s  %s\n' "$current_ui_sha" "$backup" | sha256sum -c -

old_pid="$(systemctl show cs2-web.service -p MainPID --value)"
deploy_start="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
rollback_armed=0

restore() {
  set +e
  install -o root -g root -m 0644 "$backup" .web_ui.rollback.py
  printf '%s  %s\n' "$current_ui_sha" .web_ui.rollback.py | sha256sum -c - || return 1
  mv -Tf .web_ui.rollback.py web_ui.py
  systemctl restart cs2-web.service || return 1
  smoke_generic || return 1
  printf '%s  %s\n' "$current_ui_sha" web_ui.py | sha256sum -c - || return 1
}

on_failure() {
  rc=$?
  trap - ERR INT TERM
  if (( rollback_armed )); then
    echo 'Deployment failed; restoring previous renderer.' >&2
    if ! restore; then
      echo "CRITICAL: rollback validation failed; backup remains at $backup" >&2
      exit 2
    fi
  fi
  exit "$rc"
}

smoke_generic() {
  systemctl is-active --quiet cs2-web.service || return 1
  curl -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 30 http://127.0.0.1:8920/api \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert {"live","football","ts"} <= d.keys()' \
    || return 1
  curl -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 30 http://127.0.0.1:8920/ \
    | python3 -c 'import sys; s=sys.stdin.read(); assert "id=signals" in s and "</html>" in s; assert "grok:render" not in s.lower()' \
    || return 1
  curl -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 30 http://127.0.0.1:8920/football \
    | python3 -c 'import sys; s=sys.stdin.read(); assert "<main id=main>" in s and "aria-current=page" in s and "</html>" in s' \
    || return 1
}

smoke_v4() {
  smoke_generic || return 1
  curl -fsS --max-time 30 http://127.0.0.1:8920/ \
    | python3 -c 'import sys; s=sys.stdin.read(); assert "--brand:#e8864a" in s and "content=\"#0b1724\"" in s; assert "atlantic broadcast v4" not in s.lower()' \
    || return 1
  curl -fsS --max-time 30 http://127.0.0.1:8920/football \
    | python3 -c 'import sys; s=sys.stdin.read(); assert "--bg:#0b1724" in s and "content=\"#0b1724\"" in s; assert "football atlantic v4" not in s.lower()' \
    || return 1
}

trap on_failure ERR INT TERM
install -o root -g root -m 0644 .web_ui.v4.candidate.py .web_ui.next.py
printf '%s  %s\n' "$candidate_sha" .web_ui.next.py | sha256sum -c -
rollback_armed=1
mv -Tf .web_ui.next.py web_ui.py
printf '%s  %s\n' "$candidate_sha" web_ui.py | sha256sum -c -
systemctl restart cs2-web.service
sleep 3
new_pid="$(systemctl show cs2-web.service -p MainPID --value)"
[[ "$new_pid" =~ ^[1-9][0-9]*$ && "$new_pid" != "$old_pid" ]]
smoke_v4
sleep 2
[[ "$(systemctl show cs2-web.service -p MainPID --value)" == "$new_pid" ]]
if journalctl -u cs2-web.service --since "$deploy_start" --no-pager \
  | grep -Eiq 'Traceback|SyntaxError|ImportError|ModuleNotFoundError|Address already in use'; then
  echo 'Failure signature found in cs2-web journal.' >&2
  false
fi
systemctl is-active --quiet rtunnel-cs2web.service
systemctl is-active --quiet nginx.service

rollback_armed=0
trap - ERR INT TERM

printf 'BACKUP=%s\n' "$backup"
printf 'DEPLOYED_SHA=%s\n' "$(sha256sum web_ui.py | awk '{print $1}')"
printf 'OLD_PID=%s\nNEW_PID=%s\n' "$old_pid" "$new_pid"
systemctl show cs2-web.service -p ActiveState -p SubState -p MainPID --no-pager
journalctl -u cs2-web.service --since '-2 minutes' --no-pager -n 80
