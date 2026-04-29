"""Tamper-evident audit log."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


class AuditVerifyError(RuntimeError):
    """Raised when the chain is broken."""


@dataclass
class AuditEntry:
    seq: int
    ts: str  # ISO-8601 UTC
    actor_tenant: str | None
    actor_role: str | None
    actor_key_fp: str | None
    action: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str = field(default="")  # filled by AuditLog.append

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _digest(d: dict[str, Any]) -> str:
    """Stable SHA-256 of a dict, excluding the entry's own hash."""
    payload = {k: v for k, v in d.items() if k != "entry_hash"}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class AuditLog:
    """Append-only JSONL log with hash chaining.

    Thread-safe (one lock per instance). The log is written line-by-line and
    flushed on every append, so a crash loses at most the in-flight entry.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_hash, self._last_seq = self._scan_tail()

    # ------------------------------------------------------------------ tail
    def _scan_tail(self) -> tuple[str, int]:
        if not self.path.exists():
            return GENESIS_HASH, -1
        last = None
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return GENESIS_HASH, -1
        try:
            obj = json.loads(last)
            return obj["entry_hash"], int(obj["seq"])
        except (KeyError, ValueError) as exc:
            raise AuditVerifyError(f"corrupt last line: {exc}") from exc

    # ----------------------------------------------------------------- append
    def append(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        actor_tenant: str | None = None,
        actor_role: str | None = None,
        actor_key_fp: str | None = None,
    ) -> AuditEntry:
        with self._lock:
            entry = AuditEntry(
                seq=self._last_seq + 1,
                ts=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                actor_tenant=actor_tenant,
                actor_role=actor_role,
                actor_key_fp=actor_key_fp,
                action=action,
                payload=payload or {},
                prev_hash=self._last_hash,
            )
            entry.entry_hash = _digest(entry.to_dict())
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), default=str))
                f.write("\n")
                f.flush()
            self._last_hash = entry.entry_hash
            self._last_seq = entry.seq
            return entry

    # ------------------------------------------------------------------ read
    def entries(self) -> list[AuditEntry]:
        out: list[AuditEntry] = []
        if not self.path.exists():
            return out
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                out.append(AuditEntry(**obj))
        return out

    # ----------------------------------------------------------------- verify
    def verify(self) -> tuple[bool, int | None]:
        """Re-walk the chain. Returns ``(ok, first_bad_index)``.

        ``first_bad_index`` is the seq of the first invalid entry, or
        ``None`` if the chain is intact.
        """
        prev = GENESIS_HASH
        expected_seq = 0
        for entry in self.entries():
            d = entry.to_dict()
            recomputed = _digest(d)
            if entry.seq != expected_seq:
                return False, entry.seq
            if entry.prev_hash != prev:
                return False, entry.seq
            if recomputed != entry.entry_hash:
                return False, entry.seq
            prev = entry.entry_hash
            expected_seq += 1
        return True, None
