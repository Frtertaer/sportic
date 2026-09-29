# Analyst UI v2

Обновлённый интерфейс CS2/Football dashboard. CS2 построен вокруг решения «что заслуживает внимания сейчас»: сигналы идут первыми, затем live-матчи, ближайшие события, движение линии и калибровка модели.

## Что входит

- `web_ui_v2.py` — production renderer для `render()` и `render_football()`.
- `sample_state.json` — обезличенный снимок состояния для локальной проверки.
- `build_preview.py` — генерация `preview.html` и `football_preview.html`.
- `test_web_ui_v2.py` — регрессионные тесты рендера, XSS, provider markup, адаптивных контрактов и повреждённых данных.
- `remote_smoke.py` — изолированный smoke кандидата на реальном server-side состоянии.
- `post_deploy_http_smoke.py` — HTTP smoke `/api`, `/` и `/football`.
- `deploy_web_ui_v2.sh` — hash-guarded установка с резервной копией и rollback при ошибке.

## Проверка

```powershell
python -m py_compile web_ui_v2.py
python -m unittest -v test_web_ui_v2.py
python build_preview.py
```

## Production

- Развёрнуто: `2026-07-18 16:23:19 UTC`.
- SHA-256 `web_ui.py`: `11a31837f57ed71fa2477aa4c004bbb663ea4520e206bf2927d0d227515d21b2`.
- Резервная копия предыдущего v4: `/opt/cs2-analyst/web_ui.py.bak.20260718T162319Z.17efeaf2aa20`.
- Резервная копия предыдущего v2: `/opt/cs2-analyst/web_ui.py.bak.20260718T160739Z.95bb02c7c055`.
- После публикации проверены service status, `/api`, `/`, `/football`, журнал systemd и desktop/mobile layout.

Для отката резервную копию следует сначала скопировать во временный файл в `/opt/cs2-analyst`, затем атомарно заменить `web_ui.py` и перезапустить `cs2-web.service`.

## Локальная v3 — редакционная лента

`web_ui_v3.py` — отдельный локальный прототип в более плотной спортивной стилистике. Он не развёрнут на сервере и не изменяет production v2.

- плоские строки рекомендаций и матчей вместо вложенных карточек;
- дата-группы ближайших матчей и счёт рядом с командами;
- спокойная палитра без glow, gradient, blur и больших радиусов;
- согласованные вероятности ML-сигнала из match board;
- отдельные desktop/mobile снимки `preview_v3_desktop.png` и `preview_v3_mobile.png`;
- `test_web_ui_v3.py` добавляет проверки v3 поверх полного набора v2.

```powershell
python -m unittest -v test_web_ui_v2.py test_web_ui_v3.py
python build_preview_v3.py
```

## Локальная v4 — Atlantic Broadcast

`web_ui_v4.py` сохраняет плотную структуру v3, но заменяет почти монохромный материал на глубокие синие поверхности и семантическую палитру. Production использует автономную сборку `web_ui_v4_standalone.py`.

- CS-оранжевый — бренд и active navigation;
- cyan — модель;
- violet — рынок;
- mint — положительный edge;
- coral — LIVE/риск;
- цветные section/date bands и мягко тонированные строки;
- без gradient, glow, blur и больших теней.

Исследование и источники собраны в `COLOR_RESEARCH_V4.md`.

```powershell
python -m unittest -v test_web_ui_v2.py test_web_ui_v3.py test_web_ui_v4.py
python build_preview_v4.py
python build_standalone_v4.py --check
python -m unittest -v test_web_ui_v4_standalone.py
```
