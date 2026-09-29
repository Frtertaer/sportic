import json, pathlib
pb = pathlib.Path("/opt/cs2-analyst/prematch_board.py").read_text(encoding="utf-8")
wu = pathlib.Path("/opt/cs2-analyst/web_ui.py").read_text(encoding="utf-8")
print("patch applied (prematch_board _news_cached):", "_news_cached" in pb)
print("patch applied (web_ui newsb)              :", "newsb" in wu)
try:
    d = json.load(open("/opt/cs2-analyst/data/prematch_board.json"))
    print("board matches:", len(d.get("matches", [])))
    for r in d["matches"]:
        print(f"  {r['t1']} vs {r['t2']} | verdict {r['verdict']} | news: {(r.get('news') or '(none)')[:90]}")
except Exception as ex:
    print("board read err:", ex)
