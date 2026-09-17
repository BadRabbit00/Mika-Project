# Autonomous life simulation

Offline factual renderer. No models, token estimates or Telegram delivery.
Times use Asia/Almaty. Temporary databases are discarded after verification.
A midpoint runtime restart preserves the day, resource state and receipts.

Days: 3. Final cash: 13920 KZT; debt: 0 KZT.
Storage counts: `{"activity_transitions": 64, "life_breaks": 1, "life_days": 3, "life_events": 255, "life_tasks": 17, "money_ledger": 29, "mood": 44, "outbox": 167, "world_calendars": 15, "world_changes": 262, "world_plans": 62, "world_runs": 20, "world_steps": 45}`.

## Timeline

| Time | Place | Activity | Event | Publication or silence |
| --- | --- | --- | --- | --- |
| 09-16 00:00 | транспорт | поход за небольшим перекусом | 2026-09-15T19:00:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 00:08 | «Минимаркет 24» | небольшой перекус | 2026-09-15T19:08:00.000000Z: arrive (arrival); current: shop | offline publication |
| 09-16 00:20 | «Минимаркет 24» | небольшой перекус | Recorded at 09-16 00:18: Ранее купила жвачку, теперь взяла её из своего запаса. Чуть приятнее, но голод не прошёл. | offline publication |
| 09-16 00:30 | транспорт | дорога домой | 2026-09-15T19:30:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 00:38 | дом | отдых дома | 2026-09-15T19:38:00.000000Z: home (arrival); current: rest | offline publication |
| 09-16 00:44 | дом | отдых дома | Recorded at 09-16 00:13: Оплатила покупку: жвачка, 250 тенге, место — «Минимаркет 24». Ещё не съела и не выпила. | offline publication |
| 09-16 00:52 | дом | отдых дома | Recorded at 09-16 00:52: Отложила часть свободных денег на кофеварку; тратить этот резерв на кафе не буду. | offline publication |
| 09-16 01:01 | дом | отдых дома | Recorded at 09-16 00:46: Прикинула, сколько ещё нужно на замену кофеварки; покупка не произойдёт сама собой. | offline publication |
| 09-16 01:20 | дом | отдых дома | Recorded at 09-16 00:00: Сейчас отдыхаю дома. | offline publication |
| 09-16 01:24 | дом | подготовка ко сну | 2026-09-15T20:24:50.038875Z: wind_down (bedtime); current: wind_down | offline publication |
| 09-16 01:36 | дом | сон | — | sleep |
| 09-16 07:25 | дом | чистка зубов и умывание | Проснулась; начинается новый день. | offline publication |
| 09-16 07:30 | дом | сбор рюкзака | — | cadence_or_no_event |
| 09-16 07:35 | дом | макияж | — | cadence_or_no_event |
| 09-16 07:43 | дом | макияж | Recorded at 09-16 07:35: макияж | offline publication |
| 09-16 07:47 | дом | завтрак | — | cadence_or_no_event |
| 09-16 07:52 | дом | завтрак | Recorded at 09-16 07:47: Наступило запланированное время поесть; ещё выбираю еду и не закончила приём пищи. | offline publication |
| 09-16 08:02 | дом | завтрак | Recorded at 09-16 07:25: Давно не ела, голод начинает раздражать и мешать сосредоточиться. | offline publication |
| 09-16 08:07 | дом | отдых дома | — | cadence_or_no_event |
| 09-16 08:14 | дом | отдых дома | Recorded at 09-16 08:07: Сейчас отдыхаю дома. | offline publication |
| 09-16 08:15 | транспорт | дорога пешком | 2026-09-16T03:15:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 08:20 | остановка у дома | ожидание автобуса на остановке | 2026-09-16T03:20:00.000000Z: arrive (arrival); current: bus_wait | offline publication |
| 09-16 08:23 | транспорт | дорога | 2026-09-16T03:23:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 08:45 | транспорт | дорога пешком | 2026-09-16T03:45:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 08:50 | универ | перерыв между парами | 2026-09-16T03:50:00.000000Z: arrive (arrival); current: break | offline publication |
| 09-16 08:56 | универ | перерыв между парами | Recorded at 09-16 08:06: Поела дома готовую домашнюю еду. Порция взята из реального запаса. | offline publication |
| 09-16 09:00 | универ | пара: теория коммуникации | — | cadence_or_no_event |
| 09-16 09:09 | универ | пара: теория коммуникации | Recorded at 09-16 09:00: Сейчас пара по предмету из расписания. | offline publication |
| 09-16 09:37 | универ | пара: теория коммуникации | Recorded at 09-16 00:00: Захотелось небольшого удовольствия: жвачка. Пока ничего не покупала. | offline publication |
| 09-16 10:20 | универ | перерыв между парами | перерыв между парами | offline publication |
| 09-16 10:30 | универ | пара: статистика | — | cadence_or_no_event |
| 09-16 10:35 | универ | пара: статистика | Recorded at 09-16 10:30: Сейчас пара по предмету из расписания. | offline publication |
| 09-16 11:50 | универ | перерыв между парами | перерыв между парами | offline publication |
| 09-16 12:00 | универ | пара: медиаправо | — | cadence_or_no_event |
| 09-16 12:02 | универ | пара: медиаправо | Recorded at 09-16 12:00: Сейчас пара по предмету из расписания. | offline publication |
| 09-16 13:20 | универ | перерыв между парами | перерыв между парами | offline publication |
| 09-16 13:30 | универ | пара: практикум | Сейчас пара по предмету из расписания. | offline publication |
| 09-16 13:43 | универ | пара: практикум | Давно не ела, голод начинает раздражать и мешать сосредоточиться. | offline publication |
| 09-16 14:50 | транспорт | дорога пешком | 2026-09-16T09:50:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 14:55 | остановка у универа | ожидание автобуса на остановке | 2026-09-16T09:55:00.000000Z: arrive (arrival); current: bus_wait | offline publication |
| 09-16 14:58 | транспорт | дорога | 2026-09-16T09:58:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 15:20 | транспорт | дорога пешком | 2026-09-16T10:20:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 15:25 | дом | обед | 2026-09-16T10:25:00.000000Z: home (arrival); current: lunch | offline publication |
| 09-16 15:43 | дом | починка кофеварки | Поела дома готовую домашнюю еду. Порция взята из реального запаса. | offline publication |
| 09-16 15:58 | дом | обед | — | cadence_or_no_event |
| 09-16 16:00 | дом | рисование дома | — | cadence_or_no_event |
| 09-16 16:07 | дом | рисование дома | Recorded at 09-16 15:58: Временно починила кофеварку; замена пока не куплена. | offline publication |
| 09-16 16:13 | дом | рисование дома | Recorded at 09-16 16:00: рисование дома | offline publication |
| 09-16 16:46 | дом | рисование дома | План замены сохранила; до покупки продолжаю обходиться тем, что есть. | offline publication |
| 09-16 17:17 | дом | работа над курсовой | 2026-09-16T12:17:00.000000Z: start_study (next_activity); 2026-09-16T12:17:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-16 17:37 | дом | работа над курсовой | Recorded at 09-16 17:17: домашняя учёба | offline publication |
| 09-16 17:57 | дом | домашняя учёба | 2026-09-16T12:57:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-16 18:04 | дом | домашняя учёба | Recorded at 09-16 17:57: Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-16 18:20 | дом | домашняя учёба | Recorded at 09-16 18:16: Сделала резервную копию текущей версии курсовой; это не новая выполненная часть. | offline publication |
| 09-16 18:30 | дом | ужин | 2026-09-16T13:30:00.000000Z: pause_study (meal); current: dinner | offline publication |
| 09-16 18:42 | дом | ужин | Recorded at 09-16 18:30: Наступило запланированное время поесть; ещё выбираю еду и не закончила приём пищи. | offline publication |
| 09-16 18:54 | дом | ужин | Recorded at 09-16 18:21: Открыла сохранённую копию и убедилась, что файл читается. | offline publication |
| 09-16 19:09 | дом | ужин | Recorded at 09-16 18:02: Перед работой над курсовой проверила, где лежат файлы; резервной копии не было. | offline publication |
| 09-16 19:15 | дом | домашняя учёба | 2026-09-16T14:15:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-16 19:56 | дом | отдых дома | 2026-09-16T14:56:00.000000Z: finish_study (scheduled_end); current: rest | offline publication |
| 09-16 20:06 | дом | отдых дома | Recorded at 09-16 20:01: Для выбранного блюда не хватает продуктов; внесла недостающее в список. | offline publication |
| 09-16 20:09 | дом | просмотр серии | — | cadence_or_no_event |
| 09-16 20:54 | дом | отдых дома | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-16 21:08 | транспорт | поход за небольшим перекусом | 2026-09-16T16:08:57.768000Z: depart (departure); current: travel | offline publication |
| 09-16 21:13 | Магазин «У дома» | небольшой перекус | 2026-09-16T16:13:57.768000Z: arrive (arrival); current: shop | offline publication |
| 09-16 21:35 | транспорт | дорога домой | 2026-09-16T16:35:57.768000Z: depart (departure); current: travel | offline publication |
| 09-16 21:40 | дом | отдых дома | 2026-09-16T16:40:57.768000Z: home (arrival); current: rest | offline publication |
| 09-16 21:56 | дом | отдых дома | Recorded at 09-16 21:29: Ранее купила еду или напиток: мороженое. Теперь закончила перекус или приём пищи. | offline publication |
| 09-16 22:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-16 22:07 | дом | отдых дома | Recorded at 09-16 21:19: Оплатила покупку: мороженое, 350 тенге, место — Магазин «У дома». Ещё не съела и не выпила. | offline publication |
| 09-16 22:14 | дом | отдых дома | Recorded at 09-16 21:08: Захотелось небольшого удовольствия: мороженое. Пока ничего не покупала. | offline publication |
| 09-16 22:40 | дом | отдых дома | Давно не ела, голод начинает раздражать и мешать сосредоточиться. | offline publication |
| 09-16 22:52 | дом | отдых дома | Recorded at 09-16 22:47: Под вечер трудно сосредоточиться; решила заранее закончить шумные дела. | offline publication |
| 09-16 23:09 | дом | отдых дома | Recorded at 09-16 23:08: Подготовила одежду и рюкзак на завтра; утренних дел будет меньше. | offline publication |
| 09-16 23:27 | дом | отдых дома | Приглушила свет и спокойно провела паузу; спать пойду по сохранённому плану. | offline publication |
| 09-17 00:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 00:05 | дом | подготовка ко сну | 2026-09-16T19:05:57.953741Z: wind_down (bedtime); current: wind_down | offline publication |
| 09-17 00:17 | дом | сон | — | sleep |
| 09-17 11:24 | дом | разговор с Айкой | Проснулась; начинается новый день. | offline publication |
| 09-17 11:29 | дом | завтрак | — | cadence_or_no_event |
| 09-17 11:51 | дом | завтрак | Recorded at 09-17 11:29: Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-17 11:54 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 11:59 | дом | отдых дома | Recorded at 09-17 11:54: Сейчас отдыхаю дома. | offline publication |
| 09-17 12:13 | дом | отдых дома | Recorded at 09-17 12:02: Вспомнила обещание помочь брату; сначала проверю, свободен ли он. | offline publication |
| 09-17 12:33 | дом | отдых дома | Добавила недостающие продукты в список покупок. | offline publication |
| 09-17 12:40 | дом | отдых дома | Recorded at 09-17 12:36: Написала Тимуру: предложила встретиться, но ответа и договорённости ещё нет. | offline publication |
| 09-17 12:48 | дом | отдых дома | Recorded at 09-17 12:22: У брата секция или другие дела; помощь перенесли. | offline publication |
| 09-17 13:00 | дом | обед | — | cadence_or_no_event |
| 09-17 13:02 | дом | обед | Recorded at 09-17 13:00: Наступило запланированное время поесть; ещё выбираю еду и не закончила приём пищи. | offline publication |
| 09-17 13:08 | дом | обед | Recorded at 09-17 13:05: Тимур ответил: сейчас занят своими делами и сегодня встретиться не получится. Мне стало немного грустно. | offline publication |
| 09-17 13:27 | дом | обед | Recorded at 09-17 13:23: Из-за сильного голода поела дома и взяла две порции вместо одной. | offline publication |
| 09-17 13:35 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 13:45 | дом | отдых дома | Recorded at 09-17 13:35: Сейчас отдыхаю дома. | offline publication |
| 09-17 13:55 | дом | отдых дома | Recorded at 09-17 13:50: Вместо несостоявшейся встречи решила спокойно заняться домашними делами. Отказ не означает ссору. | offline publication |
| 09-17 14:01 | дом | отдых дома | Recorded at 09-17 11:24: Сильно проголодалась; поесть стало важнее необязательных дел. | offline publication |
| 09-17 14:05 | транспорт | поход за небольшим перекусом | 2026-09-17T09:05:00.000000Z: depart (departure); current: travel | offline publication |
| 09-17 14:16 | транспорт | поход за небольшим перекусом | Recorded at 09-17 14:05: Вернулась мыслями к разговору: планы изменились, но общение не оборвалось. Позже можно предложить другое время. | offline publication |
| 09-17 14:30 | Магазин «Студенческий» | небольшой перекус | 2026-09-17T09:30:00.000000Z: arrive (arrival); current: shop | offline publication |
| 09-17 14:45 | Магазин «Студенческий» | небольшой перекус | Recorded at 09-17 14:44: Ранее купила еду или напиток: булочка. Теперь закончила перекус или приём пищи. | offline publication |
| 09-17 14:52 | транспорт | дорога домой | 2026-09-17T09:52:00.000000Z: depart (departure); current: travel | offline publication |
| 09-17 15:17 | дом | отдых дома | 2026-09-17T10:17:00.000000Z: home (arrival); current: rest | offline publication |
| 09-17 15:30 | транспорт | дорога пешком | 2026-09-17T10:30:00.000000Z: depart (departure); current: travel | offline publication |
| 09-17 15:50 | зал | тренировка | 2026-09-17T10:50:00.000000Z: arrive (arrival); current: household_task | offline publication |
| 09-17 15:56 | зал | тренировка | Recorded at 09-17 15:50: тренировка | offline publication |
| 09-17 16:09 | зал | тренировка | Recorded at 09-17 15:25: Нашли общее свободное время и вернулись к заданию. | offline publication |
| 09-17 16:32 | зал | тренировка | Recorded at 09-17 14:35: Оплатила покупку: булочка, 300 тенге, место — Магазин «Студенческий». Ещё не съела и не выпила. | offline publication |
| 09-17 16:45 | транспорт | дорога пешком | 2026-09-17T11:45:00.000000Z: depart (departure); current: travel | offline publication |
| 09-17 17:00 | транспорт | дорога пешком | Recorded at 09-17 16:45: Сходила на тренировку по действующему абонементу. | offline publication |
| 09-17 17:05 | дом | работа над курсовой | 2026-09-17T12:05:00.000000Z: start_study (next_activity); 2026-09-17T12:05:00.000000Z: home (arrival); 2026-09-17T12:05:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-17 17:21 | дом | работа над курсовой | Recorded at 09-17 17:05: домашняя учёба | offline publication |
| 09-17 17:45 | дом | домашняя учёба | 2026-09-17T12:45:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-17 17:50 | дом | домашняя учёба | Recorded at 09-17 17:45: Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-17 18:19 | дом | домашняя учёба | Recorded at 09-17 11:24: Захотелось небольшого удовольствия: булочка. Пока ничего не покупала. | offline publication |
| 09-17 18:30 | дом | ужин | 2026-09-17T13:30:00.000000Z: pause_study (meal); current: dinner | offline publication |
| 09-17 18:49 | дом | ужин | Recorded at 09-17 18:47: Поела дома готовую домашнюю еду. Порция взята из реального запаса. | offline publication |
| 09-17 19:15 | дом | домашняя учёба | 2026-09-17T14:15:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-17 19:52 | дом | отдых дома | 2026-09-17T14:52:00.000000Z: finish_study (scheduled_end); current: rest | offline publication |
| 09-17 20:16 | дом | просмотр серии | После нескольких примеров брат объяснил решение своими словами. | offline publication |
| 09-17 21:01 | транспорт | поход за небольшим перекусом | 2026-09-17T16:01:00.000000Z: depart (departure); current: travel | offline publication |
| 09-17 21:06 | Магазин «У дома» | небольшой перекус | 2026-09-17T16:06:00.000000Z: arrive (arrival); current: shop | offline publication |
| 09-17 21:28 | транспорт | дорога домой | 2026-09-17T16:28:00.000000Z: depart (departure); current: travel | offline publication |
| 09-17 21:33 | дом | отдых дома | 2026-09-17T16:33:00.000000Z: home (arrival); current: rest | offline publication |
| 09-17 21:47 | дом | отдых дома | Recorded at 09-17 21:01: Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-17 21:56 | дом | отдых дома | Recorded at 09-17 21:14: Ранее купила жвачку, теперь взяла её из своего запаса. Чуть приятнее, но голод не прошёл. | offline publication |
| 09-17 22:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 22:05 | дом | отдых дома | Recorded at 09-17 21:11: Оплатила покупку: жвачка, 200 тенге, место — Магазин «У дома». Ещё не съела и не выпила. | offline publication |
| 09-17 22:40 | дом | отдых дома | Recorded at 09-17 22:06: Для выбранного блюда не хватает продуктов; внесла недостающее в список. | offline publication |
| 09-17 23:00 | дом | отдых дома | Recorded at 09-17 21:01: Захотелось небольшого удовольствия: жвачка. Пока ничего не покупала. | offline publication |
| 09-18 00:00 | дом | подготовка ко сну | 2026-09-17T19:00:00.000000Z: wind_down (bedtime); current: wind_down | offline publication |
| 09-18 00:08 | дом | подготовка ко сну | Recorded at 09-18 00:00: Проверила продукты и выбросила испортившееся; из них больше нельзя готовить. | offline publication |
| 09-18 00:10 | дом | сон | — | sleep |
| 09-18 09:15 | дом | чистка зубов и умывание | Проснулась; начинается новый день. | offline publication |
| 09-18 09:20 | дом | сбор рюкзака | — | cadence_or_no_event |
| 09-18 09:25 | дом | макияж | — | cadence_or_no_event |
| 09-18 09:37 | дом | завтрак | — | cadence_or_no_event |
| 09-18 09:42 | дом | завтрак | Recorded at 09-18 09:37: Наступило запланированное время поесть; ещё выбираю еду и не закончила приём пищи. | offline publication |
| 09-18 09:57 | дом | отдых дома | — | cadence_or_no_event |
| 09-18 10:00 | дом | отдых дома | Recorded at 09-18 09:57: Сейчас отдыхаю дома. | offline publication |
| 09-18 10:05 | транспорт | дорога пешком | 2026-09-18T05:05:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 10:10 | остановка у дома | ожидание автобуса на остановке | 2026-09-18T05:10:00.000000Z: arrive (arrival); current: bus_wait | offline publication |
| 09-18 10:13 | транспорт | дорога | 2026-09-18T05:13:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 10:25 | транспорт | дорога | Recorded at 09-18 09:37: Отложила приём пищи ради отдыха или другого занятия. Пока не поела, голод сохранится. | offline publication |
| 09-18 10:35 | транспорт | дорога пешком | 2026-09-18T05:35:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 10:40 | универ | разговор с Айкой | 2026-09-18T05:40:00.000000Z: arrive (arrival); current: household_task | offline publication |
| 09-18 10:47 | универ | разговор с Айкой | Recorded at 09-18 10:40: перерыв между парами | offline publication |
| 09-18 10:50 | универ | пара: философия | — | cadence_or_no_event |
| 09-18 11:07 | универ | пара: философия | Recorded at 09-18 10:50: Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-18 11:21 | универ | пара: философия | Recorded at 09-18 10:50: Сейчас пара по предмету из расписания. | offline publication |
| 09-18 11:51 | универ | пара: философия | Сильно проголодалась и отложила прогулку перед обедом. После пар направлюсь сразу поесть в уже выбранное заведение. | offline publication |
| 09-18 12:07 | универ | пара: философия | Recorded at 09-18 09:15: Давно не ела, голод начинает раздражать и мешать сосредоточиться. | offline publication |
| 09-18 12:10 | универ | перерыв между парами | — | cadence_or_no_event |
| 09-18 12:20 | универ | пара: английский | — | cadence_or_no_event |
| 09-18 12:28 | универ | пара: английский | Recorded at 09-18 12:20: Сейчас пара по предмету из расписания. | offline publication |
| 09-18 12:54 | универ | пара: английский | Recorded at 09-18 09:15: Захотелось небольшого удовольствия: банан. Пока ничего не покупала. | offline publication |
| 09-18 13:15 | универ | пара: английский | Сильно проголодалась; поесть стало важнее необязательных дел. | offline publication |
| 09-18 13:40 | универ | перерыв между парами | перерыв между парами | offline publication |
| 09-18 13:50 | универ | пара: практикум | — | cadence_or_no_event |
| 09-18 13:50 | универ | пара: практикум | Recorded at 09-18 13:50: Сейчас пара по предмету из расписания. | offline publication |
| 09-18 15:10 | транспорт | дорога за едой | 2026-09-18T10:10:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 15:18 | «Бургерная на углу» | обед в заведении | 2026-09-18T10:18:00.000000Z: arrive (arrival); current: dining | offline publication |
| 09-18 15:33 | «Бургерная на углу» | обед в заведении | Recorded at 09-18 15:25: Оплатила покупку: бургер, 1600 тенге, место — «Бургерная на углу». Ещё не съела и не выпила. | offline publication |
| 09-18 15:46 | «Бургерная на углу» | обед в заведении | Recorded at 09-18 15:41: Ранее купила еду или напиток: бургер. Теперь закончила перекус или приём пищи. | offline publication |
| 09-18 15:58 | транспорт | дорога домой | 2026-09-18T10:58:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 16:16 | транспорт | дорога за едой | 2026-09-18T11:16:00.000000Z: home (arrival); 2026-09-18T11:16:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 16:26 | магазин | выбор и покупка продуктов | 2026-09-18T11:26:00.000000Z: arrive (arrival); current: household_task | offline publication |
| 09-18 16:32 | магазин | выбор и покупка продуктов | Recorded at 09-18 16:26: покупка продуктов для домашней еды | offline publication |
| 09-18 16:51 | магазин | покупка продуктов для домашней еды | — | cadence_or_no_event |
| 09-18 16:54 | магазин | покупка продуктов для домашней еды | Recorded at 09-18 16:51: Купила ингредиенты по списку. Это продукты для готовки, а не уже съеденный ужин. | offline publication |
| 09-18 16:56 | транспорт | дорога домой | 2026-09-18T11:56:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 17:06 | дом | готовка и домашний приём пищи | 2026-09-18T12:06:00.000000Z: home (arrival); current: cooking | offline publication |
| 09-18 17:26 | дом | готовка и домашний приём пищи | Recorded at 09-18 17:24: Домашняя еда приготовлена из имеющихся ингредиентов; теперь можно поесть. | offline publication |
| 09-18 17:48 | дом | приготовление еды | — | cadence_or_no_event |
| 09-18 17:52 | дом | приготовление еды | Recorded at 09-18 17:48: Из-за сильного голода поела дома и взяла две порции вместо одной. | offline publication |
| 09-18 18:11 | дом | работа над курсовой | 2026-09-18T13:11:00.000000Z: start_study (next_activity); 2026-09-18T13:11:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-18 18:30 | дом | разговор с мамой | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-18 18:45 | дом | бытовое дело | — | cadence_or_no_event |
| 09-18 18:51 | дом | бытовое дело | Recorded at 09-18 18:45: Мама спокойно помогла деньгами. Перевод уже получен. | offline publication |
| 09-18 19:00 | дом | ужин | — | cadence_or_no_event |
| 09-18 19:10 | дом | ужин | Recorded at 09-18 19:00: Кофеварка снова потекла после временной починки. Нужна замена. | offline publication |
| 09-18 19:15 | дом | домашняя учёба | 2026-09-18T14:15:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-18 19:54 | дом | домашняя учёба | Recorded at 09-18 18:30: Сейчас ещё сыта после недавней еды; второй обед или ужин подряд не нужен. | offline publication |
| 09-18 20:09 | дом | приготовление чая | 2026-09-18T15:09:47.628000Z: pause_study (planned_break); current: tea_prepare | offline publication |
| 09-18 20:13 | дом | перерыв на чай | — | cadence_or_no_event |
| 09-18 20:15 | дом | перерыв на чай | Recorded at 09-18 20:13: перерыв на чай | offline publication |
| 09-18 20:31 | дом | домашняя учёба | 2026-09-18T15:31:47.628000Z: resume_study (resume); current: study | offline publication |
| 09-18 21:15 | дом | просмотр серии | 2026-09-18T16:15:00.000000Z: finish_study (scheduled_end); current: household_task | offline publication |
| 09-18 21:35 | дом | просмотр серии | Recorded at 09-18 21:15: Сейчас отдыхаю дома. | offline publication |
| 09-18 22:00 | дом | отдых дома | Recorded evening retrospective | offline publication |
| 09-18 22:16 | дом | отдых дома | Recorded at 09-18 22:00: Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-18 22:29 | дом | отдых дома | Recorded at 09-18 22:25: Убрала готовую еду и отметила срок хранения. | offline publication |
| 09-18 22:37 | дом | отдых дома | Recorded at 09-18 22:32: Проверила продукты: для риса с овощами всё есть, начинаю готовить. | offline publication |
| 09-18 22:45 | дом | отдых дома | Recorded at 09-18 22:19: Омлет приготовлен из записанных ингредиентов; замена, если она понадобилась, учтена. | offline publication |
| 09-18 22:56 | дом | отдых дома | Recorded at 09-18 22:04: Проверила ингредиенты для омлета, включая допустимую замену молока овощами. | offline publication |
| 09-18 23:12 | дом | отдых дома | Recorded at 09-18 23:08: Вымыла посуду после готовки и освободила стол. | offline publication |
| 09-18 23:24 | дом | отдых дома | Recorded at 09-18 23:00: Приготовила рис с овощами; ингредиенты израсходованы, готовые порции стоят в холодильнике. | offline publication |
| 09-18 23:33 | дом | отдых дома | Давно не ела, голод начинает раздражать и мешать сосредоточиться. | offline publication |

## Alternative outcomes from the same 1,000 KZT shortfall fixture

| Outcome | Cash after | PAD after | Economize | Recorded facts | Pending tasks |
| --- | --- | --- | --- | --- | --- |
| help | 14160 | 0.1804, -0.0376, 0.1500 | False | Мама спокойно помогла деньгами. Перевод уже получен. | coursework, coffee_repair, gym, series |
| delay | 1000 | -0.1196, 0.0623, -0.1200 | True | Мама сейчас занята. Разговор и возможный перевод отложены, денег пока не получила. | mother_followup, coursework, coffee_repair, gym, series |
| refusal | 1000 | -0.2196, 0.1022, -0.1800 | True | Мама отказала в дополнительной помощи. Нужно пересмотреть расходы и поискать другое решение. | dasha_loan, coursework, coffee_repair, gym, series |
| lecture | 14160 | -0.0796, 0.0822, 0.0600 | False | Мама перевела деньги, но разговор сопровождался нотациями. Есть и облегчение, и обида. | coursework, coffee_repair, gym, series |
