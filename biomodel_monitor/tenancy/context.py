"""Tenant + role registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["viewer", "operator", "writer", "admin"]

ROLE_ORDER: tuple[Role, ...] = ("viewer", "operator", "writer", "admin")

PERMISSIONS: dict[str, Role] = {
    # Read endpoints.
    "read": "viewer",
    "list_runs": "viewer",
    "list_alerts": "viewer",
    "metric_history": "viewer",
    "model_card": "viewer",
    "forecast": "viewer",
    "changepoints": "viewer",
    # Operator endpoints.
    "annotate": "operator",
    # Writer endpoints.
    "run_pipeline": "writer",
    "ingest": "writer",
    "promote_baseline": "writer",
    "whatif": "writer",
    # Admin.
    "manage_tenants": "admin",
}


class AccessDenied(PermissionError):
    """Raised when a tenant's role is not sufficient for an action."""


def role_satisfies(role: Role, minimum: Role) -> bool:
    return ROLE_ORDER.index(role) >= ROLE_ORDER.index(minimum)


@dataclass(frozen=True)
class TenantContext:
    """Identity attached to an authenticated request."""

    tenant_id: str
    role: Role
    api_key_id: str | None = None  # opaque key fingerprint, for audit logs

    def require(self, action: str) -> None:
        """Raise :class:`AccessDenied` if this tenant cannot perform ``action``."""
        needed = PERMISSIONS.get(action, "admin")
        if not role_satisfies(self.role, needed):
            raise AccessDenied(
                f"action {action!r} requires role >= {needed}, got {self.role}",
            )

    def can(self, action: str) -> bool:
        try:
            self.require(action)
        except AccessDenied:
            return False
        return True


@dataclass
class _Entry:
    tenant_id: str
    role: Role


@dataclass
class TenantRegistry:
    """In-memory mapping of API key → ``(tenant_id, role)``.

    The mapping is provided up-front (or via ``TenantRegistry.from_env``);
    keys are looked up in constant time. There is no live mutation API on
    purpose: rotating keys is a deploy-time concern, and any hot-reload
    surface would need its own auth model.
    """

    entries: dict[str, _Entry] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict[str, tuple[str, Role]]) -> TenantRegistry:
        return cls(entries={k: _Entry(t, r) for k, (t, r) in mapping.items()})

    @classmethod
    def from_env(cls, env: dict | None = None) -> TenantRegistry:
        """Read ``BIOMODEL_TENANT_KEYS`` of the form
        ``key1:tenantA:writer,key2:tenantB:viewer``."""
        import os
        e = env if env is not None else os.environ
        raw = e.get("BIOMODEL_TENANT_KEYS", "")
        out: dict[str, _Entry] = {}
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(":")
            if len(parts) != 3:
                raise ValueError(
                    f"BIOMODEL_TENANT_KEYS entry must be key:tenant:role, got {chunk!r}",
                )
            key, tenant, role = parts
            if role not in ROLE_ORDER:
                raise ValueError(f"unknown role {role!r}; must be one of {ROLE_ORDER}")
            out[key] = _Entry(tenant_id=tenant, role=role)  # type: ignore[arg-type]
        return cls(entries=out)

    def lookup(self, api_key: str | None) -> TenantContext | None:
        """Return the :class:`TenantContext` for ``api_key``, or ``None``."""
        if not api_key:
            return None
        e = self.entries.get(api_key)
        if e is None:
            return None
        # Use last-4 of the key as the fingerprint — never the whole key.
        fp = api_key[-4:] if len(api_key) >= 4 else "****"
        return TenantContext(tenant_id=e.tenant_id, role=e.role, api_key_id=fp)

    def tenants(self) -> list[str]:
        return sorted({e.tenant_id for e in self.entries.values()})

    def __len__(self) -> int:
        return len(self.entries)
