#!/usr/bin/env python3
"""Make the DRY-RUN sniper observable: add a 90s heartbeat + a 180s page reload (keeps the authed
winline session warm over long uptimes, and shows it's alive + how many near-locks are ready)."""
import pathlib
p = pathlib.Path("/opt/cs2-analyst/livelag_bet.py")
s = p.read_text(encoding="utf-8")
old = ('        await page.wait_for_timeout(7000)\n'
       '        while True:\n'
       '            if KILL.exists():\n'
       '                print("[livelag-bet] KILL file present — stopping."); break\n'
       '            if _daily_loss() >= DAILY_LOSS_RUB:\n'
       '                print(f"[livelag-bet] daily loss limit {DAILY_LOSS_RUB:.0f}₽ hit — stopping for today."); break\n'
       '            try:\n'
       '                await page.evaluate(\n'
       '                    "() => { const m=document.querySelector(\'main,[class*=main__wrapper]\'); if(m) m.scrollTop=0; }")\n'
       '                cards = await page.evaluate(\n'
       '                    "() => Array.from(document.querySelectorAll(\'[class*=card__body-live]\')).map(c => c.innerText)")\n'
       '                t_quote = time.time()\n')
new = ('        await page.wait_for_timeout(7000)\n'
       '        last_hb, last_reload = 0.0, time.time()\n'
       '        while True:\n'
       '            if KILL.exists():\n'
       '                print("[livelag-bet] KILL file present — stopping."); break\n'
       '            if _daily_loss() >= DAILY_LOSS_RUB:\n'
       '                print(f"[livelag-bet] daily loss limit {DAILY_LOSS_RUB:.0f}₽ hit — stopping for today."); break\n'
       '            try:\n'
       '                if time.time() - last_reload > 180:      # keep the authed page/session warm\n'
       '                    await page.reload(wait_until="domcontentloaded", timeout=45000)\n'
       '                    await page.wait_for_timeout(5000); last_reload = time.time()\n'
       '                await page.evaluate(\n'
       '                    "() => { const m=document.querySelector(\'main,[class*=main__wrapper]\'); if(m) m.scrollTop=0; }")\n'
       '                cards = await page.evaluate(\n'
       '                    "() => Array.from(document.querySelectorAll(\'[class*=card__body-live]\')).map(c => c.innerText)")\n'
       '                t_quote = time.time()\n'
       '                if time.time() - last_hb > 90:           # heartbeat so the sniper is not a black box\n'
       '                    nopp = sum(1 for raw in cards if (_parse_card(raw) and _opportunity(_parse_card(raw))))\n'
       '                    print(f"[livelag-bet] sniper alive: {len(cards)} live карт, {nopp} near-lock готовых к ставке")\n'
       '                    last_hb = time.time()\n')
assert s.count(old) == 1, "daemon loop anchor not found"
p.write_text(s.replace(old, new, 1), encoding="utf-8")
print("livelag_bet.py: daemon heartbeat + reload added")
