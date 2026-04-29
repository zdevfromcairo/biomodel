"""Entry-point-based plugin registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

PluginGroup = Literal[
    "biomodel_monitor.metrics",
    "biomodel_monitor.notifiers",
    "biomodel_monitor.loaders",
]

VALID_GROUPS: tuple[PluginGroup, ...] = (
    "biomodel_monitor.metrics",
    "biomodel_monitor.notifiers",
    "biomodel_monitor.loaders",
)


@dataclass
class Plugin:
    """A discovered plugin."""

    name: str
    group: PluginGroup
    callable: Callable[..., Any]
    source: str = "entry_point"  # or "manual"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginRegistry:
    """In-process registry. Cheap to construct; safe to share."""

    _by_group: dict[PluginGroup, dict[str, Plugin]] = field(default_factory=dict)

    def register(
        self, name: str, group: PluginGroup, fn: Callable[..., Any],
        *, source: str = "manual", metadata: dict[str, Any] | None = None,
    ) -> Plugin:
        if group not in VALID_GROUPS:
            raise ValueError(f"unknown plugin group: {group!r}")
        if not callable(fn):
            raise TypeError("plugin callable must be callable")
        bucket = self._by_group.setdefault(group, {})
        if name in bucket:
            raise ValueError(f"plugin {name!r} already registered in {group}")
        plugin = Plugin(name=name, group=group, callable=fn, source=source,
                        metadata=metadata or {})
        bucket[name] = plugin
        return plugin

    def get(self, group: PluginGroup, name: str) -> Plugin:
        bucket = self._by_group.get(group, {})
        if name not in bucket:
            raise KeyError(f"plugin {name!r} not registered in {group}")
        return bucket[name]

    def list(self, group: PluginGroup | None = None) -> list[Plugin]:
        if group is None:
            return [p for bucket in self._by_group.values() for p in bucket.values()]
        return list(self._by_group.get(group, {}).values())

    def __len__(self) -> int:
        return sum(len(b) for b in self._by_group.values())


def discover_plugins(
    registry: PluginRegistry | None = None,
    *,
    groups: tuple[PluginGroup, ...] = VALID_GROUPS,
) -> PluginRegistry:
    """Populate ``registry`` from installed entry points.

    Returns a fresh registry if ``registry is None``. Failures to load a
    single plugin are logged and skipped — one broken plugin must not bring
    down the server.
    """
    import importlib.metadata as md
    import logging
    log = logging.getLogger("biomodel_monitor.plugins")
    reg = registry or PluginRegistry()
    for group in groups:
        try:
            eps = md.entry_points(group=group)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to enumerate %s: %s", group, exc)
            continue
        for ep in eps:
            try:
                fn = ep.load()
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to load %s plugin %r: %s", group, ep.name, exc)
                continue
            try:
                reg.register(ep.name, group, fn, source="entry_point",
                             metadata={"dist": getattr(ep, "dist", None) and ep.dist.name})
            except ValueError:
                # Already registered (idempotent reload). Skip silently.
                continue
    return reg
