# Approved life calibration

Status: the owner approved the complete table in the conversation following
commit 226e057. The amounts, opening state, payment dates, spending rules and
loan terms below are implemented in the autonomous life engine. Deployment to
the running installation is separate. All amounts are integer tenge. Prices are
simulation parameters, not live quotes.

## Evidence reviewed on 2026-09-16

- [Almaty Metro payment page](https://metroalmaty.kz/ru/payment): KZT 120 per trip.
- [OG Sushi Almaty menu](https://ogsushi.kz/ogsushi/product/489-kapuchino):
  cappuccino at KZT 1,200. This is one menu, not a city-wide average.
- [NetBazar Almaty](https://net-bazar.kz/p132531336-kofevarka-kapelnaya-konka.html):
  a basic Konka drip coffee maker at KZT 15,990.
- [Krisha apartment listing](https://krisha.kz/a/show/1015393815): a furnished
  one-room apartment on Rozybakiyev Street at KZT 250,000 per month. The listing
  is archived; it is a scale reference, not evidence of current availability or
  the market average. A half share of KZT 125,000 is an approved story parameter.

The remaining numbers below are approved authoring parameters. In particular,
the grocery basket, family means, utility share, and loan terms are not sourced
facts. The owner's approval makes them simulation defaults, not market claims.

## Opening state and cash flows

| Parameter | Approved value | Timing and rule |
| --- | ---: | --- |
| Spendable opening balance | 22,000 | Once, on first initialization; no implicit past transactions |
| Opening savings | 0 | No hidden emergency fund |
| Opening debt / pending transfers | 0 | No invented prior borrowing |
| Scholarship | 52,000 | Monthly on day 25, as already configured |
| Parents' living allowance | 45,000 | Monthly on day 5, as already configured |
| Parents' housing contribution | 135,000 | Monthly on day 5; restricted to rent and utilities |
| Mika's rent share | 125,000 | Monthly on day 5; Dasha pays her own half separately |
| Mika's utilities / home internet share | 10,000 | Monthly on day 5; fixed simulation amount |
| Mobile service | 3,500 | Monthly on day 6 |
| Household / hygiene budget | 2,500 | Purchases charged individually; this is a planning allowance |
| Opening pantry | 8 portions | Separate inventory; do not charge for an invented opening purchase |

The opening housing bill is considered covered. Only bills due after the opening
timestamp are scheduled. Starting the application on another day must never
invent a missed payment or replay the previous month's income. On an existing
installation, imported balances override opening defaults.

Housing support and living money use separate accounts. Mika cannot spend the
rent reserve at a cafe. Parents' total regular contribution is KZT 180,000 per
month under this calibration. Their ability and willingness to make additional
transfers are separate persistent state.

## Prices and consumption

| Item | Approved value, KZT | Accounting rule |
| --- | ---: | --- |
| Public transport | 120 / boarding | Charge actual boardings, including transfers |
| Home food portion | 550 | Consume inventory; charge money when buying ingredients |
| Grocery basket | 4,400 | Buys the configured ingredient basket; cooking creates portions |
| University lunch | 1,000 | Only when selected and bought at university |
| Simple snack | 500 | Optional purchase, never an automatic daily deduction |
| Cafe coffee | 1,200 | A discretionary expense |
| Cafe coffee and pastry | 2,000 | One combined purchase, not an additional coffee charge |
| Food delivery | 2,500 | Includes delivery cost in the simulation |
| Cinema / paid leisure | 3,000 | Optional; reserve money before committing |
| Coffee-maker replacement | 15,990 | Save toward purchase; no automatic replacement on stage change |
| Small repair supplies | 700 | May improve the appliance temporarily; no guaranteed repair |
| Gym renewal | 15,000 | Optional purchase; existing paid subscription remains valid |

The detailed-world extension adds ingredient units, recipe yields, shelf lives
and ownership in `world_details.yaml`. Prepared meals still use portions. The
basket total remains 4,400; no second charge is made when cooking. Refunds and
compensation require explicit ledger events.

## Help and borrowing

| Parameter | Approved rule |
| --- | --- |
| Low-money warning | Unrestricted balance below 5,000, or projected essential costs exceed balance before the next confirmed income |
| Spending response | Cancel optional purchases first; keep existing obligations visible |
| Request to mother | Exact essential shortfall, capped at 15,000 per request |
| Extra family help limit | 20,000 per calendar month; availability is not a guarantee of consent |
| Busy mother | Keep request pending; retry after her stored next available time, never reroll her state during text generation |
| Calm help / lecture / refusal | Separate stored outcomes; funds move only after an actual simulated transfer |
| Loan from Dasha | At most 10,000 outstanding; no interest; consent depends on her available funds and relationship state |
| Due date | Next confirmed income date, no later than 14 days after borrowing |
| Repayment | Preserve a 3,000 essentials reserve; repay the available remainder, recording any overdue balance explicitly |
| Repeat loan | No new loan while an earlier loan is overdue |
| Failed repayment | Pending obligation and relationship consequences; no invented payment or punitive fees |

This table did not originally specify outcome probabilities or NPC financial
capacity. The subsequent instruction authorized fictional scenario authoring;
persisted daily availability and help/delay/lecture/refusal rules now live in
`life_chains.yaml` and the life engine, with deterministic branch fixtures.
Independent disposable NPC wallets and calendars now live in `world_details.yaml`;
their movements are recorded in the shared ledger. Regular housing and allowance
remain the approved separate household flows described above.

## Monthly sanity check

An illustrative month has KZT 97,000 of spendable recurring income:
52,000 scholarship + 45,000 living allowance. Housing flows cancel in their
restricted account: 135,000 in, 125,000 rent + 10,000 utilities out.

| Illustrative actual consumption | KZT |
| --- | ---: |
| Home groceries | 42,000 |
| Thirteen university lunches | 13,000 |
| Fifty transport boardings | 6,000 |
| Mobile service | 3,500 |
| Household purchases | 2,500 |
| Six cafe coffees | 7,200 |
| Two deliveries | 5,000 |
| Optional leisure | 5,000 |
| Total | 84,200 |
| Remaining for savings or unexpected costs | 12,800 |

This is a check of scale, not a monthly expense script. The simulation must
derive actual spending from completed activities. Replacing a KZT 15,990 coffee
maker can use more than the month's free balance, creating a meaningful saving
or postponement decision without requiring constant financial crisis.

## Confirmed availability rules

- Life posts and ordinary chat are possible while awake, including classes and
  travel. Busy periods reduce frequency and length; rest permits longer replies.
- Sleep forbids sending persona messages. Incoming messages remain durable and
  unread until waking; the model receives actual received and seen timestamps.
- Learning actions and learning posts require a home study activity. Admission
  is checked before enqueue, execution, and committing results.
- Occasional long evening retrospectives use recorded events and their linked
  mood changes. Unlinked mood drift must not be attributed to invented events.
- A daily post target is still awaiting approval; no number is made mandatory.
