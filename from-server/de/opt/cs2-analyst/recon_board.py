root = "/opt/cs2-analyst"
print("=== prematch_board.py lines 40-75 ===")
for i, l in enumerate(open(f"{root}/prematch_board.py").read().splitlines()[39:75], 40):
    print(i, l)
print("\n=== line_anomaly.py tail (__main__) ===")
src = open(f"{root}/line_anomaly.py").read().splitlines()
for i, l in enumerate(src):
    if "__main__" in l:
        for j in range(max(0, i-2), min(len(src), i+3)):
            print(j+1, src[j])
        break
print("\n=== web_ui.py board render (find 'Ближайшие матчи' block) ===")
w = open(f"{root}/web_ui.py").read().splitlines()
for i, l in enumerate(w):
    if "Ближайшие матчи" in l:
        for j in range(i, min(len(w), i+16)):
            print(j+1, w[j])
        break
