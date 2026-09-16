# Точечные правки к существующим конфигам

Applied configuration corrections from the supplied decisions.

## life.yaml

**WEATHER-MONTHS** — добавить в `weather:`

```yaml
  7:  [пекло, душно даже ночью, гроза к вечеру, ветер с гор принёс прохладу]
  8:  [жара к обеду, дым от чего-то, прохладные ночи, арбузы везде]
```

**OFFTOP-PEOPLE** — добавить `ref` каждому человеку в `people:`

```yaml
  - id: dasha
    ref: {nom: Даша, gen: Даши, dat: Даше, acc: Дашу, ins: Дашей}
  - id: aika
    ref: {nom: Айка, gen: Айки, dat: Айке, acc: Айку, ins: Айкой}
  - id: mama
    ref: {nom: мама, gen: мамы, dat: маме, acc: маму, ins: мамой}
  - id: brother
    ref: {nom: брат, gen: брата, dat: брату, acc: брата, ins: братом}
  - id: timur
    ref: {nom: Тимур, gen: Тимура, dat: Тимуру, acc: Тимура, ins: Тимуром}
```

Фрейм подставляет форму по падежу: `{people:dasha.nom}`.
Фрейм с неразрешённой ссылкой остаётся недоступным — правило агента верное.

**OFFTOP-BINDINGS** — привязать `coffee_state` в слоте `dom`

```yaml
    bindings:
      coffee_state: {from: progress, key: coffee_machine.stages}
```

**SEMESTER-EPOCH** — добавить в корень

```yaml
semester:
  start: 2026-09-01      # First semester date (Tuesday).
  weeks: 16
```

**CYCLE-EPOCH** — добавить в корень

```yaml
cycle_epoch: 2026-09-21  # дата запуска канала, aware, Asia/Almaty
```

## mood.yaml

**CYCLE-EPOCH** — заменить `start_offset_days: 11` на однозначное

```yaml
  start_day: 12          # порядковый день цикла на cycle_epoch, 1-based
```

**CYCLE-WEEKDAY** — 28 делится на 7, привязка к дням недели реальна.
Добавить дрожание длины цикла:

```yaml
  length_days: 28
  length_jitter_days: [-2, 2]   # Deterministic per cycle.
  phase_lengths: {menstrual: 5, ovulatory: 3, luteal: 12}
  luteal_late_last_n: 5         # Replaces absolute late_days.
  # Follicular length is L - 20; it absorbs all variation.
```

**TRIGGER-POLICY** — вероятности развязок должны давать 1.0.
Заменить блоки `resolution` на полные:

```yaml
    # fight_with_boyfriend
      resolution:
        - {id: made_up,     p: 0.6, delta: [+0.50, -0.15, +0.20], after_hours: 20}
        - {id: still_angry, p: 0.4, delta: [-0.10, -0.20, -0.10], after_hours: 30}
    # parents_pressure
      resolution:
        - {id: let_go,      p: 0.7, delta: [+0.35, -0.20, +0.25], after_hours: 24}
        - {id: stewing,     p: 0.3, delta: [-0.05, -0.10, -0.05], after_hours: 36}
    # friend_conflict
      resolution:
        - {id: made_up,     p: 0.85, delta: [+0.30, -0.15, +0.10], after_hours: 12}
        - {id: cold_war,    p: 0.15, delta: [-0.10, -0.10, -0.05], after_hours: 24}
    # boyfriend_sweet, parents_proud, grade_disaster — развязки нет,
    # добавить явное поле, чтобы код не искал её:
      resolution: []
```

И в `rules:` добавить:

```yaml
    week_window: rolling       # неделя = скользящие 7 суток от now
```

**BASELINE-HISTORY** — добавить окна давности

```yaml
baseline_windows:
  exam_recent_hours: 72
  correction_recent_hours: 48
  quiz_streak_good_count: 3     # столько успешных кругов подряд
  stuck_days_from: 1
```

