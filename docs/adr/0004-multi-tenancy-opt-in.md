# 0004 — Multi-tenancy is opt-in and additive

* **Status:** Accepted (v0.7)

## Context

v0.4–v0.6 ran a single global authentication surface: any valid API key
could perform any action. v0.7 introduced RBAC tenants
(`viewer`/`operator`/`writer`/`admin`) but a hard cutover would have
broken every existing deployment.

## Decision

Multi-tenancy is *additive*: when ``AppSettings.tenant_keys`` is empty, the
server keeps the v0.4 behaviour and authenticated requests get a synthetic
``TenantContext(tenant_id="default", role="admin")``. Mutating endpoints
always call ``tenant.require(action)`` against this context — so the
permission machinery is exercised on every request, but is a no-op in the
single-tenant default.

## Consequences

* Upgrading from v0.6 → v0.7 → v0.9 requires no config changes.
* Tests for the permission surface run against both the synthetic context
  and a populated tenant registry, so regressions are caught early.
* Removing the synthetic context is a breaking change and would warrant a
  major version bump.
