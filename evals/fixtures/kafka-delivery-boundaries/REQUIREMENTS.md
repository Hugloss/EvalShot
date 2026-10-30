# Kafka delivery-boundary counterexamples

These are bounded failure/control models for AgentsCookbook's
`kafka-delivery-safety-review`, not a replacement Kafka client, worker framework,
or model evaluator. The canonical skill remains in AgentsCookbook.

## Governing expectations

For the selected source partition, offsets are acknowledged only after a durable
outcome or an independently recoverable handoff. Once-only effects and declared
business order must survive permitted replay. These requirements apply to the
safe and intentionally broken variants alike; do not weaken expectations to make
a mutation pass.

- The offset frontier follows the ordered records actually delivered in this
  assignment. Delivered 41/42 with 41 unfinished cannot commit 43. Delivered
  41/44, both settled and with no delivered records in between, may commit 45.
  Independent partitions may advance independently. The fixture inputs are
  already in delivery order; this is not a general offset-tracker implementation.
- A producer returning a delivery future has only accepted local enqueue. An
  eventual delivery error must leave source responsibility unacknowledged.
- Transaction abort leaves the modeled local fetch position advanced but does
  not commit outputs or offsets. Continuing with the next input can skip the
  aborted one; rewinding to committed position recovers it.
- A volatile queue disappears on process loss. A committed SQLite inbox row
  can be recovered after close/reopen even though the business effect has not
  happened when source acknowledgement occurs. The recovery owner uses one DB
  transaction for its local effect and completion. This does not cover remote
  APIs, concurrent claims or storage/power-loss guarantees.
- Deduplication evidence must survive the allowed replay horizon. The fake clock
  compares an expiry before replay with an expiry after replay.
- Reusing a live operation identity with a different payload is a conflict, not
  a successful duplicate. A version-ordered update must not be overwritten by an
  older completion even when offset commit logic is otherwise safe.

Semantics are grounded in the
[Kafka consumer offset API](https://kafka.apache.org/41/javadoc/org/apache/kafka/clients/consumer/KafkaConsumer.html),
[Kafka transaction design](https://kafka.apache.org/41/design/design/), and
[aiokafka producer delivery API](https://aiokafka.readthedocs.io/en/stable/producer.html).
The model encodes these contracts; it does not independently exercise them in a
real broker. The assumptions are explicit so that tests cannot be confused with
native fault-injection evidence.

## Execute

From EvalShot:

```sh
python3 scripts/test-kafka-delivery-safety.py
```

Only the Python standard library is needed. The test also executes the original
`kafka-offset-gap/worker.py` positive fixture, injecting a crash immediately after
commit and checking the actual missing outcome. That fixture and its pinned
source anchors remain unchanged. Barriers and controlled futures establish
causality; the bounded wait is a hang guard, not a timing assertion.

The suite checks observed offsets/effects/recovery state and compares unsafe
mutations with safe controls. SQLite close/reopen is a persistence round trip,
not a native process-kill or Kubernetes test. Keep these cheap checks in CI;
real broker/rebalance/failover/resource campaigns belong to separately scoped
runtime qualification.

## What this does not qualify

The three existing canonical model-evaluation cases remain in
`evals/sharp-skill-cases.json`; this addition is executable fixture evidence,
not additional model-backed classification results. It does not run OpenCode/Pi,
measure review accuracy, certify all rows of the skill's failure matrix, or
constitute independently adjudicated semantic-oracle approval.

Remaining qualification includes source-backed model discrimination for the new
seams and actual supported-client tests of abort/reseek, uncertain sends,
rebalance/lost ownership, fencing, retention/reset, deadlocks, forced shutdown
and resource limits. Real producer/topic durability and downstream isolation
must be inspected for the target deployment. No passing fixture count substitutes
for these missing guarantees.
