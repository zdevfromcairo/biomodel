"""Plugin discovery for metrics, notifiers and loaders (v0.7).

BioModel Monitor supports third-party extensions through Python entry
points. Plug-ins register themselves under one of three groups:

* ``biomodel_monitor.metrics``    — extra metric callables.
* ``biomodel_monitor.notifiers``  — extra notification channels.
* ``biomodel_monitor.loaders``    — extra batch loaders.

A plug-in is discovered at startup via :func:`importlib.metadata.entry_points`
and registered into a global :class:`PluginRegistry`. Plugins can also be
registered programmatically (handy for tests or for in-tree plugins).
"""

from biomodel_monitor.plugins.registry import (
    Plugin,
    PluginGroup,
    PluginRegistry,
    discover_plugins,
)

__all__ = [
    "Plugin",
    "PluginGroup",
    "PluginRegistry",
    "discover_plugins",
]
