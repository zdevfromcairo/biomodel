"""Multi-tenancy, scoped API keys and RBAC (v0.7).

The v0.4 server treats every API key as equivalent. v0.7 introduces a
**tenant model**: each API key maps to a `(tenant_id, role)` pair, and
every authenticated request carries a :class:`TenantContext` the server
can use for scoping and authorisation checks.

Roles (least-to-most privileged):

* ``viewer``    — read endpoints only.
* ``operator``  — read + annotate (ack/resolve/comment/label).
* ``writer``    — operator + ingest + run pipeline + promote baselines.
* ``admin``     — writer + tenant management.

Storage is unchanged on disk; tenancy is enforced as a **scope filter**
applied at the API layer. A ``tenant_id`` is recorded into ``run/alert``
``extra`` payloads so cross-tenant isolation is auditable.
"""

from biomodel_monitor.tenancy.context import (
    PERMISSIONS,
    ROLE_ORDER,
    AccessDenied,
    Role,
    TenantContext,
    TenantRegistry,
    role_satisfies,
)

__all__ = [
    "AccessDenied",
    "PERMISSIONS",
    "ROLE_ORDER",
    "Role",
    "TenantContext",
    "TenantRegistry",
    "role_satisfies",
]
