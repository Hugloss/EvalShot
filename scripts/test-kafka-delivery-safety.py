#!/usr/bin/env python3
"""Execute Kafka failure/control models. Does not qualify a model or broker."""

import asyncio
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_fixture(directory, name):
    source = ROOT / "evals" / "fixtures" / directory / "worker.py"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = load_fixture("kafka-offset-gap", "offset_gap")
model = load_fixture("kafka-delivery-boundaries", "delivery_boundaries")


class CrashAfterCommit(Exception):
    pass


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_pinned_positive_really_loses_earlier_record(self):
        persisted, positions = [], []

        async def persist(record):
            persisted.append(record.offset)

        async def commit(offsets):
            positions.append(offsets["partition"])
            raise CrashAfterCommit

        before = asyncio.all_tasks()
        try:
            with self.assertRaises(CrashAfterCommit):
                await legacy.process_pair(
                    SimpleNamespace(commit=commit), "partition",
                    SimpleNamespace(offset=41), SimpleNamespace(offset=42),
                    persist, asyncio.Event(),
                )
            self.assertEqual(persisted, [42])
            self.assertEqual(positions, [43])
            replayable = {x for x in (41, 42) if x >= positions[-1]}
            self.assertEqual({41, 42} - set(persisted) - replayable, {41})
        finally:
            # The simulated process death must not leak its blocked task.
            pending = asyncio.all_tasks() - before
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def delivery_probe(self, wait_for_receipt, succeeds):
        receipt = asyncio.get_running_loop().create_future()
        enqueued = asyncio.Event()
        commits = []

        async def send(_payload):
            enqueued.set()
            return receipt

        async def commit(position):
            commits.append(position)

        task = asyncio.create_task(model.publish_and_ack(
            SimpleNamespace(send=send), commit, 41, wait_for_receipt
        ))
        await asyncio.wait_for(enqueued.wait(), timeout=5)
        before_delivery = list(commits)
        if succeeds:
            receipt.set_result("broker-confirmed")
        else:
            receipt.set_exception(ConnectionError("delivery rejected"))
        if wait_for_receipt and not succeeds:
            with self.assertRaises(ConnectionError):
                await task
        else:
            await task
        # Retrieve the unsafe branch's unobserved error without hiding it.
        if not succeeds:
            self.assertIsInstance(receipt.exception(), ConnectionError)
        return before_delivery, commits

    async def test_enqueue_only_mutation_acknowledges_failed_delivery(self):
        self.assertEqual(await self.delivery_probe(False, False), ([42], [42]))

    async def test_delivery_failure_control_retains_source_responsibility(self):
        self.assertEqual(await self.delivery_probe(True, False), ([], []))

    async def test_delivery_success_control_acknowledges_after_receipt(self):
        self.assertEqual(await self.delivery_probe(True, True), ([], [42]))


class FrontierTests(unittest.TestCase):
    def test_maximum_mutation_crosses_unfinished_delivery(self):
        self.assertEqual(model.restart_position([41, 42], {42}, 41, "maximum"), 43)
        self.assertEqual(model.restart_position([41, 42], {42}, 41), 41)

    def test_numeric_gap_control_does_not_wait_for_nonexistent_records(self):
        self.assertEqual(model.restart_position([41, 44], {41, 44}, 41), 45)
        self.assertEqual(
            model.restart_position([41, 44], {41, 44}, 41, "integer-prefix"), 42
        )

    def test_real_delivered_gap_still_blocks(self):
        self.assertEqual(model.restart_position([41, 42, 44], {41, 44}, 41), 42)

    def test_empty_and_independent_partition_controls(self):
        self.assertEqual(model.restart_position([], set(), 41), 41)
        blocked = model.restart_position([41, 42], {42}, 41)
        independent = model.restart_position([8], {8}, 8)
        self.assertEqual((blocked, independent), (41, 9))

    def test_abort_without_rewind_mutation_skips_input(self):
        cursor = model.TransactionCursor()
        cursor.finish(cursor.fetch(), abort=True)
        cursor.finish(cursor.fetch())
        self.assertEqual((cursor.committed, cursor.output), (43, [42]))

    def test_abort_rewind_control_recovers_both_inputs(self):
        cursor = model.TransactionCursor()
        cursor.finish(cursor.fetch(), abort=True)
        cursor.rewind()
        cursor.finish(cursor.fetch())
        cursor.finish(cursor.fetch())
        self.assertEqual((cursor.committed, cursor.output), (43, [41, 42]))

    def test_safe_offsets_do_not_prevent_business_reordering(self):
        newer = model.apply_versioned((0, "initial"), 2, "new", False)
        self.assertEqual(model.apply_versioned(newer, 1, "old", False), (1, "old"))
        self.assertEqual(model.apply_versioned(newer, 1, "old", True), (2, "new"))

    def test_dedupe_retention_must_cover_allowed_replay(self):
        for lifetime, expected_effects in ((1, 2), (3, 1)):
            with self.subTest(lifetime=lifetime):
                guard = model.ExpiringGuard()
                guard.apply("operation", now=0, lifetime=lifetime)
                guard.apply("operation", now=2, lifetime=lifetime)
                self.assertEqual(len(guard.effects), expected_effects)


class HandoffTests(unittest.TestCase):
    def test_memory_queue_is_not_a_recoverable_handoff(self):
        volatile = {"operation": "document"}
        acknowledged = 42
        volatile.clear()  # Process death loses the only accepted obligation.
        self.assertEqual((acknowledged, volatile), (42, {}))

    def test_durable_inbox_recovers_after_ack_and_reopen(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "inbox.sqlite"
            inbox = model.Inbox(path)
            try:
                inbox.accept("operation", "document")
                self.assertEqual(inbox.db.execute("SELECT * FROM effects").fetchall(), [])
                acknowledged = 42  # Business effect has intentionally not run.
            finally:
                inbox.close()
            recovered = model.Inbox(path)
            try:
                self.assertEqual(acknowledged, 42)
                self.assertEqual(recovered.recover(), [("operation", "document")])
                recovered.accept("operation", "document")
                self.assertEqual(recovered.recover(), [("operation", "document")])
            finally:
                recovered.close()

    def test_conflicting_identity_cannot_inherit_success(self):
        with TemporaryDirectory() as temporary:
            inbox = model.Inbox(Path(temporary) / "inbox.sqlite")
            try:
                inbox.accept("operation", "original")
                inbox.recover()
                with self.assertRaises(ValueError):
                    inbox.accept("operation", "changed")
                self.assertEqual(inbox.recover(), [("operation", "original")])
            finally:
                inbox.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
