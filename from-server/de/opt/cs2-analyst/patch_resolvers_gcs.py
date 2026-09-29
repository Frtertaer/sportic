#!/usr/bin/env python3
"""Wire g-cs.ru (full coverage) into both resolvers as the PRIMARY result source; bo3 stays fallback."""
import pathlib

# ---------- line_steam_resolve.py ----------
p = pathlib.Path("/opt/cs2-analyst/line_steam_resolve.py")
s = p.read_text(encoding="utf-8")
s = s.replace("import veto_sim as vs      # noqa: E402\n",
              "import veto_sim as vs      # noqa: E402\nimport gcs_lookup          # noqa: E402\n", 1)
# after computing pending, load gcs
s = s.replace("    fin = _finished()\n    conn = vs._conn(); names = {}\n",
              "    gcs_res = gcs_lookup.load()\n    fin = _finished()\n    conn = vs._conn(); names = {}\n", 1)
# at the top of the per-bet loop, try gcs first
s = s.replace(
    "    for b in pending:\n"
    "        b1, b2, side = _norm(b[\"t1\"]), _norm(b[\"t2\"]), _norm(b[\"side\"])\n"
    "        match = None\n",
    "    for b in pending:\n"
    "        b1, b2, side = _norm(b[\"t1\"]), _norm(b[\"t2\"]), _norm(b[\"side\"])\n"
    "        g = gcs_lookup.find(b[\"t1\"], b[\"t2\"], gcs_res)\n"
    "        if g:\n"
    "            won = bool(side and (side in g[\"winner_n\"] or g[\"winner_n\"] in side))\n"
    "            rec = dict(b); rec[\"outcome\"] = \"win\" if won else \"loss\"\n"
    "            rec[\"series_final\"] = f'{g[\"s1\"]}-{g[\"s2\"]}'; rec[\"source\"] = \"gcs\"\n"
    "            rec[\"pl_units\"] = round((b[\"entry\"] - 1) if won else -1.0, 2)\n"
    "            rec[\"settled_at\"] = datetime.now(timezone.utc).isoformat(); out.append(rec); continue\n"
    "        match = None\n", 1)
assert "gcs_lookup.find" in s
p.write_text(s, encoding="utf-8")
print("line_steam_resolve.py: gcs primary")

# ---------- board_resolve.py ----------
q = pathlib.Path("/opt/cs2-analyst/board_resolve.py")
b = q.read_text(encoding="utf-8")
b = b.replace("import veto_sim as vs         # noqa: E402\n",
              "import veto_sim as vs         # noqa: E402\nimport gcs_lookup             # noqa: E402\n", 1)
b = b.replace("    fin = _finished(); conn = vs._conn(); names = {}\n",
              "    gcs_res = gcs_lookup.load()\n    fin = _finished(); conn = vs._conn(); names = {}\n", 1)
b = b.replace(
    "    for b in pending:\n"
    "        b1, b2, side = _norm(b[\"t1\"]), _norm(b[\"t2\"]), _norm(b[\"side\"])\n"
    "        match = None\n",
    "    for b in pending:\n"
    "        b1, b2, side = _norm(b[\"t1\"]), _norm(b[\"t2\"]), _norm(b[\"side\"])\n"
    "        g = gcs_lookup.find(b[\"t1\"], b[\"t2\"], gcs_res)\n"
    "        if g:\n"
    "            side_is_g1 = bool(side and (side in g[\"g1n\"] or g[\"g1n\"] in side))\n"
    "            side_score = g[\"s1\"] if side_is_g1 else g[\"s2\"]\n"
    "            won = (side_score >= 1) if b[\"market\"] == \"antisweep\" else bool(side and (side in g[\"winner_n\"] or g[\"winner_n\"] in side))\n"
    "            rec = dict(b); rec[\"outcome\"] = \"win\" if won else \"loss\"\n"
    "            rec[\"series_final\"] = f'{g[\"s1\"]}-{g[\"s2\"]}'; rec[\"source\"] = \"gcs\"\n"
    "            rec[\"pl_units\"] = (round((b[\"odd\"] - 1) if won else -1.0, 2) if b[\"market\"] == \"ml\" and b.get(\"odd\") else None)\n"
    "            rec[\"settled_at\"] = datetime.now(timezone.utc).isoformat(); out.append(rec); continue\n"
    "        match = None\n", 1)
assert "gcs_lookup.find" in b
q.write_text(b, encoding="utf-8")
print("board_resolve.py: gcs primary")

# ---------- line_steam_backtest.py ----------
k = pathlib.Path("/opt/cs2-analyst/line_steam_backtest.py")
c = k.read_text(encoding="utf-8")
c = c.replace("import veto_sim as vs\n", "import veto_sim as vs\nimport gcs_lookup\n", 1)
c = c.replace("graded, pending = [], []\n", "gcs_res = gcs_lookup.load()\ngraded, pending = [], []\n", 1)
c = c.replace(
    "for r in sig:\n"
    "    b1, b2, side = _norm(r[\"t1\"]), _norm(r[\"t2\"]), _norm(r[\"side\"])\n"
    "    match = None\n",
    "for r in sig:\n"
    "    b1, b2, side = _norm(r[\"t1\"]), _norm(r[\"t2\"]), _norm(r[\"side\"])\n"
    "    g = gcs_lookup.find(r[\"t1\"], r[\"t2\"], gcs_res)\n"
    "    if g:\n"
    "        won = bool(side and (side in g[\"winner_n\"] or g[\"winner_n\"] in side))\n"
    "        entry = r[\"close\"][0] if side == _norm(r[\"t1\"]) else r[\"close\"][1]\n"
    "        graded.append((r, won, entry, f'{g[\"s1\"]}-{g[\"s2\"]}')); continue\n"
    "    match = None\n", 1)
assert "gcs_lookup.find" in c
k.write_text(c, encoding="utf-8")
print("line_steam_backtest.py: gcs primary")
