import urllib.request, re
h = urllib.request.urlopen("http://127.0.0.1:8920/football", timeout=10).read().decode()
print("edge panel present   :", "РЕАЛЬНЫЙ край" in h)
print("sweet-spots present  :", "край реально заходит" in h)
print("edge headline in card:", "лучший рынок (край)" in h)
print("DoubleChance gone    :", "двойной шанс" not in h)
print("traceback            :", "Traceback" in h, "| bytes:", len(h))
print("--- family edge ranking ---")
for m in re.findall(r"<span class=team>([^<]+)</span><b class=\w+>([^<]+)</b>", h)[:6]:
    print("  ", m[0], "->", m[1])
print("--- sweet spots ---")
for m in re.findall(r"<b>([^<]+)</b> · модель ([\d-]+)% · (\d+) матч", h)[:6]:
    print("  ", m[0], m[1]+"%", m[2]+"матч")
print("--- sample per-match edge pick ---")
for m in re.findall(r"лучший рынок \(край\)</span><b class=\"?\w+\"?>([^<]+)</b>", h)[:5]:
    print("  ", m)
