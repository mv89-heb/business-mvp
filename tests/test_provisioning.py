import json

import pytest

from business_app.provisioning import (
    ProvisioningInputError,
    build_provisioning_plan,
)


def make_plan(**overrides):
    values = {
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
    }
    values.update(overrides)
    return build_provisioning_plan(**values)


def test_plan_is_dry_run_and_contains_isolated_resources():
    plan = make_plan()

    assert plan["mode"] == "dry_run"
    assert plan["execution_status"] == "not_started"
    assert plan["requires_explicit_execution_approval"] is True
    assert [item["provider"] for item in plan["infrastructure"]["resources"]] == [
        "github", "neon", "render"
    ]
    assert len({item["idempotency_key"] for item in plan["infrastructure"]["resources"]}) == 3
    assert plan["security"]["secrets_included"] is False
    assert plan["security"]["customer_data_copied"] is False


def test_plan_is_deterministic_except_creation_timestamp():
    first = make_plan()
    second = make_plan()

    assert first["plan_id"] == second["plan_id"]
    assert first["infrastructure"]["resources"] == second["infrastructure"]["resources"]


def test_render_free_plan_is_requested_but_not_claimed_as_verified():
    plan = make_plan()
    assert plan["cost_policy"]["requested_render_plan"] == "free"
    assert plan["cost_policy"]["live_price_check_required"] is True
    assert "not verified" in " ".join(plan["warnings"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"business_name": " "},
        {"business_name": "x" * 121},
        {"slug": "../unsafe"},
        {"slug": "UpperCase"},
        {"category": "unknown"},
        {"region": "oregon"},
        {"modules": ["inventory"]},
        {"modules": "lead_capture"},
    ],
)
def test_invalid_requests_fail_closed(overrides):
    with pytest.raises(ProvisioningInputError):
        make_plan(**overrides)


def test_module_selection_is_normalized_and_deduplicated():
    plan = make_plan(modules=["lead_management", "lead_capture", "lead_capture"])
    assert plan["application"]["modules"] == ["lead_capture", "lead_management"]


def test_plan_serializes_as_json_without_secrets():
    plan = make_plan()
    serialized = json.dumps(plan, ensure_ascii=False)
    assert "DATABASE_URL" not in serialized
    assert "SECRET_KEY" not in serialized
    assert "api_key" not in serialized.lower()


def test_plan_uses_provider_region_identifiers():
    plan = make_plan()
    resources = {item["provider"]: item for item in plan["infrastructure"]["resources"]}
    assert resources["neon"]["region"] == "aws-eu-central-1"
    assert resources["render"]["region"] == "frankfurt"
    assert plan["provider_readiness"]["github"] == "adapter_available_credentials_required"



def test_ai_enrichment_requires_automation_webhooks():
    with pytest.raises(ProvisioningInputError, match="requires the automation_webhooks"):
        make_plan(modules=["lead_capture", "lead_management", "ai_lead_enrichment"])


def test_lead_integration_modules_are_available_in_the_plan():
    plan = make_plan(modules=[
        "lead_capture", "lead_management", "whatsapp_inbound",
        "automation_webhooks", "ai_lead_enrichment",
    ])
    assert set(plan["application"]["modules"]) == {
        "lead_capture", "lead_management", "whatsapp_inbound",
        "automation_webhooks", "ai_lead_enrichment",
    }


def test_plan_includes_validated_business_profile_and_changes_identity():
    profile = make_plan(
        tagline="פתרונות מקצועיים לבית",
        phone="050-123-4567",
        whatsapp_number="050-123-4567",
        address="רחוב הדוגמה 12",
        service_area="בית שמש והסביבה",
        primary_color="#123456",
        accent_color="#ABCDEF",
        font_family="Heebo",
        services=["פרגולות", "דקים", "פרגולות"],
    )
    business = profile["business"]
    assert business["tagline"] == "פתרונות מקצועיים לבית"
    assert business["contact"]["phone_href"] == "tel:0501234567"
    assert business["contact"]["whatsapp_url"] == "https://wa.me/972501234567"
    assert business["branding"] == {
        "primary_color": "#123456",
        "accent_color": "#abcdef",
        "font_family": "Heebo",
    }
    assert business["services"] == ["פרגולות", "דקים"]
    assert profile["plan_id"] != make_plan().get("plan_id")


def test_plan_identity_changes_when_modules_change():
    basic = make_plan()
    expanded = make_plan(modules=["lead_capture", "lead_management", "whatsapp_inbound"])
    assert basic["plan_id"] != expanded["plan_id"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"primary_color": "blue"},
        {"accent_color": "#123"},
        {"font_family": "Comic Sans"},
        {"phone": "call me"},
        {"services": ["שירות"] * 7},
    ],
)
def test_invalid_business_profile_fails_closed(overrides):
    with pytest.raises(ProvisioningInputError):
        make_plan(**overrides)


def test_export_starter_applies_business_profile_and_modules(tmp_path):
    import json
    from business_app.export_starter import export_starter

    plan = make_plan(
        tagline="עסק מקצועי ואמין",
        phone="050-123-4567",
        whatsapp_number="050-123-4567",
        service_area="בית שמש והסביבה",
        primary_color="#123456",
        accent_color="#ABCDEF",
        font_family="Rubik",
        services=["שירות ראשון", "שירות שני"],
    )
    destination = tmp_path / "starter-export"
    export_starter(
        destination,
        business_name=plan["business"]["name"],
        slug=plan["business"]["slug"],
        modules=plan["application"]["modules"],
        business_profile=plan["business"],
    )
    config = json.loads((destination / "config" / "business.json").read_text(encoding="utf-8"))
    assert config["tagline"] == "עסק מקצועי ואמין"
    assert config["contact"]["phone_display"] == "050-123-4567"
    assert config["contact"]["whatsapp_url"] == "https://wa.me/972501234567"
    assert config["branding"]["primary_color"] == "#123456"
    assert config["branding"]["accent_color"] == "#abcdef"
    assert config["branding"]["font_family"] == "Rubik"
    assert [item["name"] for item in config["content"]["services"]] == ["שירות ראשון", "שירות שני"]
    assert config["modules"]["lead_capture"] is True
    assert not (destination / "business_app" / "provisioning").exists()
