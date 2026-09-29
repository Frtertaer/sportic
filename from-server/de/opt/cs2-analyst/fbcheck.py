import urllib.request, re
h = urllib.request.urlopen("http://127.0.0.1:8920/football", timeout=10).read().decode()
print("markets panel present :", "Маркеты по винрейту" in h)
print("DC headline in cards  :", "двойной шанс" in h)
print("DNB present           :", "DNB" in h)
print("Over2.5 still headline:", "тотал 2.5" in h)
print("traceback             :", "Traceback" in h, "| bytes:", len(h))
print("--- market ranking ---")
for m in re.findall(r"<b>([^<]+)</b> · (\d+) матчей", h):
    print("  ", m[0], m[1], "матчей")
print("--- sample DC pick in a card ---")
for m in re.findall(r"двойной шанс</span><b class=pos>([^<]+)</b>", h)[:4]:
    print("  ", m)
