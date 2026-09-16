# Publication, curator, and Telegram interfaces

## Durable publication

`Publisher.enqueue_post` atomically queues a validated draft and its destinations.
The diary is the primary destination; a public channel can be a second delivery.
Each destination gets the existing unique `(post_id, channel)` idempotency key.
Changed payloads under the same key fail instead of modifying an in-flight post.

`OutboxWorker.run_once` claims one due row inside an immediate SQLite transaction,
increments attempts, and clears `next_try_at` before the network call. Other
workers and restarted processes cannot claim it again. Success records the
Telegram receipt; the primary receipt updates the post in the same transaction.
The outbox retains separate receipt IDs for each destination.

An explicit flood-control rejection can schedule a retry. A timeout, ambiguous
network failure, cancellation, or process crash leaves the claim uncertain.
`uncertain()` lists such rows, and `reconcile(id, message_id=..., at=...)` accepts
an externally verified receipt. Automatic resend is prohibited in this state.
This also means a crash before the network call can require manual attention.

Section 37.2 overstates the guarantee: the
[Telegram Bot API](https://core.telegram.org/bots/api#sendmessage) has no supplied
idempotency-key parameter and cannot commit atomically with SQLite. The sender
prevents automatic duplicates; it cannot guarantee both delivery and no duplicates
through every network failure. See TODO(OUTBOX-DELIVERY).

Every payload carries the originating `trace_id`, which is rebound around worker
execution and survives process restarts. Operational cards, attachments, edits,
and pins use the same outbox with an explicit stable operation key.

The publication tests use an asynchronous fake transport, including concurrent
workers, restart after success, remote acceptance followed by timeout, and failure
to commit a received message ID. No live Telegram message has been sent.
