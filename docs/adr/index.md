# Architecture Decision Records

These ADRs capture the *why* behind durable design choices in BioModel
Monitor — the kind of decisions that are expensive to revisit and that new
contributors keep asking about.

| #     | Title                                                          | Status   |
|-------|----------------------------------------------------------------|----------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions          | Accepted |
| [0002](0002-severity-contract.md)             | Single severity contract for all metric results | Accepted |
| [0003](0003-sqlite-default-store.md)          | SQLite as the default persistence backend       | Accepted |
| [0004](0004-multi-tenancy-opt-in.md)          | Multi-tenancy is opt-in and additive            | Accepted |
| [0005](0005-model-registry-quarantine.md)     | Model registry + quarantine as v0.9 governance primitive | Accepted |

We use the [MADR](https://adr.github.io/madr/) lite format: *Context →
Decision → Consequences*.
