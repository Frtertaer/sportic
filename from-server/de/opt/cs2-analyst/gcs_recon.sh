#!/bin/bash
for u in "https://g-cs.ru/results" "https://g-cs.ru/matches" "https://g-cs.ru/"; do
  echo "=== $u ==="
  curl -sL -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" --max-time 25 "$u" -o /tmp/g.html \
    -w "final=%{url_effective} HTTP=%{http_code} size=%{size_download}\n"
  echo "-- structure hints --"
  grep -oE "__NEXT_DATA__|__NUXT__|application/json|/api/[a-z0-9/_.?=-]+|buildId|window.__" /tmp/g.html | sort | uniq -c | sort -rn | head -8
  echo "-- score-ish patterns --"
  grep -oE "[0-3][[:space:]]*:[[:space:]]*[0-3]" /tmp/g.html | head -4
  echo "-- title --"
  grep -oE "<title>[^<]+" /tmp/g.html | head -1
done
echo "=== if NEXT_DATA present, dump a slice ==="
curl -sL -A "Mozilla/5.0" --max-time 25 "https://g-cs.ru/results" -o /tmp/g.html
python3 - <<'PY'
import re, json
h = open("/tmp/g.html", encoding="utf-8", errors="replace").read()
m = re.search(r'id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>', h, re.S)
if m:
    print("NEXT_DATA found, len", len(m.group(1)))
    try:
        d = json.loads(m.group(1))
        def walk(o, p="", d=0):
            if d > 6: return
            if isinstance(o, dict):
                for k in list(o)[:12]:
                    if re.search(r'match|result|team|score|winner|game', k, re.I):
                        print("  key:", p + "/" + k, "->", type(o[k]).__name__)
                    walk(o[k], p + "/" + k, d + 1)
            elif isinstance(o, list) and o:
                walk(o[0], p + "[0]", d + 1)
        walk(d)
    except Exception as e:
        print("json parse fail:", e)
else:
    print("no __NEXT_DATA__; site likely SSR-HTML or client-rendered")
    print("body sample:", re.sub(r"\s+", " ", h)[:400])
PY
