# Autonomous life simulation

Offline factual renderer. No models, token estimates or Telegram delivery.
Times use Asia/Almaty. Temporary databases are discarded after verification.
A midpoint runtime restart preserves the day, resource state and receipts.

Days: 5. Final cash: 16420 KZT; debt: 0 KZT.
Storage counts: `{"life_days": 5, "life_events": 99, "life_tasks": 23, "money_ledger": 6, "mood": 34, "outbox": 38}`.

## Timeline

| Time | Place | Activity | Event | Publication or silence |
| --- | --- | --- | --- | --- |
| 09-16 00:00 | дом | отдых дома | Сейчас отдыхаю дома. | offline publication |
| 09-16 01:40 | дом | сон | — | sleep |
| 09-16 08:00 | дом | завтрак | Проснулась; начинается новый день. | offline publication |
| 09-16 08:20 | транспорт | дорога | — | cadence_or_no_event |
| 09-16 09:00 | универ | пара: теория коммуникации | — | cadence_or_no_event |
| 09-16 09:20 | универ | пара: теория коммуникации | Сейчас пара по предмету из расписания. | offline publication |
| 09-16 10:20 | универ | перерыв между парами | — | cadence_or_no_event |
| 09-16 10:40 | универ | пара: статистика | — | cadence_or_no_event |
| 09-16 11:40 | универ | пара: статистика | Сейчас пара по предмету из расписания. | offline publication |
| 09-16 12:00 | универ | пара: медиаправо | — | cadence_or_no_event |
| 09-16 13:20 | универ | перерыв между парами | — | cadence_or_no_event |
| 09-16 13:40 | универ | пара: практикум | — | cadence_or_no_event |
| 09-16 14:00 | универ | пара: практикум | Сейчас пара по предмету из расписания. | offline publication |
| 09-16 15:00 | транспорт | дорога | — | cadence_or_no_event |
| 09-16 15:40 | дом | починка кофеварки | — | cadence_or_no_event |
| 09-16 16:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-16 17:00 | дом | работа над курсовой | Временно починила кофеварку; замена пока не куплена. | offline publication |
| 09-16 17:40 | дом | домашняя учёба | — | cadence_or_no_event |
| 09-16 18:40 | дом | ужин | — | cadence_or_no_event |
| 09-16 19:00 | дом | ужин | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-16 19:20 | дом | домашняя учёба | — | cadence_or_no_event |
| 09-16 20:00 | дом | домашняя учёба | домашняя учёба | offline publication |
| 09-16 22:00 | дом | просмотр серии | Сейчас отдыхаю дома. | offline publication |
| 09-16 23:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 00:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 00:20 | дом | сон | — | sleep |
| 09-17 11:40 | дом | разговор с Айкой | Проснулась; начинается новый день. | offline publication |
| 09-17 12:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 13:00 | дом | обед | — | cadence_or_no_event |
| 09-17 13:40 | дом | отдых дома | — | cadence_or_no_event |
| 09-17 14:40 | дом | отдых дома | Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-17 15:40 | дом | отдых дома | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-17 17:00 | дом | работа над курсовой | домашняя учёба | offline publication |
| 09-17 17:40 | дом | домашняя учёба | — | cadence_or_no_event |
| 09-17 18:40 | дом | ужин | — | cadence_or_no_event |
| 09-17 19:20 | дом | домашняя учёба | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-17 22:00 | дом | просмотр серии | — | cadence_or_no_event |
| 09-17 22:20 | дом | просмотр серии | Сейчас отдыхаю дома. | offline publication |
| 09-17 23:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-18 00:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-18 00:20 | дом | сон | — | sleep |
| 09-18 09:40 | дом | завтрак | Проснулась; начинается новый день. | offline publication |
| 09-18 10:00 | дом | сборы | — | cadence_or_no_event |
| 09-18 10:20 | транспорт | дорога | — | cadence_or_no_event |
| 09-18 10:40 | универ | перерыв между парами | — | cadence_or_no_event |
| 09-18 11:00 | универ | пара: философия | — | cadence_or_no_event |
| 09-18 11:20 | универ | пара: философия | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-18 12:20 | универ | пара: английский | — | cadence_or_no_event |
| 09-18 13:40 | универ | разговор с Айкой | перерыв между парами | offline publication |
| 09-18 14:00 | универ | пара: практикум | — | cadence_or_no_event |
| 09-18 15:20 | транспорт | дорога | — | cadence_or_no_event |
| 09-18 16:00 | дом | бытовое дело | Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-18 16:20 | транспорт | дорога пешком | — | cadence_or_no_event |
| 09-18 16:40 | магазин | выбор и покупка продуктов | — | cadence_or_no_event |
| 09-18 17:00 | транспорт | дорога пешком | — | cadence_or_no_event |
| 09-18 17:20 | дом | работа над курсовой | — | cadence_or_no_event |
| 09-18 18:00 | дом | домашняя учёба | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-18 18:40 | дом | ужин | — | cadence_or_no_event |
| 09-18 19:20 | дом | домашняя учёба | — | cadence_or_no_event |
| 09-18 20:40 | дом | домашняя учёба | Купила продукты и принесла их в корзине; готовить буду после возвращения домой. | offline publication |
| 09-18 22:00 | дом | просмотр серии | — | cadence_or_no_event |
| 09-18 23:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-18 23:40 | дом | отдых дома | Recorded evening retrospective | offline publication |
| 09-19 00:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-19 01:00 | дом | отдых дома | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-19 01:40 | дом | сон | — | sleep |
| 09-19 09:40 | дом | завтрак | Проснулась; начинается новый день. | offline publication |
| 09-19 10:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-19 11:00 | дом | разговор с Айкой | Кофеварка снова потекла после временной починки. Нужна замена. | offline publication |
| 09-19 11:20 | дом | отдых дома | — | cadence_or_no_event |
| 09-19 13:00 | дом | обед | — | cadence_or_no_event |
| 09-19 13:40 | дом | отдых дома | Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-19 15:20 | дом | отдых дома | Сейчас отдыхаю дома. | offline publication |
| 09-19 17:00 | дом | работа над курсовой | домашняя учёба | offline publication |
| 09-19 17:40 | дом | домашняя учёба | — | cadence_or_no_event |
| 09-19 18:40 | дом | ужин | — | cadence_or_no_event |
| 09-19 19:20 | дом | домашняя учёба | — | cadence_or_no_event |
| 09-19 19:40 | дом | домашняя учёба | Завершила очередную часть курсовой; оставшиеся части ещё предстоит сделать. | offline publication |
| 09-19 22:00 | дом | просмотр серии | — | cadence_or_no_event |
| 09-19 22:20 | дом | просмотр серии | Сейчас отдыхаю дома. | offline publication |
| 09-19 23:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 00:00 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 00:40 | дом | сон | — | sleep |
| 09-20 10:20 | дом | завтрак | Проснулась; начинается новый день. | offline publication |
| 09-20 10:40 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 11:00 | дом | разговор с Айкой | — | cadence_or_no_event |
| 09-20 11:20 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 12:00 | дом | отдых дома | Обсудила с Айкой уже просмотренную серию, без новых выдуманных эпизодов. | offline publication |
| 09-20 13:00 | дом | обед | — | cadence_or_no_event |
| 09-20 13:20 | дом | обед | Посмотрела следующую серию; номер просмотренной серии записан в событии. | offline publication |
| 09-20 13:40 | дом | отдых дома | — | cadence_or_no_event |
| 09-20 14:20 | дом | отдых дома | Сейчас отдыхаю дома. | offline publication |
| 09-20 15:40 | дом | отдых дома | Приготовила и съела домашнюю еду из имеющихся продуктов. | offline publication |
| 09-20 17:00 | дом | работа над курсовой | домашняя учёба | offline publication |
| 09-20 17:40 | дом | сдача курсовой | — | cadence_or_no_event |
| 09-20 18:00 | дом | домашняя учёба | — | cadence_or_no_event |
| 09-20 18:40 | дом | помощь брату | — | cadence_or_no_event |
| 09-20 19:20 | дом | домашняя учёба | — | cadence_or_no_event |
| 09-20 20:00 | дом | домашняя учёба | Позвонила брату и разобрала с ним задание; он смог объяснить решение сам. | offline publication |
| 09-20 22:00 | дом | просмотр серии | — | cadence_or_no_event |
| 09-20 22:40 | дом | просмотр серии | Recorded evening retrospective | offline publication |
| 09-20 23:00 | дом | отдых дома | — | cadence_or_no_event |

## Alternative outcomes from the same 1,000 KZT shortfall fixture

| Outcome | Cash after | PAD after | Economize | Recorded facts | Pending tasks |
| --- | --- | --- | --- | --- | --- |
| help | 14160 | 0.1804, -0.0376, 0.1500 | False | Мама спокойно помогла деньгами. Перевод уже получен. | coursework, coffee_repair, gym, series |
| delay | 1000 | -0.1196, 0.0623, -0.1200 | True | Мама сейчас занята. Разговор и возможный перевод отложены, денег пока не получила. | mother_followup, coursework, coffee_repair, gym, series |
| refusal | 1000 | -0.2196, 0.1022, -0.1800 | True | Мама отказала в дополнительной помощи. Нужно пересмотреть расходы и поискать другое решение. | dasha_loan, coursework, coffee_repair, gym, series |
| lecture | 14160 | -0.0796, 0.0822, 0.0600 | False | Мама перевела деньги, но разговор сопровождался нотациями. Есть и облегчение, и обида. | coursework, coffee_repair, gym, series |
