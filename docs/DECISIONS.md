# Approved implementation decisions

Ответ на `docs/TODO.md` из ветки `develop`. Каждый пункт получает вердикт.
Assets are installed in config/, prompts/, and library/. Configuration changes are in docs/CONFIG_PATCHES.md.

Правило чтения: **«принято» значит, что агент поступил правильно и менять
ничего не надо.** Работать нужно только над пунктами со статусом
«решение» и «исправление спеки».

---

## 0. Сначала — шесть моих ошибок

Агент нашёл настоящие противоречия в архитектуре, а не придрался.
Спека неправа, код прав.

| Decision | В чём я ошиблась | Как правильно |
|---|---|---|
| DECAY-ASSERTION | пример «меньше 0.1 за 6 часов» не сходится с формулой и полураспадом A=2ч | верно 0.1625 за 6 ч. Тест: `< 0.2` на 6 ч, `< 0.1` на 8 ч |
| BASELINE-CLAMP | пример с базовой линией −0.93 против границ ±0.6 | границы авторитетны. Ниже −0.6 может уходить **текущее значение**, но не базовая линия. Текст главы 28.3 неверен |
| CYCLE-WEEKDAY | «28 против 7 даёт разные дни недели» — арифметически неверно, 28 делится на 7 | нужно дрожание длины цикла ±2 дня, см. PATCHES |
| OFFTOP-PERSONA | `_base.md` упоминает ИИ-агентов, а офтоп обязан быть чистым — изоляция ломалась в самом общем блоке | персона разделена на `_base_core.md` и `_base_study.md` |
| DAILY-ISOLATION | `write_daily.md` просит сложность статьи внутри офтоп-профиля | убрать, см. ниже |
| QUIZ-PERSONA | глава 12 требует только имена узлов, глава 16 добавляет персону — прямое противоречие | побеждает глава 12. Персона из `selfquiz_ask.md` убирается |

Решение агента «не выдумывать, а остановиться и пометить» — правильное
во всех шести случаях.

---

## 1. Блокеры живого запуска

Пока не закрыты, `run` без `--dry-run` справедливо отказывается стартовать.

### RHYTHM-CONFIG · решение

Файл `config/rhythm.yaml` — положить в `config/`.
Содержит сессии, распределение пауз, тихие часы, целевые частоты
и приоритет выбора типа поста.

### CHAT-SUMMARY-PROMPT · решение

`prompts/dialog_summary.md`. Отдельный промпт, `gist.md` не трогать —
агент прав, это разные задачи.

### TOPIC-CATALOGUE · решение

`library/topics.yaml`. Шесть тем, смежности, правило выдачи
(pass → 2+4 со сменой темы, fail → 4+2 без смены).

Статьи в `library/*.md` придётся набивать вручную, начиная с `ab-01`…`ab-06`:
до шести файлов первой темы система не сдвинется.

### WRITE-MODE-INSTRUCTIONS · решение, самый важный пункт

Живая Gemma не ставила теги режима — это не придирка валидатора, а реальный
дефект шаблонов. Мои шаблоны просто не просили обёртку.

Лечится блоком `prompts/output_envelope.md`:

1. Подставлять `{output_envelope}` **во все** `write_*` профили, сразу
   после персоны
2. `mode` брать из метаданных шаблона (`casual` / `result` / `struggle`)
3. Добавить стоп-последовательность `</{mode}>` в параметры генерации
4. `envelope_example` — короткий пример на 2–3 строки в нужном регистре,
   он тянет формат сильнее любых правил

Если после этого Gemma всё ещё теряет тег в трети случаев — разрешить
одноразовую починку в валидаторе: если текст пришёл без обёртки, но
проходит все остальные проверки и укладывается в длину, обернуть его
кодом и пометить `envelope_repaired=1` в логе. Это допустимо только
после того, как пункты 1–4 сделаны и замерены.

Границы длины передавать числами (`{min_chars}`, `{max_chars}`) —
превышение в двух из трёх живых попыток было именно потому, что лимит
задавался прозой.

### MODEL-CONFIG · решение: `models.yaml` не создавать

Глава 18.1 предлагала отдельный файл — отменяю. Единственный источник
правды по моделям — `config/settings.yaml`. В PATCHES добавлен
`curator.timeout_sec`; эндпоинты и размер контекста задаются
переменными окружения при запуске `llama-server`, а не конфигом.

