import json, ssl, http.cookiejar, urllib.request, base64, sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import analyst

IMG = "/opt/kwork-monitor/data/agent_step.png"


def call_vision(model, prompt, img_path, max_tokens=300):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                     urllib.request.HTTPCookieProcessor(cj))
    base = analyst.OMNI_URL.rstrip("/")
    op.open(urllib.request.Request(base + "/api/auth/login",
            data=json.dumps({"username": analyst.OMNI_USER, "password": analyst.OMNI_PASS}).encode(),
            headers={"Content-Type": "application/json"}), timeout=30)
    b64 = base64.b64encode(open(img_path, "rb").read()).decode()
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]
    r = op.open(urllib.request.Request(base + "/api/managed-agents/chat-completion",
            data=json.dumps({"model": model, "messages": [{"role": "user", "content": content}],
                             "stream": False, "tools": [], "tool_choice": "none",
                             "max_tokens": max_tokens}).encode(),
            headers={"Content-Type": "application/json"}), timeout=230)
    return json.load(r)["choices"][0]["message"]["content"].strip()


import os
print("img exists:", os.path.exists(IMG), "size", os.path.getsize(IMG) if os.path.exists(IMG) else 0)
for m in ("gemini-cli", "claude-code", "gpt-cursor", "grok"):
    try:
        t0 = time.time()
        out = call_vision(m, "What web page / content do you see in this screenshot? Answer in one sentence.", IMG)
        print(f"\n[{m}] ({time.time()-t0:.0f}s): {out[:220]}")
    except Exception as e:
        print(f"\n[{m}] FAIL {type(e).__name__}: {str(e)[:140]}")
