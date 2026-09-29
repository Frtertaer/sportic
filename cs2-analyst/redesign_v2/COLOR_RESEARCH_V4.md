# Цветовое исследование для Analyst v4

Дата исследования: 18 июля 2026 года. Production в ходе исследования не изменялся.

## Спортивные продукты

- [VLR Matches](https://www.vlr.gg/matches) — плотные строки, тёмная шапка и стабильные красный/зелёный статусы; дополнительный цвет дают турниры и логотипы.
- [BO3 Match Center](https://bo3.gg/matches/current) — несколько хроматических уровней тёмных поверхностей, синий active-state, золотой prediction и красный LIVE.
- [Flashscore eSports](https://www.flashscore.com/esports/) — крупная фирменная зона и один насыщенный active/live цвет.
- [FotMob](https://www.fotmob.com/) — цветные полосы лиг и естественные цвета логотипов поверх спокойных строк.
- [Sofascore eSports](https://www.sofascore.com/esports) — выраженная синяя брендовая шапка и отдельная красная live-семантика.

Общий вывод: цвет должен занимать крупные устойчивые зоны — шапку, поверхности, заголовки групп и важные состояния. Разнообразие не следует создавать случайными разноцветными карточками.

## Дизайн-системы и статьи

- [Carbon: color overview](https://carbondesignsystem.com/elements/color/overview/) — тёмные интерфейсы строятся слоями поверхностей, а не одним чёрным полотном.
- [Carbon: data visualization palettes](https://carbondesignsystem.com/data-visualization/color-palettes/) — категориальные, последовательные и alert-палитры имеют разные задачи; несколько градиентов не рекомендуются.
- [IBM Data Visualization Basics](https://www.ibm.com/design/language/data-visualization/design/basics/) — каждый цвет должен иметь причину и стабильное значение.
- [Adobe Spectrum: color for data visualization](https://spectrum.adobe.com/page/color-for-data-visualization/) — одна метрика сохраняет один цвет во всех представлениях.
- [Smashing Magazine: dashboard design](https://www.smashingmagazine.com/2021/11/dashboard-design-research-decluttering-data-viz/) — палитра должна поддерживать назначение продукта и семантические ассоциации.
- [Material color roles](https://developer.android.com/design/ui/wear/guides/styles/color/roles-tokens) — акцентные, surface- и semantic-роли следует токенизировать.
- [W3C: use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) — состояние нельзя сообщать только цветом.
- [W3C: contrast minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) — обычный текст требует контраста не ниже 4.5:1.

## Принятая система Atlantic Broadcast

| Роль | Цвет | Использование |
|---|---:|---|
| Canvas | `#0B1724` | общий хроматический фон |
| Header | `#10263B` | брендовая навигационная зона |
| Surface | `#152B40` | панели и таблицы |
| Row | `#1B354C` | обычная строка данных |
| Brand | `#E8864A` | wordmark, active navigation, главный маркер |
| Model | `#63CFDB` | вероятность модели и фактическая калибровка |
| Market | `#B39AF2` | вероятность рынка и ожидаемая калибровка |
| Value | `#67C18F` | положительное преимущество |
| Live/Risk | `#F07A76` | live-состояние и риск |
| Warning | `#DFB65E` | неизвестный статус и внимание |

В интерфейсе остаются текстовые метки `LIVE`, `VALUE`, `РИСК`, `Δ` и знаки `+/-`; цвет только усиливает значение.

## Что сознательно не используется

- glow и светящиеся обводки;
- glassmorphism и backdrop blur;
- градиенты на каждой карточке;
- одинаковые большие радиусы;
- случайный цвет без постоянной роли;
- полностью залитые ярким цветом крупные блоки.

