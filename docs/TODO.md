# Open deployment inputs

The supplied implementation decisions are applied. The remaining items require
real deployment data; fixture values cannot close them. Each code marker is
listed here. Search with:

```sh
rg -n 'TODO\(' src tests scripts docs
```

- TODO(FIRST-TOPIC-ARTICLES) — [src/catalogue.py](../src/catalogue.py), Catalogue.load.
  Supply the six manually authored first-topic files `library/ab-01.md` through
  `ab-06.md`, with the IDs, topics, and origin keys required by topics.yaml.
  Strict catalogue validation reports the exact missing IDs. Live startup allows
  an empty library so everyday life and chat can run independently. Later-topic
  shortages are reported by the curator without inventing articles.
- TODO(TELEGRAM-DEPLOYMENT) — [src/telegram_runtime.py](../src/telegram_runtime.py),
  run_telegram. Supply the actual owner, group/channel and seven topic IDs, plus
  the three named token environment variables. Then verify a post in the test
  supergroup. No live Telegram publication is claimed without those values.

## Accepted operational limits

Uncertain Telegram sends and interrupted learner effects require manual receipt
review; they are never automatically replayed. This is an approved policy, not
an unfinished implementation. JSONL is authoritative. Ordinary incoming chat and
its delivery binding are durable; command/library jobs and unflushed mirror cards
remain ephemeral. Deterministic semantic filters
are the approved first version and do not prove arbitrary factual correctness.

## Evidence

[VALIDATION.md](VALIDATION.md) records the real local-model checks, simulation,
and regression gates. [DECISIONS.md](DECISIONS.md) preserves the supplied decisions
and their later corrections. Resolved markers are removed from maintained code
and documentation.

## Autonomous-life expansion

The connected life runtime and its verification commands are documented in
[AUTONOMOUS_LIFE_WORK.md](AUTONOMOUS_LIFE_WORK.md). Remaining authoring scope is
listed in [LIFE_GAPS.md](LIFE_GAPS.md), and the owner-approved financial parameters
are recorded in [LIFE_CALIBRATION.md](LIFE_CALIBRATION.md). These documents
distinguish tested runtime behavior from authoring extensions and deployment.