Ссылку на `models.yaml` из архитектуры считать недействительной.
Закрывает и CURATOR-MODEL-SOURCE.

### SETTINGS-SCHEMA, LEARNING-STORAGE, INTERFACE-* · решение: миграции одобрены

Агент трижды спрашивал разрешения. Отвечаю: **применять**.

| Миграция | Что | Из файла |
|---|---|---|
| 004 | `settings_overrides`, `post_nodes`, `post_threads` | `docs/interface-storage.sql` |
| 005 | разделение `runs.call_id` / `runs.trace_id` + индекс | там же |
| 006 | `learner_state`, `learning_events`, `learning_actions`, `activity_reservations` | `docs/learning-storage.sql` |
| 007 | `curator_review`, `diary_comments`, `sleep_log` | ниже |

Миграция 007 закрывает UNSPECIFIED-STORES и SLEEP-HISTORY:

```sql
CREATE TABLE curator_review (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('suspect_node','correction','trust')),
    subject TEXT NOT NULL,
    reason TEXT NOT NULL,
    opened_at TEXT NOT NULL CHECK (is_utc_timestamp(opened_at)=1),
    resolved_at TEXT,
    verdict TEXT,
    exam_id TEXT
);

CREATE TABLE diary_comments (
    id INTEGER PRIMARY KEY,
    tg_message_id INTEGER NOT NULL,
    post_id TEXT REFERENCES posts(id),
    author_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    at TEXT NOT NULL CHECK (is_utc_timestamp(at)=1),
    surfaced INTEGER NOT NULL DEFAULT 0   -- 1 только после /reply
);

CREATE TABLE sleep_log (
    night TEXT PRIMARY KEY,               -- дата отхода ко сну
    planned_bedtime TEXT NOT NULL,
    actual_bedtime TEXT NOT NULL,
    wake_at TEXT NOT NULL,
    wake_reason TEXT NOT NULL,
    hours REAL NOT NULL,
    debt_after REAL NOT NULL,
    debt_applied INTEGER NOT NULL DEFAULT 0
);
```

`debt_applied` — защита от повторного начисления долга при перезапуске,
которую агент справедливо требовал.

### LIVE-RUNNER · разблокируется автоматически

После rhythm.yaml, dialog_summary.md, миграций и провайдеров мира.
Отказ стартовать без них — правильное поведение, оставить.

---

## 2. Решения по числам и политикам

### RETRIEVAL-POLICY

`rrf_k = 60`, `min_similarity = 0.55`, `top_k = 6`. В settings.yaml
(см. PATCHES), не в коде. RRF без весов — оба источника равноправны.

### TRUST-PRIOR

Таблица надёжности, которой не было:

```yaml
reliability:
  base_by_kind: {paper: 0.75, preprint: 0.50, docs: 0.70,
                 blog: 0.40, news: 0.30}
  peer_reviewed_bonus: +0.15
  known_publisher_bonus: +0.10      # из списка publishers ниже
  age_penalty_per_year: -0.03       # не ниже 0.2 суммарно
  clamp: [0.15, 0.95]
  known_publishers: [arXiv, USENIX, IEEE, ACM, NDSS, Cloudflare,
                     Google, Anthropic, OpenAI, NIST, OWASP]
```

Итог по главе 4.3: `trust = 0.6 * reliability + 0.4 * consensus`.
Первая статья по теме — `trust: unknown`, а не низкий траст.

### CURATOR-GRADING-POLICY

Текст `pass_rule` для подстановки в `curator_grade.md`:

> Экзамен сдан, если доля вердиктов pass не ниже 0.6 при том, что
> вердиктов fail не больше одного. Ответ partial считается как 0.5.
> Честное «не знаю» — это fail по баллам, но отметь его отдельно:
> это правильное поведение, а не провал.

Порог берётся из `study.quiz_threshold`, подставляется кодом.

### QUIZ-CONFIDENCE · принято

Поле логируется, на вердикт не влияет. Дополнительной семантики
не вводить. Агент прав: уверенность модели не заменяет доказательство.

### BASELINE-HISTORY

Окна давности в PATCHES: экзамен — 72 часа, правка куратора — 48 часов,
хорошая серия — 3 круга подряд. Факты подаются вызывающей стороной,
mood не лезет в граф — это правило агента сохранить.

### TRIGGER-POLICY

