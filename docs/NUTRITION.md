# Hunger, meal choices and small treats

The configuration is [nutrition.yaml](../config/nutrition.yaml). Venue names,
prices and travel times are fictional simulation parameters. The scale describes
behavior, not calories or medical advice.

## Body clock and optional meals

Hunger is **0 when sated and 100 when very hungry**. It increases by seven points
per awake hour, two during saved sleep, and two extra during active travel,
walking or exercise. The saved anchor moves only after actual consumption; the
clock itself does not rewrite resources every minute. Existing installations
receive an initial 25-point observation, without fabricating prior meals.

| Hunger | Meaning and consequence |
| --- | --- |
| Below 30 | Sated; another scheduled meal may be skipped |
| 30–39 | Wants food, but a walk or conversation may take priority |
| 40–74 | Hunger becomes irritating and reduces productivity |
| 75–100 | Eating takes precedence over an optional walk |

Breakfast, lunch and dinner are three ordinary opportunities, not unconditional
commands. Dinner normally starts at 18:30. The actual appetite depends on the
previous meal. Saved choices allow postponement or skipping. Fatigue and sleep
debt raise the chance of skipping breakfast or dinner. Breakfast also belongs to
the optional morning preparation: extra sleep can remove it while retaining
hygiene, packing and actual travel time.

A normal class-day alarm leaves time for the configured morning routine,
including breakfast, the commute and the arrival buffer. A high-debt morning
can exchange optional preparation for more sleep; this changes the saved plan,
not the hunger calculation. Missing breakfast never counts as having eaten.

If lunch was delayed, hunger at or above 55 can bring dinner forward from 17:00
when Mika is home and has prepared food and enough time. At 65 or above she takes
two available portions. Normal home food removes 60 points; a large home meal
removes 90. It cannot consume two portions when only one exists. A recent meal
and its hunger reduction prevent a second full dinner just to satisfy the clock.

Home food is the most filling option. Venue meals remove 28–55 points. Small
treats remove 4–16 points; gum, water, tea and coffee remove none. A treat produces
a small positive mood event, while a full meal uses the existing meal event.
Irritability is a saved threshold consequence, not a new penalty at every poll.
All mood changes still pass through the existing mood service.

When hunger rises at home and no prepared food remains, available ingredients
lead to cooking. Otherwise, an affordable grocery trip precedes cooking and
eating. These actions consume actual time and stock. They can replace adjacent
flexible home activities, including study, but cannot cross sleep or a fixed
departure. A purchase fills ingredient stock; cooking creates portions; only
completed eating reduces hunger.
An immediately due eating step keeps its place in the sequence before unrelated
household tasks are scheduled; the planner cannot insert another cooking job
between preparing this meal and eating it.

## Routes and menus

| Venue | Type | Example prices, KZT |
| --- | --- | --- |
| Тёплая тарелка | Cafe | Soup 1,100; rice bowl 1,400 |
| Дворик | Cafe | Pasta 2,300; manty 2,500 |
| Пауза | Coffee shop | Coffee 1,200; sandwich 1,700 |
| Зёрна | Coffee shop | Coffee 1,500; sandwich 2,100 |
| Самал | Restaurant | Pasta 3,900; fish plate 4,800 |
| Уют | Restaurant | Soup 2,400; rice bowl 2,900 |
| Быстрый лаваш | Fast food | Wrap 1,200; fries 650 |
| Бургерная на углу | Fast food | Burger 1,600; ice cream 550 |
| У дома | Shop | Chocolate 450; gum 200; yogurt 400 |
| Студенческий | Shop | Sandwich 800; pastry 300; water 180 |
| Минимаркет 24 | Shop | Chips 650; nuts 700; ice cream 450 |
| Фруктовая лавка | Shop | Apple 200; banana 250; nuts 600 |
| Сладкая полка | Shop | Pastry 550; ice cream 500; chocolate 650 |

Menus also contain drinks, fruit, cookies and other small items. Each venue has
its own opening hours and home/university/park travel times. The plan can go
directly from classes to an affordable venue, sometimes with a walk first, or
return home for lunch. A home meal can lead to a saved decision to stay home and
cancel an optional outing. Fixed university and sleep boundaries remain intact.
If breakfast was skipped and actual hunger becomes urgent during classes, the
saved park detour is removed: Mika goes directly to the already selected venue
after the last class. The venue and meal duration are preserved, and the change
has its own saved decision rather than a new random lunch selection.

Normal purchases retain a KZT 3,000 reserve. Optional treats use at most 15% of
cash above that reserve. Very high hunger permits spending the available cash on
a meal. Menus are selected for satiety and affordability and rechecked at payment.
The model never picks an order, changes a price or invents a paid receipt.

## Cravings and receipts

A seeded decision is saved once per three-hour awake window. A craving can exist
without hunger and remains pending for up to six hours. The same product has a
six-hour repeat cooldown. An unaffordable craving produces a declined decision
and a small disappointment; it does not create a purchase. A suitable free home
interval may become an actual trip to an open shop, with both journeys saved.

Selection -> queue/payment -> owned, expiring food -> timed consumption -> hunger
and mood consequence. A purchase alone does not improve satiety. If the venue
activity ends before consumption, the purchased item remains carried stock and
can be consumed in a later suitable interval. Expired food is discarded. A
restart cannot charge the same order or consume its stock twice.

Meal outcomes, carried stock, craving state and verbal hunger are shared with
chat and the operator state document. Generated prose remains subject to the
same current-activity and delivery checks as the rest of the world.
