"""Tests for the file-watch directory watcher and filesystem queue."""

from __future__ import annotations

from pathlib import Path

from biomodel_monitor.scheduler.watcher import DirectoryWatcher, FilesystemQueue


def test_filesystem_queue_at_least_once(tmp_path):
    q = FilesystemQueue(tmp_path / "q.json")
    q.enqueue("a")
    q.enqueue("b")
    q.enqueue("a")  # dedup
    assert q.stats() == {"pending": 2, "in_flight": 0}
    item1 = q.claim()
    assert item1 == "a"
    assert q.stats() == {"pending": 1, "in_flight": 1}
    q.fail(item1)  # back to pending
    assert q.stats() == {"pending": 2, "in_flight": 0}
    item1b = q.claim()
    q.acknowledge(item1b)
    assert q.stats() == {"pending": 1, "in_flight": 0}


def test_filesystem_queue_visibility_timeout(tmp_path):
    q = FilesystemQueue(tmp_path / "q.json", visibility_timeout=0.0)
    q.enqueue("x")
    q.claim()  # in-flight
    # next claim should reclaim due to expired visibility timeout
    item = q.claim()
    assert item == "x"


def test_directory_watcher_emits_only_stable_files(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    w = DirectoryWatcher(d)

    f = d / "batch.jsonl"
    f.write_text('{"a":1}\n')
    # First scan registers the file but does not emit (size not yet stable)
    assert w.scan_once() == []
    # Second scan with same size — emit
    events = w.scan_once()
    assert len(events) == 1
    assert events[0].path == f.resolve() or events[0].path.name == "batch.jsonl"
    # No double emission
    assert w.scan_once() == []


def test_directory_watcher_ignores_growing_file(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    w = DirectoryWatcher(d)
    f = d / "batch.jsonl"
    f.write_text("a")
    assert w.scan_once() == []  # first sighting
    f.write_text("ab")  # still growing
    assert w.scan_once() == []  # size changed → reset
    # now stable across two polls → emit
    events = w.scan_once()
    assert len(events) == 1
    assert w.scan_once() == []  # no double emission


def test_directory_watcher_skips_unsupported_suffix(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    Path(d / "ignored.txt").write_text("x")
    w = DirectoryWatcher(d)
    w.scan_once()
    w.scan_once()
    assert w.scan_once() == []
