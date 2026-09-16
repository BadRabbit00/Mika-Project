# Autonomous life simulation

Offline factual renderer. No models, token estimates or Telegram delivery.
Times use Asia/Almaty. Temporary databases are discarded after verification.
A midpoint runtime restart preserves the day, resource state and receipts.

Days: 5. Final cash: 16420 KZT; debt: 0 KZT.
Storage counts: `{"activity_transitions": 71, "life_breaks": 9, "life_days": 5, "life_events": 194, "life_tasks": 23, "money_ledger": 6, "mood": 42, "outbox": 92}`.

## Timeline

| Time | Place | Activity | Event | Publication or silence |
| --- | --- | --- | --- | --- |
| 09-16 00:00 | дом | отдых дома | Сейчас отдыхаю дома. | offline publication |
| 09-16 01:24 | дом | подготовка ко сну | 2026-09-15T20:24:50.038875Z: wind_down (bedtime); current: wind_down | offline publication |
| 09-16 01:36 | дом | сон | — | sleep |
| 09-16 07:50 | дом | завтрак | Проснулась; начинается новый день. | offline publication |
| 09-16 08:05 | дом | сборы | — | cadence_or_no_event |
| 09-16 08:15 | транспорт | дорога | 2026-09-16T03:15:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 08:50 | универ | перерыв между парами | 2026-09-16T03:50:00.000000Z: arrive (arrival); current: break | offline publication |
| 09-16 09:00 | универ | пара: теория коммуникации | — | cadence_or_no_event |
| 09-16 10:20 | универ | перерыв между парами | перерыв между парами | offline publication |
| 09-16 10:30 | универ | пара: статистика | — | cadence_or_no_event |
| 09-16 11:30 | универ | пара: статистика | Сейчас пара по предмету из расписания. | offline publication |
| 09-16 11:50 | универ | перерыв между парами | — | cadence_or_no_event |
| 09-16 12:00 | универ | пара: медиаправо | — | cadence_or_no_event |
| 09-16 13:20 | универ | перерыв между парами | — | cadence_or_no_event |
| 09-16 13:30 | универ | пара: практикум | — | cadence_or_no_event |
| 09-16 13:50 | универ | пара: практикум | Сейчас пара по предмету из расписания. | offline publication |
| 09-16 14:50 | транспорт | дорога | 2026-09-16T09:50:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 15:25 | дом | починка кофеварки | 2026-09-16T10:25:00.000000Z: home (arrival); current: household_task | offline publication |
| 09-16 15:40 | дом | обед | — | cadence_or_no_event |
| 09-16 16:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-16 17:00 | дом | работа над курсовой | 2026-09-16T12:00:00.000000Z: start_study (next_activity); 2026-09-16T12:00:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-16 17:20 | дом | работа над курсовой | Временно починила кофеварку; замена пока не куплена. | offline publication |
| 09-16 17:40 | дом | домашняя учёба | 2026-09-16T12:40:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-16 18:30 | дом | ужин | 2026-09-16T13:30:00.000000Z: pause_study (meal); current: dinner | offline publication |
| 09-16 19:15 | дом | домашняя учёба | 2026-09-16T14:15:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-16 20:15 | транспорт | дорога пешком | 2026-09-16T15:15:00.000000Z: pause_study (planned_break); 2026-09-16T15:15:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 20:35 | парк | прогулка | 2026-09-16T15:35:00.000000Z: arrive (arrival); current: walk | offline publication |
| 09-16 21:00 | транспорт | дорога пешком | 2026-09-16T16:00:00.000000Z: depart (departure); current: travel | offline publication |
| 09-16 21:20 | дом | домашняя учёба | 2026-09-16T16:20:00.000000Z: resume_study (resume); 2026-09-16T16:20:00.000000Z: home (arrival); current: study | offline publication |
| 09-16 22:00 | дом | просмотр серии | 2026-09-16T17:00:00.000000Z: finish_study (scheduled_end); current: household_task | offline publication |
| 09-16 22:45 | дом | отдых дома | — | cadence_or_no_event |
| 09-16 23:25 | дом | отдых дома | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-17 00:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 00:05 | дом | подготовка ко сну | 2026-09-16T19:05:57.953741Z: wind_down (bedtime); current: wind_down | offline publication |
| 09-17 00:17 | дом | сон | — | sleep |
| 09-17 11:24 | дом | разговор с Айкой | Проснулась; начинается новый день. | offline publication |
| 09-17 11:39 | дом | завтрак | — | cadence_or_no_event |
| 09-17 11:49 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 13:00 | дом | обед | — | cadence_or_no_event |
| 09-17 13:35 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 14:35 | дом | отдых дома | Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-17 15:35 | дом | отдых дома | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-17 16:35 | дом | отдых дома | Сейчас отдыхаю дома. | offline publication |
| 09-17 17:00 | дом | работа над курсовой | 2026-09-17T12:00:00.000000Z: start_study (next_activity); 2026-09-17T12:00:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-17 17:40 | дом | домашняя учёба | 2026-09-17T12:40:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-17 18:00 | дом | домашняя учёба | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-17 18:30 | дом | ужин | 2026-09-17T13:30:00.000000Z: pause_study (meal); current: dinner | offline publication |
| 09-17 19:15 | дом | домашняя учёба | 2026-09-17T14:15:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-17 20:15 | дом | приготовление чая | 2026-09-17T15:15:00.000000Z: pause_study (planned_break); current: tea_prepare | offline publication |
| 09-17 20:18 | дом | перерыв на чай | — | cadence_or_no_event |
| 09-17 20:34 | дом | домашняя учёба | 2026-09-17T15:34:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-17 21:34 | дом | короткий отдых | 2026-09-17T16:34:00.000000Z: pause_study (planned_break); current: short_rest | offline publication |
| 09-17 21:45 | дом | домашняя учёба | 2026-09-17T16:45:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-17 22:00 | дом | просмотр серии | 2026-09-17T17:00:00.000000Z: finish_study (scheduled_end); current: household_task | offline publication |
| 09-17 22:45 | дом | отдых дома | — | cadence_or_no_event |
| 09-18 00:00 | дом | подготовка ко сну | 2026-09-17T19:00:00.000000Z: wind_down (bedtime); current: wind_down | offline publication |
| 09-18 00:10 | дом | сон | — | sleep |
| 09-18 09:40 | дом | завтрак | Проснулась; начинается новый день. | offline publication |
| 09-18 09:55 | дом | сборы | — | cadence_or_no_event |
| 09-18 10:05 | транспорт | дорога | 2026-09-18T05:05:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 10:40 | универ | перерыв между парами | 2026-09-18T05:40:00.000000Z: arrive (arrival); current: break | offline publication |
| 09-18 10:50 | универ | пара: философия | — | cadence_or_no_event |
| 09-18 12:10 | универ | разговор с Айкой | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-18 12:20 | универ | пара: английский | — | cadence_or_no_event |
| 09-18 13:40 | универ | перерыв между парами | — | cadence_or_no_event |
| 09-18 13:50 | универ | пара: практикум | — | cadence_or_no_event |
| 09-18 14:30 | универ | пара: практикум | Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-18 15:10 | транспорт | дорога | 2026-09-18T10:10:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 15:45 | дом | бытовое дело | 2026-09-18T10:45:00.000000Z: home (arrival); current: household_task | offline publication |
| 09-18 16:00 | дом | обед | — | cadence_or_no_event |
| 09-18 16:20 | транспорт | дорога пешком | 2026-09-18T11:20:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 16:30 | магазин | выбор и покупка продуктов | 2026-09-18T11:30:00.000000Z: arrive (arrival); current: household_task | offline publication |
| 09-18 16:55 | транспорт | дорога пешком | 2026-09-18T11:55:00.000000Z: depart (departure); current: travel | offline publication |
| 09-18 17:05 | дом | работа над курсовой | 2026-09-18T12:05:00.000000Z: start_study (next_activity); 2026-09-18T12:05:00.000000Z: home (arrival); 2026-09-18T12:05:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-18 17:45 | дом | домашняя учёба | 2026-09-18T12:45:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-18 18:30 | дом | ужин | 2026-09-18T13:30:00.000000Z: pause_study (meal); current: dinner | offline publication |
| 09-18 19:15 | дом | домашняя учёба | 2026-09-18T14:15:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-18 20:15 | дом | короткий отдых | 2026-09-18T15:15:00.000000Z: pause_study (planned_break); current: short_rest | offline publication |
| 09-18 20:29 | дом | домашняя учёба | 2026-09-18T15:29:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-18 21:29 | дом | приготовление чая | 2026-09-18T16:29:00.000000Z: pause_study (planned_break); current: tea_prepare | offline publication |
| 09-18 21:32 | дом | перерыв на чай | — | cadence_or_no_event |
| 09-18 21:45 | дом | домашняя учёба | 2026-09-18T16:45:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-18 22:00 | дом | просмотр серии | 2026-09-18T17:00:00.000000Z: finish_study (scheduled_end); current: household_task | offline publication |
| 09-18 22:45 | дом | отдых дома | — | cadence_or_no_event |
| 09-19 00:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-19 00:20 | дом | отдых дома | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-19 01:08 | дом | подготовка ко сну | 2026-09-18T20:08:35.075973Z: wind_down (bedtime); current: wind_down | offline publication |
| 09-19 01:20 | дом | сон | — | sleep |
| 09-19 09:24 | дом | завтрак | Проснулась; начинается новый день. | offline publication |
| 09-19 09:49 | дом | отдых дома | — | cadence_or_no_event |
| 09-19 10:29 | дом | отдых дома | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-19 10:49 | дом | разговор с Айкой | — | cadence_or_no_event |
| 09-19 11:04 | дом | отдых дома | — | cadence_or_no_event |
| 09-19 11:44 | дом | отдых дома | Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-19 13:00 | дом | обед | — | cadence_or_no_event |
| 09-19 13:20 | дом | обед | Купила продукты и принесла их в корзине; готовить буду после возвращения домой. | offline publication |
| 09-19 13:35 | дом | отдых дома | — | cadence_or_no_event |
| 09-19 14:55 | дом | отдых дома | Кофеварка снова потекла после временной починки. Нужна замена. | offline publication |
| 09-19 16:15 | дом | отдых дома | Сейчас отдыхаю дома. | offline publication |
| 09-19 17:00 | дом | работа над курсовой | 2026-09-19T12:00:00.000000Z: start_study (next_activity); 2026-09-19T12:00:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-19 17:40 | дом | домашняя учёба | 2026-09-19T12:40:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-19 18:00 | дом | домашняя учёба | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-19 18:30 | дом | ужин | 2026-09-19T13:30:00.000000Z: pause_study (meal); current: dinner | offline publication |
| 09-19 19:15 | дом | домашняя учёба | 2026-09-19T14:15:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-19 20:15 | дом | короткий отдых | 2026-09-19T15:15:00.000000Z: pause_study (planned_break); current: short_rest | offline publication |
| 09-19 20:32 | дом | домашняя учёба | 2026-09-19T15:32:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-19 21:32 | дом | короткий отдых | 2026-09-19T16:32:00.000000Z: pause_study (planned_break); current: short_rest | offline publication |
| 09-19 21:40 | дом | домашняя учёба | 2026-09-19T16:40:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-19 22:00 | дом | просмотр серии | 2026-09-19T17:00:00.000000Z: finish_study (scheduled_end); current: household_task | offline publication |
| 09-19 22:45 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 00:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 00:11 | дом | подготовка ко сну | 2026-09-19T19:11:46.695932Z: wind_down (bedtime); current: wind_down | offline publication |
| 09-20 00:23 | дом | сон | — | sleep |
| 09-20 10:08 | дом | завтрак | Проснулась; начинается новый день. | offline publication |
| 09-20 10:33 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 10:53 | дом | разговор с Айкой | — | cadence_or_no_event |
| 09-20 11:08 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 11:48 | дом | отдых дома | Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-20 13:00 | дом | обед | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-20 13:35 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 13:55 | дом | отдых дома | Сейчас отдыхаю дома. | offline publication |
| 09-20 16:15 | дом | отдых дома | Сейчас отдыхаю дома. | offline publication |
| 09-20 17:00 | дом | работа над курсовой | 2026-09-20T12:00:00.000000Z: start_study (next_activity); 2026-09-20T12:00:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-20 17:40 | дом | сдача курсовой | 2026-09-20T12:40:00.000000Z: resume_study (resume); 2026-09-20T12:40:00.000000Z: pause_study (next_activity); current: household_task | offline publication |
| 09-20 17:55 | дом | домашняя учёба | 2026-09-20T12:55:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-20 18:15 | дом | домашняя учёба | Отправила готовую курсовую; оценка ещё неизвестна, но работа сдана. | offline publication |
| 09-20 18:30 | дом | помощь брату | 2026-09-20T13:30:00.000000Z: pause_study (meal); current: household_task | offline publication |
| 09-20 18:55 | дом | ужин | — | cadence_or_no_event |
| 09-20 19:15 | дом | домашняя учёба | 2026-09-20T14:15:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-20 20:15 | дом | приготовление чая | 2026-09-20T15:15:00.000000Z: pause_study (planned_break); current: tea_prepare | offline publication |
| 09-20 20:19 | дом | перерыв на чай | — | cadence_or_no_event |
| 09-20 20:30 | дом | домашняя учёба | 2026-09-20T15:30:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-20 21:30 | дом | приготовление чая | 2026-09-20T16:30:00.000000Z: pause_study (planned_break); current: tea_prepare | offline publication |
| 09-20 21:33 | дом | перерыв на чай | — | cadence_or_no_event |
| 09-20 21:45 | дом | домашняя учёба | 2026-09-20T16:45:00.000000Z: resume_study (resume); current: study | offline publication |
| 09-20 22:00 | дом | просмотр серии | 2026-09-20T17:00:00.000000Z: finish_study (scheduled_end); current: household_task | offline publication |
| 09-20 22:45 | дом | отдых дома | — | cadence_or_no_event |

## Alternative outcomes from the same 1,000 KZT shortfall fixture

| Outcome | Cash after | PAD after | Economize | Recorded facts | Pending tasks |
| --- | --- | --- | --- | --- | --- |
| help | 14160 | 0.1804, -0.0376, 0.1500 | False | Мама спокойно помогла деньгами. Перевод уже получен. | coursework, coffee_repair, gym, series |
| delay | 1000 | -0.1196, 0.0623, -0.1200 | True | Мама сейчас занята. Разговор и возможный перевод отложены, денег пока не получила. | mother_followup, coursework, coffee_repair, gym, series |
| refusal | 1000 | -0.2196, 0.1022, -0.1800 | True | Мама отказала в дополнительной помощи. Нужно пересмотреть расходы и поискать другое решение. | dasha_loan, coursework, coffee_repair, gym, series |
| lecture | 14160 | -0.0796, 0.0822, 0.0600 | False | Мама перевела деньги, но разговор сопровождался нотациями. Есть и облегчение, и обида. | coursework, coffee_repair, gym, series |
