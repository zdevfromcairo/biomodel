# Plugins (v0.7)

BioModel Monitor's metric library, notifier list and batch loaders cover
the common cases — pathology rule packs, Slack/PagerDuty/Teams, CSV /
Parquet / JSONL. Real deployments also need *one weird thing* the upstream
project shouldn't carry: an in-house rule pack, a Splunk webhook, a
proprietary loader for an internal data lake.

v0.7 ships a **plugin system** that lets you publish those bits as
independent Python packages.

## Discovery

Plugins are advertised through standard Python entry points:

```toml
# pyproject.toml of your plugin package
[project.entry-points."biomodel_monitor.metrics"]
my_org_plausibility = "myorg_plugins.plausibility:run"

[project.entry-points."biomodel_monitor.notifiers"]
splunk = "myorg_plugins.notify_splunk:send"
```

The three supported groups are:

* `biomodel_monitor.metrics` — extra metric callables.
* `biomodel_monitor.notifiers` — extra notification channels.
* `biomodel_monitor.loaders` — extra batch loaders.

## Activating

```bash
pip install your-plugin-package
biomodel-monitor plugins list
```

```json
[
  {"name": "my_org_plausibility", "group": "biomodel_monitor.metrics",
   "source": "entry_point", "metadata": {"dist": "myorg-plugins"}},
  {"name": "splunk", "group": "biomodel_monitor.notifiers",
   "source": "entry_point", "metadata": {"dist": "myorg-plugins"}}
]
```

In the server, plugin discovery is opt-in via
`BIOMODEL_DISCOVER_PLUGINS=true` (or `AppSettings.discover_plugins=True`).
A failure to load any single plugin is logged and skipped — one broken
package will never bring down the server.

## Programmatic registration

For in-tree plugins or tests, just register directly:

```python
from biomodel_monitor.plugins import PluginRegistry

reg = PluginRegistry()
reg.register("internal_rule",
             "biomodel_monitor.metrics",
             my_callable)
```

## Stability promise

The plugin **groups** above are part of BioModel Monitor's public API and
will only change with a major version bump. The exact callable signatures
expected inside each group are documented next to the group's primary
consumer (e.g. `metrics/plausibility.py` for `metrics`).