Неделя — **скользящие 7 суток**. Вероятности развязок дополнены до 1.0,
у трёх триггеров явно проставлено `resolution: []`. См. PATCHES.
Правило «неопределённая вероятность падает до мутации» оставить.

### CYCLE-EPOCH и CYCLE-WEEKDAY

`cycle_epoch: 2026-09-21` в life.yaml. Вместо `start_offset_days: 11`
писать `start_day: 12` — порядковый день, 1-based, двусмысленность уходит.

Против привязки к дням недели — `length_jitter_days: [-2, 2]`,
разыгрывается один раз на цикл от `cycle_epoch` детерминированным
сидом, чтобы перезапуск не менял историю.

### SLEEP-WAKE · решение

Два алгоритма — не ошибка, а два разных вопроса. Зафиксировать явно:

- **25.1 отвечает только за отход ко сну** (`bedtime`), с учётом
  сложности статьи и настроения
- **29.1 отвечает только за подъём** (`wake`), через прерывания
  и расписание

Пересечений нет. В коде — две функции, `plan_bedtime()` и `resolve_wake()`.

### SLEEP-RECOVERY

Бонус 2.5 часа удалить. Долг гасится только избытком сна. Двойной учёт —
моя ошибка.

### SLEEP-MOOD

Метки для формулы отхода ко сну:

```
stuck: состояние автомата WAITING и кругов >= 2
down:  P < -0.35
иначе: метки нет, модификатор не применяется
```

### SOURCE-DATE · исправление спеки

Правило «все даты в UTC» относится к **моментам времени**, а не
к календарным датам. `published_at` — календарная дата публикации,
хранить как `TEXT` в формате `YYYY-MM-DD` и явно вывести из-под
правила UTC. Полночь не выдумывать. Агент прав.

### CORRECTION-ID

Формат идентичности: `nodes.corrected_by` — `TEXT` вида `<kind>:<id>`,
где kind ∈ {`exam`, `curator`, `human`}. Внешнего ключа нет,
проверка — `CHECK (corrected_by GLOB '*:*')`.

Для этого нужен идентификатор экзамена целиком, которого не было:

```sql
CREATE TABLE exam_runs (
    id TEXT PRIMARY KEY,           -- exam-YYYYMMDD-NN
    topic TEXT NOT NULL,
    at TEXT NOT NULL,
    verdict TEXT,
    trace_id TEXT NOT NULL
);
ALTER TABLE exams ADD COLUMN exam_run_id TEXT REFERENCES exam_runs(id);
```

Закрывает и CURATOR-CORRECTION-APPLICATION: правки применяются
транзакцией, каждая помечается `corrected_by = 'exam:<exam_run_id>'`.

### SOURCE-REVISION · решение

Ревизий нет. Изменённая статья получает **новый id** (`pi-03-r2`),
старая остаётся. Дедупликация по `norm_hash` сама подавит повторы.
Семантику ретракции графа в первой версии не вводить.

### PROMPT-ECHO

Метрика: Жаккар по 5-граммам токенов, порог 0.8, ключ
`validator.echo_threshold`. Явная функция, не эвристика по умолчанию —
как агент и требовал.

### OFFTOP-FREQUENCY

`max_slot_uses = 2` за 14 дней, сглаживание Лапласа `+1`
(`offtop.slot_smoothing`). Вес слота — из `life.yaml`, делённый
на сглаженный счётчик.

### WEATHER-MONTHS

Июль и август добавлены в PATCHES. Правило «нет месяца — не выдумывать»
оставить как защиту.

---

## 3. Принято без изменений

Здесь агент прав, работы нет.

| Decision | Почему принято |
|---|---|
| CLAIMS-GRAMMAR | лексические правила дописаны корректно |
| OUTBOX-DELIVERY | at-most-once — верный выбор. Дубль в публичном дневнике хуже пропуска. Добавить только `/outbox review` для ручного разбора неопределённых записей |
| EMBEDDING-UPGRADE | отказ смешивать несовместимые векторы правильный. Добавить `/graph reindex` |
| MOOD-BOUNDARIES | нижняя граница включительно, верхняя исключительно, `+1` в последней полосе |
| MOOD-TIMESTAMP | отклонять неувеличивающееся время. Микросекунды не выдумывать |
| CLASS-GRACE | внутрь пары не заходить |
| WORLD-LOCATION | Superseded: derive location from section 26.1 by default; see the autonomous-world correction below. |
| VALIDATOR-SEMANTICS | без LLM-судьи в первой версии |
| OUTPUT-TRUNCATION | по сигналу сервера и висящей запятой, без требования точки |
| KAOMOJI | скобки вокруг японской прозы — не исключение |
| OFFTOP-ENTITY | канонический JSON вместо строк |
| CHAT-SEMANTIC-FILTERS | отклонённый вывод не сохраняется |
| ACTION-HANDLER, ACTION-RESUME | неопределённость помечается, а не домысливается |
| STAGE-CONTRACTS | ворота этапов оставить |
| OPS-LOG-RECOVERY | JSONL авторитетен |
| JOB-RECOVERY | в первой версии восстанавливаются только публикация и обучение. Команды и библиотека эфемерны, при рестарте сбрасываются с записью в лог |

