import json, os, re, glob
root = "/opt/cs2-analyst"
src = open(f"{root}/winline_odds.py").read()
print("=== winline_odds.py: URL / caps / scroll ===")
for ln in src.splitlines():
    if re.search(r"URL\s*=|\[:\d|limit|MAX|range\(|scrollIntoView|querySelectorAll|wait_for_timeout", ln):
        print("  ", ln.strip()[:100])
d = json.load(open(f"{root}/data/winline_odds.json"))
print("\ncurrent prematch matches:", len(d))
print("sample:", [f"{r['t1']} vs {r['t2']}" for r in d[:6]])
print("\n=== data files re odds/history ===")
for f in sorted(glob.glob(f"{root}/data/*odds*") + glob.glob(f"{root}/data/*hist*") + glob.glob(f"{root}/data/*snapshot*")):
    print("  ", os.path.basename(f), os.path.getsize(f), "bytes")
