"""
tenant.py — Multi-tenant configuration.

Provides:
  TenantConfig        — per-tenant settings (Twilio credentials, defaults, overrides)
  TenantStore         — Protocol for tenant lookup by ID
  InMemoryTenantStore — default in-memory implementation
  YamlTenantStore     — loads from a YAML file at startup
  default_tenant_store() — convenience: single 'default' tenant from env vars
  resolve_tenant()    — resolve tenant from Twilio request parameters
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from typing import Protocol, runtime_checkable

import yaml


# =============================================================================
# TENANT CONFIG
# =============================================================================

@dataclass
class TenantConfig:
    """
    Per-tenant settings.  None values fall back to the corresponding
    environment variable so single-tenant deployments work unchanged.
    """
    tenant_id: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    default_goal: Optional[str] = None
    tts_provider: Optional[str] = None    # overrides TTS_PROVIDER env var
    voice_id: Optional[str] = None        # overrides ELEVENLABS_VOICE_ID env var
    # Phone numbers that belong to this tenant (for disambiguation when
    # multiple tenants share one Twilio account).
    allowed_to_numbers: List[str] = field(default_factory=list)


# =============================================================================
# TENANT STORE PROTOCOL
# =============================================================================

@runtime_checkable
class TenantStore(Protocol):
    def get(self, tenant_id: str) -> Optional[TenantConfig]: ...
    def list_all(self) -> List[TenantConfig]: ...


# =============================================================================
# IMPLEMENTATIONS
# =============================================================================

class InMemoryTenantStore:
    """Immutable in-memory store initialised from a dict at startup."""

    def __init__(self, tenants: dict) -> None:
        self._tenants: dict[str, TenantConfig] = dict(tenants)

    def get(self, tenant_id: str) -> Optional[TenantConfig]:
        return self._tenants.get(tenant_id)

    def list_all(self) -> List[TenantConfig]:
        return list(self._tenants.values())


class YamlTenantStore:
    """
    Loads tenant configs from a YAML file at construction time.

    YAML format — a list of tenant objects:

        - tenant_id: acme
          twilio_account_sid: ACxxx
          twilio_auth_token: xxx
          twilio_phone_number: "+15551234567"
          default_goal: "Answer customer enquiries"
          tts_provider: elevenlabs          # optional
          voice_id: some-voice-id           # optional
          allowed_to_numbers:               # optional
            - "+15559876543"

    Raises FileNotFoundError if the path does not exist.
    Raises ValueError if the YAML is malformed or missing required fields.
    """

    def __init__(self, path) -> None:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"Tenant config file not found: {path}. "
                "Create it or unset the TENANTS_YAML environment variable."
            )
        try:
            with open(path) as f:
                raw = yaml.safe_load(f) or []
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in tenant config {path}: {e}") from e

        if not isinstance(raw, list):
            raise ValueError(
                f"Tenant config {path} must be a YAML list of tenant objects, "
                f"got {type(raw).__name__}"
            )

        self._tenants: dict[str, TenantConfig] = {}
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Each tenant entry must be a mapping, got {type(item).__name__}"
                )
            tid = item.get("tenant_id")
            if not tid:
                raise ValueError(f"Tenant entry missing required 'tenant_id': {item}")
            config = TenantConfig(
                tenant_id=str(tid),
                twilio_account_sid=item.get("twilio_account_sid", ""),
                twilio_auth_token=item.get("twilio_auth_token", ""),
                twilio_phone_number=item.get("twilio_phone_number", ""),
                default_goal=item.get("default_goal"),
                tts_provider=item.get("tts_provider"),
                voice_id=item.get("voice_id"),
                allowed_to_numbers=list(item.get("allowed_to_numbers") or []),
            )
            self._tenants[str(tid)] = config

    def get(self, tenant_id: str) -> Optional[TenantConfig]:
        return self._tenants.get(tenant_id)

    def list_all(self) -> List[TenantConfig]:
        return list(self._tenants.values())


# =============================================================================
# HELPERS
# =============================================================================

def default_tenant_store() -> InMemoryTenantStore:
    """
    Build a single 'default' tenant whose credentials come from environment
    variables.  This is the automatic fallback for single-tenant deployments
    so existing configurations need no changes.
    """
    config = TenantConfig(
        tenant_id="default",
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        twilio_phone_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
    )
    return InMemoryTenantStore({"default": config})


def resolve_tenant(params: dict, store) -> Optional[TenantConfig]:
    """
    Resolve a TenantConfig from Twilio request parameters.

    Resolution priority:
    1. ``AccountSid`` matches a tenant's ``twilio_account_sid`` (distinct Twilio
       accounts per tenant).
    2. ``To`` phone number matches a tenant's ``twilio_phone_number`` or one of
       its ``allowed_to_numbers`` (shared Twilio account, different numbers).
    3. Single-tenant convenience: if exactly one tenant is registered, return it.

    Returns None if no tenant could be resolved.
    """
    configs = store.list_all() if hasattr(store, "list_all") else []

    account_sid = params.get("AccountSid", "")
    to_number = params.get("To", "")

    if account_sid:
        for config in configs:
            if config.twilio_account_sid == account_sid:
                return config

    if to_number:
        for config in configs:
            if config.twilio_phone_number == to_number:
                return config
            if to_number in config.allowed_to_numbers:
                return config

    # Single-tenant convenience — avoids mandatory TWILIO_ACCOUNT_SID matching
    if len(configs) == 1:
        return configs[0]

    return None