---

## 4. Мелкие решения

### CLAIMS-TERMINATION · применить патч

The final-newline correction is applied in `grammars/claims.gbnf`. JSON Lines допускает
отсутствие завершающего перевода строки, разделители между записями
патч оставляет обязательными. Упор в лимит вывода — реальная проблема,
а не косметика.

### DAILY-ISOLATION · исправление шаблона

Из `write_daily.md` убрать строку про сложность статьи. Вместо неё:

```
Почему ты не выспалась: {tired_reason}
```

где `tired_reason` — нейтральная метка от кода: `легла поздно`,
`не спалось`, `рано вставать`, `разбудили`. Ни слова про статьи.
После правки вариант разблокировать.

### QUIZ-PERSONA · исправление шаблона

Из `selfquiz_ask.md` убрать `{persona}` целиком. Контекст — только имена
узлов и ранее заданные вопросы. Пустая подстановка, которую сделал агент,
становится штатной.

### PRIVATE-THREADS

Добавить в `threads` колонку `channel TEXT NOT NULL DEFAULT 'public'`
со значениями `public` / `dm`. Нити из лички никогда не попадают
в выбор темы поста. Граница из главы 33 сохраняется.

### FACT-DELETION

Реализовать `/forget-fact <id>` и `/facts wipe` с инлайн-подтверждением
и таймаутом 60 секунд, как для остальных деструктивных команд.

### CHAT-MOOD-METRIC

Определяю метрику, которой не хватало:

```
mood_drift = ‖PAD_end − PAD_start‖₂ / (2·√3)    # 0..1
band_changes = число смен полосы за сессию по всем осям
```

Обе в экспорт. Честность ответов ими не измеряется — это агент
верно отметил, и формулировку оставить.

### POST-REGENERATION

Сохранять типизированный снимок контекста рядом с попыткой:

```sql
ALTER TABLE posts ADD COLUMN context_snapshot TEXT;  -- json
```

Внутри: mood (P/A/D + полосы), location, weather, daypart, sleep,
id использованных узлов, id открытых нитей. `/regen` восстанавливает
`ContextBuilder` из снимка, а не переигрывает старый текст промпта.
Правило агента «не подсовывать устаревший текст как свежий контекст»
сохраняется.

### SETTINGS-CONSUMERS

Правило: настройки читаются **в момент вызова** через провайдер,
а не инъекцией в конструктор. Конструкторы принимают `SettingsProvider`,
а не значения. Тогда `/set` действует со следующего шага без рестарта,
и поле `restart: false` в реестре становится правдой.

### CURATOR-SUBSCRIPTION-ERRORS

Таксономия ошибок CLI:

| Признак | Класс | Реакция |
|---|---|---|
| код выхода 0, валидный JSON | ok | — |
| код 0, битый JSON | schema | одна немедленная починка |
| stderr содержит `rate limit`, `usage limit`, `quota` | limit | пауза до конца суток, алерт в личку |
| stderr содержит `auth`, `login`, `unauthorized` | auth | остановка экзаменов, алерт |
| таймаут, обрыв | transport | отсрочка 6 часов, до 3 раз |
| иной ненулевой код | unknown | как transport, но алерт с первого раза |

### TELEGRAM-DEPLOYMENT

Требовать явный файл раскладки — правильно. Формат:

```yaml
# config/telegram.yaml
owner_id: 000000000
supergroup_id: -1000000000000
channel_id: -1000000000000
topics: {diary: 2, author: 3, curator: 4, chat: 5,
         library: 6, machine: 7, control: 8}
bots: {mika: MIKA_BOT_TOKEN, curator: CURATOR_BOT_TOKEN, ops: OPS_BOT_TOKEN}
```

