#!/usr/bin/env python3
"""Reusable vision helper — reads images via the cheat-ai.shop Anthropic-proxy (claude-opus-4-8 + vision).
Key in vision_key.txt (chmod 600). NOTE: third-party gray-market proxy — every image passes THROUGH it,
so use ONLY for low-sensitivity content (public betting odds), NEVER for client-account/private screenshots."""
from __future__ import annotations
import base64, json, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_KEY = None
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _key() -> str:
    global _KEY
    if _KEY is None:
        _KEY = (ROOT / "vision_key.txt").read_text(encoding="utf-8-sig").strip()
    return _KEY


def read(img_path: str | Path, prompt: str, max_tokens: int = 1200) -> str | None:
    """Send an image + prompt to vision, return the text reply (or None on error)."""
    img_path = Path(img_path)
    data = base64.b64encode(img_path.read_bytes()).decode()
    payload = json.dumps({
        "model": "claude-opus-4-8", "max_tokens": max_tokens, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": _MEDIA.get(img_path.suffix.lower(), "image/png"), "data": data}},
            {"type": "text", "text": prompt},
        ]}],
    }).encode()
    req = urllib.request.Request("https://api.cheat-ai.shop/v1/messages", data=payload,
                                 headers={"x-api-key": _key(), "anthropic-version": "2023-06-01",
                                          "content-type": "application/json", "user-agent": _UA})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=150))
    except urllib.error.HTTPError as e:  # noqa: BLE001
        print("vision HTTP", e.code, e.read().decode()[:200]); return None
    except Exception as e:  # noqa: BLE001
        print("vision err", type(e).__name__, str(e)[:150]); return None
    return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")


def ask_text(prompt: str, max_tokens: int = 80) -> str | None:
    """Text-only Claude call via the same proxy (e.g. EN->RU team-name translation)."""
    payload = json.dumps({"model": "claude-opus-4-8", "max_tokens": max_tokens, "temperature": 0,
                          "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.cheat-ai.shop/v1/messages", data=payload,
                                 headers={"x-api-key": _key(), "anthropic-version": "2023-06-01",
                                          "content-type": "application/json", "user-agent": _UA})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=40))
    except Exception as e:  # noqa: BLE001
        print("ask_text err", type(e).__name__, str(e)[:120]); return None
    return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()


def read_json(img_path, prompt, max_tokens: int = 1200, retries: int = 3):
    """read() + parse a JSON array/object out of the reply (strips ```json fences). Retries on None/parse-fail."""
    for _ in range(max(1, retries)):
        txt = read(img_path, prompt, max_tokens) or ""
        t = txt.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        starts = [x for x in (t.find("["), t.find("{")) if x >= 0]
        a = min(starts) if starts else -1
        b = max(t.rfind("]"), t.rfind("}"))
        try:
            parsed = json.loads(t[a:b + 1]) if a >= 0 and b > a else json.loads(t)
            if parsed not in (None, {}, []):
                return parsed
        except Exception:  # noqa: BLE001
            pass
    return None
