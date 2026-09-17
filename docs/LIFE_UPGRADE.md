# Upgrading the running installation

Development and simulation do not modify the running checkout. Use this sequence
when the new revision is ready to deploy. Keep the exact previous commit ID and
backup path so rollback restores code and database together.

1. Review pending and uncertain deliveries with the existing owner controls.
   An uncertain Telegram send needs a receipt decision; do not automatically replay
   it. Record unfinished learner actions for the same review.
2. Stop the application gracefully with its normal terminal/service shutdown and
   wait for background work to drain. Leave the local model services running.
3. Back up the real database consistently. From the deployment checkout, adjust
   the database path below if needed:

```sh
nix develop --command python - <<'PY'
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

source = Path("data/mika.db").resolve()
directory = Path("backups")
directory.mkdir(exist_ok=True)
stamp = datetime.now(ZoneInfo("Asia/Almaty")).strftime("%Y%m%d-%H%M%S")
target = directory / f"mika-before-life-{stamp}.sqlite3"
with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
    with closing(sqlite3.connect(target)) as backup:
        original.backup(backup)
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
print(target)
PY
```

4. Select the reviewed code revision in the stopped deployment checkout. Retain
   its local `.env`, `config/telegram.yaml`, optional world-state file, library,
   data and logs. Do not replace these with development fixtures.
   For the detailed-world revision, retain `topics.state: 1266` in that private
   YAML. The prior seven-topic parser does not accept this optional eighth entry;
   select the matching new code before restarting. The ops bot needs permission
   to post photos/documents and pin/edit its own message in that topic.
5. Apply additive migrations to that same database:

```sh
nix develop --command python -m src.cli init-db \
  --database data/mika.db --log-file logs/migration.jsonl
```

6. Start the assembled runtime:

```sh
nix develop --command python -m src.cli run \
  --layout config/telegram.yaml \
  --database data/mika.db \
  --log-file logs/mika.jsonl
```

Use the existing `--world-state` argument only if keeping intentional overrides.
It does not reset stored mood, sleep or resources. An empty library is supported.

7. Review `/state`, `/health` and `/outbox review`. Confirm that saved progress is
   present, new itinerary/resource records initialize once, and held deliveries
   remain held. Ordinary incoming chat has durable recovery; old command/library
   jobs remain ephemeral. No test database should enter the deployment directory.
   Migration 16 preserves existing saved days. Revised morning, productivity and
   outing rules apply when a future day is first planned. Check that the state
   topic contains one pinned PAD photo and later changes edit the same message.
   An existing state document is converted to a photo while retaining its ID;
   `/state` exports the complete world snapshot.

For rollback, stop the upgraded process before restoring the matched old revision
and its database backup. Review receipts received since the backup before any
resend. Do not run old and new processes against the same database concurrently.