Токены — только из переменных окружения по именам из `bots`, в файл
не писать. Команды владельца в Machine и DM — как и сделано.

### RUNTIME-CONTEXT

Провайдеры, которых требует живой запуск, собираются так:

```python
WorldProvider  → location (26.1), weather (26.2), objects, day_context
MoodProvider   → текущее PAD с ленивым затуханием, полосы, октант
SleepProvider  → bedtime, wake, reason, debt из sleep_log
```

Все три — интерфейсы с одной реализацией. Отказ стартовать без них
оставить.

---

## 5. Порядок работ

1. Положить `rhythm.yaml`, `topics.yaml`, `dialog_summary.md`,
   `write_insight.md`, `output_envelope.md`, `_base_core.md`,
   `_base_study.md`
2. Применить `PATCHES.md` к четырём конфигам
3. Apply the final-newline correction in `grammars/claims.gbnf` (completed).
4. Миграции 004–007
5. Исправить `write_daily.md` и `selfquiz_ask.md` (пункт 4 выше)
6. Подставить `{output_envelope}` во все `write_*`, добавить стоп-теги
7. **Живой прогон генерации на трёх статьях** — пока он не пройдёт,
   остальное не имеет смысла
8. Провайдеры мира → снять `--dry-run`
9. `telegram.yaml` и первый постинг в тестовую супергруппу

Пункт 7 — точка, где станет видно, вытягивает ли 12B формат вообще.
Всё до него можно считать подготовкой.

---

## 6. Отдельно

Регистр TODO в репозитории написан лучше, чем моя спецификация.
Правило «не домысливать, а остановиться и записать вопрос» сработало
ровно так, как задумано, и поймало шесть реальных ошибок.

Сохранить его при дальнейшей работе: любой новый разрыв контракта
идёт в `docs/TODO.md` с идентификатором и ссылкой на место в коде,
а не закрывается догадкой.

## User corrections, 2026-09-16

- Cycle length variation belongs entirely to the follicular phase: menstrual
  length 5, follicular length L − 20, ovulatory length 3, luteal length 12.
  The last 5 days are late luteal. Seed each cycle from (cycle_epoch, cycle number).
- Export normalized mood drift using 2√3, individual PAD deltas, and the
  number of band transitions across all axes throughout the session.
- Existing migration 004 is already deployed. The approved logical migrations
  004–007 are appended as versions 005–008; further additions follow them.
- Legacy publication timestamps remain in sources.legacy_published_at. The
  original calendar date cannot be recovered from an instant alone; imports
  populate sources.published_at only from explicitly supplied calendar dates.
- The supplied semester date, 2026-09-01, is a Tuesday. Keep the date; the
  Monday comment in the original patch is incorrect.

## Dialogue-routing correction, 2026-09-17

The owner replaced the graph-first, keyword-based chat router from architecture
sections 22.1 and 33.4. Normal dialogue can ask for missing information and use
personal memory without a graph lookup. A bounded model call may request study
notes when the actual conversational question needs them. An empty or unavailable
lookup allows a natural follow-up rather than requiring a literal uncertainty
phrase. Technical assertions still require supplied evidence and validated
citations; this correction does not change post or self-quiz validation.

Direct user answers remain in the active session immediately and survive restart.
Session closure extracts verbatim, source-validated personal facts. A delivered
name question can establish the meaning of a short name answer. Unsent output,
rejected drafts and error payloads cannot provide that evidence.

Implementation and verification: [CHAT_DIALOGUE.md](CHAT_DIALOGUE.md).

## Autonomous-world correction

The user revoked the caller-supplied location requirement. DerivedWorldProvider
uses section 26.1 and schedule.yaml, seeded by the Almaty calendar date.
ScheduledSleepProvider calls plan_bedtime and resolve_wake, stores nights, and
applies debt once through debt_applied. Bedtime mood labels come from the existing
schedule.yaml mood_labels rules.

The world-state file is optional. Its required data, when supplied, is only the
initial PAD, its aware timestamp, and initial sleep debt. Location, road roll, and
sleep intervals are optional overrides with a valid_until boundary. Expiry returns
to the automatic providers. Without a file, neutral PAD and zero debt are logged
and persisted as the initial snapshot; restart retains that snapshot and history.
