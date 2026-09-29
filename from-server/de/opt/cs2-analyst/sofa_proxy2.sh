#!/bin/bash
echo "=== dataimpulse-fwd.service (masked) ==="
grep -iE "ExecStart|Environment|Description" /etc/systemd/system/dataimpulse-fwd.service | sed -E "s#:[^:@/ ]+@#:***@#g; s#[0-9]{8,}#***#g"
echo "=== listening local ports (forwarder) ==="
ss -ltnp 2>/dev/null | grep -iE "fwd|dataimpulse|gost|socat|redsocks|10000|1080|808[0-9]" | awk '{print $4, $6}' | head
# collect candidate local proxy ports
ports=$(ss -ltnp 2>/dev/null | grep -oE "127.0.0.1:[0-9]+" | grep -oE "[0-9]+$" | sort -u)
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
echo "=== пробую SofaScore через локальные прокси-порты ==="
for pt in $ports; do
  for scheme in http socks5h; do
    code=$(curl -s --max-time 15 -x "$scheme://127.0.0.1:$pt" -A "$UA" "https://api.sofascore.com/api/v1/sport/football/events/live" -o /tmp/s.json -w "%{http_code}" 2>/dev/null)
    if [ "$code" = "200" ]; then
      echo "✅ $scheme://127.0.0.1:$pt -> HTTP 200"
      python3 -c "import json;d=json.load(open('/tmp/s.json'));print('  events:',len(d.get('events',[])))" 2>/dev/null
    elif [ "$code" != "000" ] && [ -n "$code" ]; then
      echo "  $scheme://127.0.0.1:$pt -> HTTP $code"
    fi
  done
done
