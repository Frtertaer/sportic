import sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import analyst
for w in ("grok", "claude-code", "gpt-cursor", "gemini-cli"):
    t0 = time.time()
    try:
        r = analyst.call_omni(w, "Reply with exactly: PONG", max_tokens=12)
        ok = "PONG" in (r or "").upper()
        print(f"{w:12} {'OK' if ok else 'RESP'} ({time.time()-t0:.1f}s): {(r or '').strip()[:40]}")
    except Exception as e:
        print(f"{w:12} FAIL ({time.time()-t0:.1f}s): {type(e).__name__} {str(e)[:60]}")
