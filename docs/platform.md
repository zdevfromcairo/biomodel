# Platform & Governance (v0.9)

v0.9 turns BioModel Monitor from "detector + notifier" into "detector +
notifier + governance fabric". Three new building blocks land:

* a **model registry** that owns the truth about which versions are cleared;
* a **policy engine** that turns alerts into auditable actions;
* **DP-protected federation**, **sliced-Wasserstein** drift, **mSPRT canary**
  and **SBOM/SLSA provenance** to round out the platform story.

Every new endpoint is *opt-in* — without `BIOMODEL_REGISTRY_PATH` and
`BIOMODEL_POLICY_PATH` the server keeps the v0.8 behaviour.

## Model registry & lineage

Register a model version, list it, and (when something goes wrong)
quarantine it. Every state change emits an event and lands in the v0.7
hash-chained audit log when configured.

```bash
biomodel-monitor registry register \
    --db reg.db \
    --model-id pathology-her2 --model-version 2.1.0 \
    --training-data-hash 9c2a... --framework pytorch

curl -XPOST -H "X-API-Key: $K" \
    http://localhost:8080/models/pathology-her2/2.1.0/quarantine \
    -d '{"note":"ECE alert persisted 3+ runs in ICU"}'
```

Lineage edges (`POST /lineage`) capture *"this version derives from that
one"* so you can answer "which downstream models depend on the version I
just quarantined?". See [ADR 0005](adr/0005-model-registry-quarantine.md)
for the design constraints.

## Declarative governance policies

Operators write rules in YAML and load them via `BIOMODEL_POLICY_PATH`:

```yaml
policies:
  - name: quarantine-on-persistent-ece
    when:
      metric: ece
      severity_at_least: alert
      persistence_at_least: 3
      cohort: ICU
    action: quarantine
    message: "ECE alert in ICU has persisted for 3+ runs"

  - name: notify-on-mmd-warn
    when:
      metric: mmd_rbf
      severity_at_least: warn
    action: notify
    message: "Embedding drift detected"
```

The engine returns a list of `PolicyAction` objects per alert; the v0.4
notifier stack consumes the `notify` actions and the registry consumes
the `quarantine` ones. CLI: `biomodel-monitor policy-eval --policies
policies.yaml --alerts alerts.json`.

## Differential-privacy federation

`biomodel_monitor.federated.dp` adds calibrated Laplace noise on top of
the v0.7 federated aggregator, with sequential-composition tracking via
`PrivacyAccountant`. Sites can publish drift / calibration sufficient
statistics with an explicit ε budget per release:

```python
from biomodel_monitor.federated.dp import (
    PrivacyAccountant, privatise_histogram, privatise_mean,
)

acc = PrivacyAccountant(budget_epsilon=2.0)
noisy_hist  = privatise_histogram(counts, epsilon=0.5, accountant=acc)
noisy_mean  = privatise_mean(local_mean, n=N, lo=0.0, hi=1.0,
                             epsilon=0.2, accountant=acc)
```

Counts are clipped to be non-negative as a post-processing step (still
ε-DP). The accountant fails closed when the budget is exhausted.

## Sliced 1-D Wasserstein

`metrics.wasserstein.sliced_wasserstein` complements PSI/MMD when you care
about *how far* the distribution moved, not just how *different* its shape
became. For multivariate inputs we project onto random unit directions and
average per-direction W₁:

```python
from biomodel_monitor.metrics.wasserstein import sliced_wasserstein

res = sliced_wasserstein(reference_emb, current_emb, n_projections=64)
print(res.value, res.severity)
```

Endpoint: `POST /wasserstein`. CLI: `biomodel-monitor wasserstein
--reference ref.json --current cur.json --projections 64`.

## Canary deployments via mSPRT

`biomodel_monitor.canary.CanaryMonitor` runs a mixture sequential
probability ratio test on the difference of bounded outcomes between
**control** and **canary** models. You can peek as often as you like —
Type-I error is controlled at any stopping time:

```python
mon = CanaryMonitor(alpha=0.01, tau=0.1, min_n=30)
for x in control_stream():  mon.add_control(x)
for x in canary_stream():   mon.add_canary(x)
print(mon.decide())
# CanaryDecision(verdict='rollback', diff_mean=-0.21, log_lr=7.4, …)
```

CLI: `biomodel-monitor canary --input ab.json --alpha 0.01`.

## SBOM + SLSA provenance

`biomodel_monitor.security` ships:

* `build_sbom()` → CycloneDX-1.5 lite JSON listing this build's Python
  dependencies, suitable for upload to a regulatory dossier.
* `build_provenance(subject_paths=…, invocation=…)` → in-toto SLSA-v1.0
  statement with SHA-256 digests for the produced artefacts.

Endpoint: `GET /sbom`. CLI: `biomodel-monitor sbom --out sbom.json`.

## What's *not* in v0.9

By design:

* **No per-`/runs` quarantine enforcement.** Quarantine is exposed via the
  registry and the policy engine; we don't load every batch up-front just
  to check status. See [ADR 0005](adr/0005-model-registry-quarantine.md).
* **No replacement for MLflow / W&B.** The registry tracks identity +
  status + lineage; experiment metrics still live wherever you train.
* **No new dependencies.** The whole v0.9 surface is implemented with the
  v0.8 dependency set (numpy, scipy, FastAPI, click, PyYAML).
