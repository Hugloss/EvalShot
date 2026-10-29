# Kafka offset-gap fixture

This self-contained fixture represents a manually committing Kafka consumer. `first` and `second` are consecutive messages from the **same partition**, at offsets 41 and 42. `persist(record)` is the required durable side effect. A record is terminal only after its `persist` call finishes.

Contract: only commit the offset after the **highest contiguous terminal record**. A restart or rebalance after a successful commit must not permanently skip an unfinished earlier record. Retrying completed records may be acceptable, but skipping `first` is not.

The `gate` is initially unset and has no other setter. The source releases it only after the commit, so the first durable effect is definitively unfinished when the offset is committed. This is an evaluation fixture, not production integration code.
