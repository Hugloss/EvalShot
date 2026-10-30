"""Bounded counterexample models, not a Kafka client or production worker."""

import sqlite3


def restart_position(delivered, settled, start, strategy="delivery-prefix"):
    """Compare a safe delivered-record prefix with two tempting mutations."""
    if strategy == "maximum":
        return max(settled, default=start - 1) + 1
    if strategy == "integer-prefix":
        while start in settled:
            start += 1
        return start
    if strategy != "delivery-prefix":
        raise ValueError(strategy)
    position = start
    for offset in delivered:
        if offset not in settled:
            break
        position = offset + 1
    return position


async def publish_and_ack(producer, commit, offset, wait_for_receipt):
    """The producer test double separates enqueue from delivery explicitly."""
    receipt = await producer.send(b"result")
    if wait_for_receipt:
        await receipt
    await commit(offset + 1)


class TransactionCursor:
    """Model local position separately from transactionally committed state."""

    def __init__(self):
        self.position = 41
        self.committed = 41
        self.output = []

    def fetch(self):
        offset = self.position
        self.position += 1
        return offset

    def finish(self, offset, abort=False):
        if not abort:
            self.output.append(offset)
            self.committed = offset + 1

    def rewind(self):
        self.position = self.committed


class Inbox:
    """Real SQLite commit/reopen control; its effects are in the same DB."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS inbox "
            "(id TEXT PRIMARY KEY, payload TEXT NOT NULL, done INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS effects "
            "(id TEXT PRIMARY KEY, payload TEXT NOT NULL);"
        )

    def accept(self, key, payload):
        with self.db:
            row = self.db.execute(
                "SELECT payload FROM inbox WHERE id = ?", (key,)
            ).fetchone()
            if row is not None and row[0] != payload:
                raise ValueError("conflicting payload for existing operation")
            self.db.execute(
                "INSERT OR IGNORE INTO inbox VALUES (?, ?, 0)", (key, payload)
            )

    def recover(self):
        with self.db:
            pending = self.db.execute(
                "SELECT id, payload FROM inbox WHERE done = 0"
            ).fetchall()
            for key, payload in pending:
                self.db.execute(
                    "INSERT OR IGNORE INTO effects VALUES (?, ?)", (key, payload)
                )
                self.db.execute("UPDATE inbox SET done = 1 WHERE id = ?", (key,))
        return self.db.execute("SELECT id, payload FROM effects ORDER BY id").fetchall()

    def close(self):
        self.db.close()


class ExpiringGuard:
    """Model a once-only effect whose deduplication evidence has finite life."""

    def __init__(self):
        self.remembered = {}
        self.effects = []

    def apply(self, key, now, lifetime):
        if self.remembered.get(key, -1) > now:
            return
        self.effects.append(key)
        self.remembered[key] = now + lifetime


def apply_versioned(state, version, value, enforce_order):
    if not enforce_order or version > state[0]:
        return version, value
    return state
