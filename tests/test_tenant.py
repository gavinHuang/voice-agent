"""
Tests for shuo/tenant.py — TenantConfig, InMemoryTenantStore,
YamlTenantStore, resolve_tenant, and default_tenant_store.
"""

import pytest
import yaml


# =============================================================================
# InMemoryTenantStore
# =============================================================================

def _make_config(tenant_id="acme", account_sid="ACacme", phone="+15551111111"):
    from shuo.tenant import TenantConfig
    return TenantConfig(
        tenant_id=tenant_id,
        twilio_account_sid=account_sid,
        twilio_auth_token="tok",
        twilio_phone_number=phone,
    )


def test_in_memory_store_get_known():
    from shuo.tenant import InMemoryTenantStore
    cfg = _make_config()
    store = InMemoryTenantStore({"acme": cfg})
    assert store.get("acme") is cfg


def test_in_memory_store_get_unknown():
    from shuo.tenant import InMemoryTenantStore
    store = InMemoryTenantStore({})
    assert store.get("unknown") is None


def test_in_memory_store_list_all():
    from shuo.tenant import InMemoryTenantStore
    a = _make_config("a", "ACa", "+15550000001")
    b = _make_config("b", "ACb", "+15550000002")
    store = InMemoryTenantStore({"a": a, "b": b})
    items = store.list_all()
    assert len(items) == 2
    assert {c.tenant_id for c in items} == {"a", "b"}


# =============================================================================
# YamlTenantStore
# =============================================================================

def test_yaml_store_loads_two_tenants(tmp_path):
    from shuo.tenant import YamlTenantStore
    p = tmp_path / "tenants.yaml"
    p.write_text(yaml.dump([
        {"tenant_id": "acme", "twilio_account_sid": "ACacme",
         "twilio_auth_token": "tok1", "twilio_phone_number": "+15551111111"},
        {"tenant_id": "beta", "twilio_account_sid": "ACbeta",
         "twilio_auth_token": "tok2", "twilio_phone_number": "+15552222222"},
    ]))
    store = YamlTenantStore(p)
    assert store.get("acme") is not None
    assert store.get("beta") is not None
    assert store.get("unknown") is None
    assert len(store.list_all()) == 2


def test_yaml_store_missing_file_raises(tmp_path):
    from shuo.tenant import YamlTenantStore
    with pytest.raises(FileNotFoundError, match="TENANTS_YAML"):
        YamlTenantStore(tmp_path / "no_such_file.yaml")


def test_yaml_store_missing_tenant_id_raises(tmp_path):
    from shuo.tenant import YamlTenantStore
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump([{"twilio_account_sid": "ACfoo"}]))
    with pytest.raises(ValueError, match="tenant_id"):
        YamlTenantStore(p)


def test_yaml_store_not_a_list_raises(tmp_path):
    from shuo.tenant import YamlTenantStore
    p = tmp_path / "bad.yaml"
    p.write_text("tenant_id: foo\n")
    with pytest.raises(ValueError, match="list"):
        YamlTenantStore(p)


def test_yaml_store_optional_fields(tmp_path):
    from shuo.tenant import YamlTenantStore
    p = tmp_path / "tenants.yaml"
    p.write_text(yaml.dump([{
        "tenant_id": "t1",
        "twilio_account_sid": "AC1",
        "twilio_auth_token": "tok",
        "twilio_phone_number": "+15550000001",
        "tts_provider": "kokoro",
        "voice_id": "v123",
        "allowed_to_numbers": ["+15559999999"],
        "default_goal": "Be helpful",
    }]))
    store = YamlTenantStore(p)
    cfg = store.get("t1")
    assert cfg.tts_provider == "kokoro"
    assert cfg.voice_id == "v123"
    assert cfg.allowed_to_numbers == ["+15559999999"]
    assert cfg.default_goal == "Be helpful"


# =============================================================================
# resolve_tenant
# =============================================================================

def _make_store(*configs):
    from shuo.tenant import InMemoryTenantStore
    return InMemoryTenantStore({c.tenant_id: c for c in configs})


def test_resolve_by_account_sid():
    from shuo.tenant import resolve_tenant
    cfg = _make_config("acme", "ACacme", "+15551111111")
    store = _make_store(cfg)
    result = resolve_tenant({"AccountSid": "ACacme", "To": ""}, store)
    assert result is cfg


def test_resolve_by_to_number():
    from shuo.tenant import resolve_tenant
    cfg = _make_config("acme", "ACshared", "+15551111111")
    store = _make_store(cfg)
    result = resolve_tenant({"AccountSid": "", "To": "+15551111111"}, store)
    assert result is cfg


def test_resolve_by_allowed_to_number():
    from shuo.tenant import TenantConfig, resolve_tenant
    cfg = TenantConfig(
        tenant_id="acme",
        twilio_account_sid="ACshared",
        twilio_auth_token="tok",
        twilio_phone_number="+15551111111",
        allowed_to_numbers=["+15559999999"],
    )
    store = _make_store(cfg)
    result = resolve_tenant({"AccountSid": "", "To": "+15559999999"}, store)
    assert result is cfg


def test_resolve_unknown_returns_none():
    from shuo.tenant import resolve_tenant
    cfg = _make_config("acme", "ACacme", "+15551111111")
    store = _make_store(cfg, _make_config("beta", "ACbeta", "+15552222222"))
    result = resolve_tenant({"AccountSid": "ACunknown", "To": "+15550000000"}, store)
    assert result is None


def test_resolve_single_tenant_convenience():
    from shuo.tenant import resolve_tenant
    cfg = _make_config("acme", "", "+15551111111")  # account_sid empty
    store = _make_store(cfg)
    # No AccountSid or To match, but only one tenant → return it
    result = resolve_tenant({"AccountSid": "ACother", "To": "+15550000000"}, store)
    assert result is cfg


def test_resolve_account_sid_takes_priority_over_to():
    from shuo.tenant import resolve_tenant
    a = _make_config("a", "ACa", "+15551111111")
    b = _make_config("b", "ACb", "+15552222222")
    store = _make_store(a, b)
    # AccountSid matches "a", but To matches "b"
    result = resolve_tenant({"AccountSid": "ACa", "To": "+15552222222"}, store)
    assert result is a


# =============================================================================
# default_tenant_store
# =============================================================================

def test_default_tenant_store_creates_default(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "toktest")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
    from shuo.tenant import default_tenant_store
    store = default_tenant_store()
    cfg = store.get("default")
    assert cfg is not None
    assert cfg.twilio_account_sid == "ACtest"
    assert cfg.tenant_id == "default"
