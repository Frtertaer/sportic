#!/bin/bash
svc=$(grep -rli dataimpulse /etc/systemd/system/*.service 2>/dev/null | head -1)
echo "service: $(basename "$svc")"
purl=$(grep -hoiE "https?://[^ \"']+@[^ \"']+:[0-9]+" "$svc" 2>/dev/null | head -1)
[ -z "$purl" ] && purl=$(grep -hoiE "https?://[^ \"']*(dataimpulse|10000)[^ \"']*" "$svc" 2>/dev/null | head -1)
echo "proxy (masked): $(echo "$purl" | sed -E 's#//[^@]*@#//***:***@#')"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
echo "--- SofaScore напрямую через резидентный прокси ---"
curl -s --max-time 35 -x "$purl" -A "$UA" -H "Accept: application/json" \
  "https://api.sofascore.com/api/v1/sport/football/events/live" -o /tmp/sofa.json \
  -w "HTTP %{http_code} | size %{size_download}\n"
echo "body head:"; head -c 260 /tmp/sofa.json; echo
python3 -c "import json;d=json.load(open('/tmp/sofa.json'));ev=d.get('events',[]);print('events:',len(ev), '| inprogress:', sum(1 for e in ev if e.get('status',{}).get('type')=='inprogress'))" 2>/dev/null || echo "(не JSON с events)"
