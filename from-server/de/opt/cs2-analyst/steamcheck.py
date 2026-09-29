import urllib.request, re
h = urllib.request.urlopen("http://127.0.0.1:8920/", timeout=10).read().decode()
print("steam section present :", "Движение линии winline" in h)
print("track line present    :", "следуй за стимом" in h or "трек копится" in h)
print("traceback             :", "Traceback" in h, "| bytes:", len(h))
seg = h.split("Движение линии")[1].split("Ставить сейчас")[0] if "Движение линии" in h else ""
print("--- steam card teams shown ---")
for m in re.findall(r"<span class=team>([^<]+)</span>", seg)[:6]:
    print("  ", m)
for m in re.findall(r"ставь ([^<]+)</b>", seg)[:6]:
    print("   bet:", m)
