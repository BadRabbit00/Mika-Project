# prompts/ — карта

Ни один промпт не собирается в коде. `ContextBuilder` берёт файл
по имени профиля, подставляет блоки и отдаёт в `llm_*`.

## Ученица — Gemma 4 12B

| Файл | Профиль | Персона | temp | Грамматика | Выход |
|---|---|---|---|---|---|
| `_base.md` | блок `persona` | — | — | — | вставляется в другие |
| `extract.md` | `extract` | **нет** | 0.2 | `claims.gbnf` | триплеты |
| `gist.md` | `gist` | **нет** | 0.1 | — | одна фраза |
| `selfquiz_ask.md` | `selfquiz_ask` | да | 0.7 | — | список вопросов |
| `selfquiz_answer.md` | `selfquiz_answer` | нет | 0.3 | `answer.gbnf` | ответ + cited |
| `write_found.md` | `write_tech` | да | 0.85 | — | `<casual>` |
| `write_impression.md` | `write_tech` | да | 0.85 | — | `<casual>` |
| `write_struggle.md` | `write_tech` | да | 0.9 | — | `<struggle>` |
| `write_summary.md` | `write_tech` | да | 0.75 | — | `<result>` |
| `write_correction.md` | `write_tech` | да | 0.8 | — | `<result>` |
| `write_offtop.md` | `write_offtop` | да | 0.95 | — | `<casual>` |
| `write_daily.md` | `write_offtop` | да | 0.95 | — | `<casual>` |
| `write_situation.md` | `write_offtop` | да | 1.0 | — | `<casual>` |
| `chat_topical.md` | `chat` | да | 0.4 | `answer.gbnf` | ответ + cited |
| `chat_unknown.md` | `chat` | да | 0.7 | — | текст |
| `chat_personal.md` | `chat` | да | 0.9 | — | текст |
| `chat_session.md` | `chat_session` | да | 0.75 | по режиму | текст |
| `facts_extract.md` | `facts_extract` | **нет** | 0.1 | `facts.gbnf` | факты |

Режим размышления (`<|think|>`) — выключен везде. Для письма он
не нужен, а латентность на ноуте растёт заметно.

## Куратор — Claude Code CLI, Sonnet 5, effort medium

| Файл | Когда | Подаётся |
|---|---|---|
| `curator_system.md` | всегда | `--append-system-prompt` |
| `curator_exam.md` | вызов 1: составить вопросы | тело запроса |
| `curator_grade.md` | вызов 2: проверить ответы | тело запроса |
| `curator_select.md` | после экзамена: выдать статьи | тело запроса |
| `ingest_complexity.md` | на каждую входящую статью, `--effort low` | тело запроса |

## Блоки состояния

Добавляются автоматически в каждый профиль, где есть персона:

| Блок | Содержимое | Источник |
|---|---|---|
| `{mood}` | 4 строки: октант, подсказка, три полосы | `core/mood.py` |
| `{when}` `{daypart}` | время и часть суток | `core/schedule.py` |
| `{location}` | дом / универ / улица / кофейня / транспорт | `core/world.py` |
| `{bedtime}` `{wake_time}` `{wake_reason}` | вчерашний сон | `core/schedule.py` |
| `{sleep_debt}` | часов недосыпа | `core/schedule.py` |
| `{weather}` | погода, **может быть пусто** | `core/world.py` |

Числа PAD в промпт не попадают никогда. Только текст полос.
Фаза цикла не попадает в промпт вообще — ни числом, ни словом.
Она меняет настроение, и модель видит только результат.

`chat_session` — единственный профиль с настоящей историей.
Всё остальное stateless.

## Плейсхолдеры

Каждый `{name}` соответствует блоку в `BLOCKS` реестра. Промпт
с плейсхолдером, которого нет в реестре, должен падать на старте,
а не в рантайме — добавь проверку в тесты.

`{emoji_max}` берётся из `SETTINGS["persona.emoji_max"]`,
`{n}` — из `SETTINGS["study.questions_per_round"]`,
`{pass_rule}` в `curator_grade.md` собирается из
`SETTINGS["study.quiz_threshold"]`.

## Что где нельзя

Проверяется ассертами в `ContextBuilder._enforce`:

- `extract.md`, `gist.md`, `facts_extract.md` — **без персоны**.
  Это чистые функции, характер там только мешает и портит формат
- `selfquiz_ask.md` — **только имена узлов**, никогда содержимое
- `write_offtop.md`, `chat_personal.md` — **ничего технического**:
  ни графа, ни статей, ни темы
- `people_facts` — только в `chat`, никогда в `write_*`

## Порядок правки

Промпты меняются чаще всего остального. Держи их в git отдельными
коммитами и записывай в сообщение, что именно чинил: через месяц
будет невозможно вспомнить, почему у `write_struggle` температура
0.9, а не 0.8.

Хорошая привычка: перед правкой прогнать текущую версию на пяти
одинаковых входах и сохранить выходы. После правки — на тех же
входах. Иначе «стало лучше» невозможно отличить от разброса.
