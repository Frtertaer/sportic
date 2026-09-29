#!/usr/bin/env python3
"""Task 1: grok+gemini news on board VALUE picks + strong anti-sweeps (roster/stand-in/scandal check),
cached 3h so we don't hammer the AIs every 20-min board rebuild."""
import pathlib

pb = pathlib.Path("/opt/cs2-analyst/prematch_board.py")
t = pb.read_text(encoding="utf-8")

# imports: add time
t = t.replace("import json, sys\n", "import json, sys, time\n", 1)

# news-cache helper after OUT definition
t = t.replace(
    'OUT = ROOT / "data" / "prematch_board.json"\n',
    'OUT = ROOT / "data" / "prematch_board.json"\n'
    'NEWS_CACHE = ROOT / "data" / "prematch_news_cache.json"\n\n\n'
    'def _news_cached(n1, n2) -> str:\n'
    '    """grok+gemini news for a match, cached 3h (value picks are few; avoids re-hitting the AIs)."""\n'
    '    key = f"{la._norm(n1)}|{la._norm(n2)}"\n'
    '    try:\n'
    '        cache = json.loads(NEWS_CACHE.read_text(encoding="utf-8"))\n'
    '    except Exception:\n'
    '        cache = {}\n'
    '    e = cache.get(key)\n'
    '    if e and time.time() - e.get("ts", 0) < 10800:\n'
    '        return e.get("news", "")\n'
    '    try:\n'
    '        news = la._news(n1, n2)\n'
    '    except Exception:\n'
    '        news = ""\n'
    '    cache[key] = {"news": news, "ts": time.time()}\n'
    '    try:\n'
    '        NEWS_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")\n'
    '    except Exception:\n'
    '        pass\n'
    '    return news\n', 1)

# fetch news for value / strong anti-sweep, before board.append
t = t.replace(
    "        board.append(rec)\n",
    '        rec["news"] = ""\n'
    '        if rec["verdict"] == "VALUE" or (rec.get("antisweep") or ["", 0])[1] >= 0.78:\n'
    '            rec["news"] = _news_cached(n1, n2)\n'
    "        board.append(rec)\n", 1)
assert "_news_cached" in t and 'rec["news"] = _news_cached' in t
pb.write_text(t, encoding="utf-8")
print("prematch_board.py: cached grok+gemini news on value/antisweep")

# web_ui: render the news line on the board card
u = pathlib.Path("/opt/cs2-analyst/web_ui.py")
w = u.read_text(encoding="utf-8")
anchor = "            P.append(f'''<div class=\"card{' val' if vd == 'VALUE' else ''}\">\n"
assert w.count(anchor) == 1, "board card append not found"
w = w.replace(anchor,
              "            newsb = (f'<div class=mrow><span class=mut>\\U0001f4f0</span>"
              "<span class=mut>{e(str(r.get(\"news\"))[:110])}</span></div>' if r.get('news') else '')\n" + anchor, 1)
assert w.count("{betline}{aline}{lineb}</div>'''") == 1
w = w.replace("{betline}{aline}{lineb}</div>'''", "{betline}{aline}{lineb}{newsb}</div>'''", 1)
u.write_text(w, encoding="utf-8")
print("web_ui.py: news line on board card")
