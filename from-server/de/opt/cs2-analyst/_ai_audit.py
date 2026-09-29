"""Audit all OmniRoute AI workers: ping each, report alive/failed + latency + a reply snippet."""
import time
import analyst

WORKERS = ["claude-code", "gpt-cursor", "chatgpt", "grok", "gemini-cli"]
PROMPT = "Reply in ONE short line: confirm you are online and name which AI you are."

print("=== OmniRoute AI workers audit ===\n")
for w in WORKERS:
    t0 = time.time()
    try:
        r = analyst.call_omni(w, PROMPT, max_tokens=80)
        dt = time.time() - t0
        snippet = " ".join(r.split())[:130]
        print(f"[✅ OK  {dt:5.0f}s] {w:12} -> {snippet}")
    except Exception as e:
        dt = time.time() - t0
        msg = str(e)
        # surface HTTP status if present
        code = ""
        if hasattr(e, "code"):
            code = f" HTTP{e.code}"
        print(f"[❌ FAIL {dt:5.0f}s] {w:12} -> {type(e).__name__}{code}: {msg[:90]}")
print("\n(first call to a browser-worker is slow — it launches/refreshes its session)")
