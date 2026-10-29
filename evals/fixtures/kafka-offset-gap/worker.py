"""Deliberately unsafe manually committing Kafka worker fixture."""

import asyncio


async def process_pair(consumer, topic_partition, first, second, persist, gate):
    """Both records belong to one partition and have consecutive offsets."""

    async def finish_first():
        await gate.wait()
        await persist(first)

    first_task = asyncio.create_task(finish_first())
    await persist(second)
    await consumer.commit({topic_partition: second.offset + 1})
    gate.set()
    await first_task