**MOOD-DISABLED** — добавить раздел

```yaml
disabled_state:
  p: 0.0
  a: 0.0
  d: 0.0
  band_text: "Ровное рабочее состояние."
  octant_name: нейтрально
  drop_queued_resolutions: true
  skip_post_when_low: false
```

## schedule.yaml

**SLEEP-RECOVERY** — удалить строку целиком:

```yaml
  debt_recovery_per_good_night: 2.5     # УДАЛИТЬ
```

Долг гасится только избытком сна по формуле. Агент прав: двойной учёт.

**WAKE-TIMES** — добавить `window` тем прерываниям, где его нет

```yaml
    - id: dasha_hairdryer
      window: ["07:40", "08:30"]
    - id: delivery_doorbell
      window: ["10:00", "18:00"]
    - id: overslept
      window: ["+0:00", "+0:45"]   # смещение от времени будильника
```

**CLASS-GRACE** — уточнить смысл

```yaml
blackout:
  - {reason: пара, source: university, grace_after_min: 5, grace_before_min: 0}
```

Пять минут после конца пары, внутрь занятия окно не заходит.

**COMMUTE-WINDOW** — убрать фиксированное окно, считать от расписания

```yaml
  - reason: дорога
    derive: {from_first_class: true, minus_minutes: 45, length_minutes: 40}
    chance_to_post: 0.3
```

**SLEEP-MOOD** — добавить правило маппинга

```yaml
mood_labels:
  stuck: {learning_state: WAITING, min_rounds: 2}
  down:  {p_below: -0.35}
  # оба не выполнены → метка отсутствует, модификатор не применяется
```

## settings.yaml

Добавить ключи, которых требует код:

```yaml
- {key: curator.timeout_sec, group: curator, type: int, range: [60, 900],
   default: 300, title: Таймаут куратора,
   desc: Сколько ждать ответа от claude -p.,
   effect: Меньше — чаще срывы на длинных экзаменах.}

- {key: retrieval.rrf_k, group: study, type: int, range: [10, 200], default: 60,
   title: Константа RRF,
   desc: Параметр слияния рангов FTS и косинуса.,
   effect: Больше — ранги сглаживаются, меньше — верхние результаты доминируют.}

- {key: retrieval.min_similarity, group: study, type: float, range: [0.3, 0.9],
   default: 0.55, title: Порог косинуса,
   desc: Ниже этого узел не считается найденным.,
   effect: Выше — чаще честное «не знаю». Ниже — чаще притянутые ответы.}

- {key: retrieval.top_k, group: study, type: int, range: [3, 12], default: 6,
   title: Узлов в ответ, desc: Сколько заметок подаётся в контекст ответа.,
   effect: Больше — полнее ответ, выше риск, что она смешает темы.}

- {key: offtop.max_slot_uses, group: world, type: int, range: [1, 5], default: 2,
   title: Повторов слота за окно,
   desc: Сколько раз один слот офтопа может сработать за 14 дней.,
   effect: Больше — однообразнее лента.}

- {key: offtop.slot_smoothing, group: world, type: float, range: [0.1, 5.0],
   default: 1.0, title: Сглаживание весов,
   desc: Прибавка Лапласа к счётчику использований слота.,
   effect: Защищает от деления на ноль и резких перекосов у новых слотов.}

- {key: validator.echo_threshold, group: system, type: float, range: [0.5, 0.95],
   default: 0.8, title: Порог эха промпта,
   desc: Жаккар по 5-граммам токенов между ответом и промптом.,
   effect: Ниже — режет нормальные ответы, выше — пропускает пересказ задания.}

- {key: validator.repeat_threshold, group: system, type: float, range: [0.6, 0.95],
   default: 0.86, title: Порог повтора,
   desc: Косинус против последних 30 постов.,
   effect: Ниже — частые ложные перегенерации.}
```
