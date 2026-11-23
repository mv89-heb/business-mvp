import io
import json
import zipfile

import pytest

from business_app.provisioning.handover_generator import (
    HandoverValidationError,
    build_handover_pack,
)


def completed_state(**overrides):
    state = {
        "workflow_id": "a" * 20,
        "plan_id": "a" * 20,
        "business_slug": "sample-business",
        "business_name": "עסק לדוגמה",
        "modules": ["lead_capture", "lead_management", "whatsapp_inbound", "automation_webhooks"],
        "status": "completed",
        "resources": {
            "github": {
                "repository_id": 12345,
                "repository": "customer/sample-business",
                "url": "https://github.com/customer/sample-business",
                "merge_commit": "abc123",
                "pull_request": {"number": 12},
            },
            "neon": {
                "project_id": "silent-tree-123",
                "project_name": "sample-business",
                "region": "aws-eu-central-1",
                "org_id": "org-123",
            },
            "render": {
                "service_id": "srv-123",
                "service_name": "sample-business",
                "url": "https://sample-business.onrender.com",
                "health_url": "https://sample-business.onrender.com/health",
                "plan": "free",
                "region": "frankfurt",
                "workspace_id": "ws-123",
            },
        },
    }
    state.update(overrides)
    return state


def test_builds_zip_with_guide_and_machine_readable_inventory():
    pack = build_handover_pack(completed_state())

    with zipfile.ZipFile(io.BytesIO(pack["zip_bytes"])) as archive:
        assert set(archive.namelist()) == {"HANDOVER_GUIDE.md", "TECHNICAL_INVENTORY.json"}
        guide = archive.read("HANDOVER_GUIDE.md").decode("utf-8")
        inventory = json.loads(archive.read("TECHNICAL_INVENTORY.json"))

    assert "עסק לדוגמה" in guide
    assert "HTTP 200" in guide
    assert inventory["provisioning"]["render_health_verified"] is True
    assert inventory["resources"]["github"]["repository_id"] == "12345"
    assert inventory["resources"]["neon"]["project_id"] == "silent-tree-123"
    assert inventory["resources"]["render"]["service_id"] == "srv-123"


def test_handover_does_not_claim_account_ownership_or_unverified_integrations():
    pack = build_handover_pack(completed_state())
    inventory = pack["inventory"]

    assert inventory["ownership"]["status"] == "not_verified"
    assert inventory["integrations"]["whatsapp"] == "selected_not_end_to_end_verified"
    assert inventory["integrations"]["automation_webhooks"] == "selected_not_end_to_end_verified"
    assert inventory["integrations"]["database_backup_restore"] == "not_verified"
    assert "בעלות על חשבונות: לא אומת" in pack["guide"]


def test_handover_strips_url_query_parameters_and_never_serializes_secrets():
    state = completed_state()
    state["resources"]["render"]["url"] = "https://sample-business.onrender.com/?token=do-not-leak"
    state["resources"]["render"]["health_url"] = "https://sample-business.onrender.com/health?secret=do-not-leak"
    state["resources"]["github"]["url"] = "https://user:password@github.com/customer/sample-business?access_token=do-not-leak"
    pack = build_handover_pack(state)
    serialized = pack["guide"] + pack["inventory_json"]

    assert "do-not-leak" not in serialized
    assert "password@" not in serialized
    assert "access_token" not in serialized
    assert pack["inventory"]["resources"]["github"]["repository_url"] is None
    assert pack["inventory"]["resources"]["render"]["health_url"] == "https://sample-business.onrender.com/health"


@pytest.mark.parametrize(
    "state",
    [
        {"status": "failed", "business_slug": "sample-business", "resources": {}},
        {"status": "completed", "business_slug": "../unsafe", "resources": {}},
        {"status": "completed", "business_slug": "sample-business", "resources": {}},
    ],
)
def test_handover_acceptance_fails_closed_for_incomplete_or_invalid_state(state):
    with pytest.raises(HandoverValidationError):
        build_handover_pack(state)


def test_handover_requires_a_real_http_health_url():
    state = completed_state()
    state["resources"]["render"]["health_url"] = "javascript:alert(1)"
    with pytest.raises(HandoverValidationError, match="verified Render health URL"):
        build_handover_pack(state)


def test_handover_escapes_html_and_does_not_publish_secret_like_business_name():
    state = completed_state(business_name='<script>alert(1)</script>')
    pack = build_handover_pack(state)
    assert '<script>' not in pack["guide"]
    assert '&lt;script&gt;' in pack["guide"]

    state = completed_state(business_name='password=should-not-appear')
    pack = build_handover_pack(state)
    assert "password=should-not-appear" not in pack["guide"]
    assert pack["inventory"]["business"]["name"] == "sample-business"


def test_handover_marks_unselected_integrations_as_not_selected():
    state = completed_state(modules=["lead_capture", "lead_management"])
    pack = build_handover_pack(state)

    assert pack["inventory"]["integrations"]["whatsapp"] == "not_selected"
    assert pack["inventory"]["integrations"]["automation_webhooks"] == "not_selected"
    assert "לא נכלל בפרויקט" in pack["guide"]
