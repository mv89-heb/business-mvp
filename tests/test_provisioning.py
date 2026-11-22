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
