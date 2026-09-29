import shutil, os

def patch(path, repls, add_import=None):
    s = open(path, encoding="utf-8").read()
    if not os.path.exists(path + ".bak_params"):
        shutil.copy(path, path + ".bak_params")
    changed = 0
    if add_import and add_import not in s:
        # insert after the first 'import ' block-ish: put right after the module docstring / first import
        idx = s.find("\nimport ")
        if idx < 0:
            idx = s.find("\nfrom ")
        s = s[:idx] + "\n" + add_import + s[idx:]
        changed += 1
    for old, new in repls:
        if new in s:
            print(f"  already: {os.path.basename(path)} :: {new[:40]}"); continue
        assert old in s, f"ANCHOR MISSING in {path}: {old[:60]}"
        s = s.replace(old, new, 1); changed += 1
    open(path, "w", encoding="utf-8").write(s)
    print(f"patched {os.path.basename(path)} ({changed} edits)")

# 1) veto_sim.calibrate_map_p -> read shrink/cap from cs2_params
patch("/opt/cs2-analyst/veto_sim.py",
      [("    return 0.5 + max(-0.10, min(0.10, (p - 0.5) * 0.6))",
        "    import cs2_params as _cp\n"
        "    _cap = _cp.get(\"map_calib_cap\", 0.10); _sh = _cp.get(\"map_calib_shrink\", 0.6)\n"
        "    return 0.5 + max(-_cap, min(_cap, (p - 0.5) * _sh))")])

# 2) totals_monitor MIN_EDGE/MAX_EDGE -> config
patch("/opt/cs2-analyst/totals_monitor.py",
      [("        if not (MIN_EDGE <= edge <= MAX_EDGE):",
        "        if not (cs2_params.get(\"totals_min_edge\", MIN_EDGE) <= edge <= cs2_params.get(\"totals_max_edge\", MAX_EDGE)):")],
      add_import="import cs2_params")

# 3) live_monitor series gate 0.53 -> config
patch("/opt/cs2-analyst/live_monitor.py",
      [("            if e >= MIN_EDGE and p >= 0.53:",
        "            if e >= MIN_EDGE and p >= __import__(\"cs2_params\").get(\"series_gate_p\", 0.53):")])

print("DONE")
