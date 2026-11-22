import pytest

from business_app.provisioning.business_studio import (
    create_business_studio_blueprint,
    normalize_business_payload,
)


class FakeStore:
    def __init__(self):
        self.businesses = [{"id":"base-template","slug":"base-template","name":"תבנית בסיס","industry":"general","isTemplate":True,"status":"ACTIVE"}]
        self.proposals = []
        self.tasks = []
        self.approved = []
    def list_businesses(self): return self.businesses
    def get_source_config(self, source_id): return {"branding":{"primaryColor":"#123456","secondaryColor":"#abcdef","fontFamily":"Rubik"},"siteContent":{"services":[{"title":"שירות לדוגמה"}]}}
    def list_proposals(self): return self.proposals
    def list_tasks(self): return self.tasks
    def create_proposal(self, payload):
        item={"id":"proposal-1","status":"PENDING_APPROVAL","payload":payload}
        self.proposals.append(item)
        return item
    def create_task(self,title,description="",proposal_id=None,priority="NORMAL"):
        item={"id":f"task-{len(self.tasks)+1}","title":title,"description":description,"proposalId":proposal_id,"status":"OPEN","priority":priority}
        self.tasks.append(item); return item
    def approve_proposal(self, proposal_id):
        self.approved.append(proposal_id)
        return {"businessId":"business-1","proposalId":proposal_id,"status":"APPROVED"}
    def dispatch_make_event(self, proposal_id): return {"status":"QUEUED_CONFIGURATION_REQUIRED"}
    def reject_proposal(self, proposal_id): return {"proposalId":proposal_id,"status":"REJECTED"}


def test_normalize_payload_applies_hebrew_rtl_defaults_and_three_questions():
    payload=normalize_business_payload({"name":"דני מיזוג אוויר","slug":"dani-ac","industry":"מיזוג אוויר"}, {"branding":{"primaryColor":"#123456","secondaryColor":"#abcdef","fontFamily":"Rubik"},"siteContent":{"services":[{"title":"שירות לדוגמה"}]}})
    assert payload["sourceBusinessId"] == "base-template"
    assert payload["branding"]["primaryColor"] == "#123456"
    assert payload["branding"]["fontFamily"] == "Rubik"
    assert payload["siteContent"]["heroTitle"].startswith("דני מיזוג אוויר")
    assert len(payload["automationConfig"]["leadQualificationQuestions"]) == 3
    assert payload["siteContent"]["services"][0]["title"] == "שירות לדוגמה"
    assert payload["automationConfig"]["direction"] == "rtl"
    assert payload["automationConfig"]["timeFormat"] == "24h"


@pytest.mark.parametrize("payload", [
    {"name":"","slug":"valid-slug","industry":"services"},
    {"name":"שם","slug":"bad slug","industry":"services"},
    {"name":"שם","slug":"valid-slug","industry":""},
])
def test_normalize_payload_rejects_missing_or_invalid_required_fields(payload):
    with pytest.raises(ValueError):
        normalize_business_payload(payload)


def make_client():
    from flask import Flask, session
    app=Flask(__name__)
    app.secret_key="test-secret"
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    store=FakeStore()
    app.register_blueprint(create_business_studio_blueprint(lambda:store))
    @app.before_request
    def login():
        session["developer"]=True
    return app.test_client(),store


def test_business_catalog_and_proposal_api_is_protected_and_returns_pending_proposal():
    client,store=make_client()
    response=client.get("/api/businesses")
    assert response.status_code==200
    assert response.json["businesses"][0]["id"]=="base-template"
    response=client.post("/api/business-proposals",json={"name":"דני מיזוג אוויר","slug":"dani-ac","industry":"מיזוג אוויר"})
    assert response.status_code==201
    assert response.json["proposal"]["status"]=="PENDING_APPROVAL"
    assert response.json["createdTasks"]==2
    assert len(store.tasks)==2


def test_approval_creates_business_and_queues_make_event_only_after_explicit_approval():
    client,store=make_client()
    response=client.post("/api/business-proposals/proposal-1/approve",json={})
    assert response.status_code==200
    assert store.approved==["proposal-1"]
    assert response.json["result"]["status"]=="APPROVED"
    assert response.json["result"]["webhook"]["status"]=="QUEUED_CONFIGURATION_REQUIRED"


def test_tasks_endpoint_validates_required_title_and_priority():
    client,_=make_client()
    assert client.post("/api/tasks",json={"title":" "}).status_code==400
    assert client.post("/api/tasks",json={"title":"משימה","priority":"URGENT"}).status_code==400

def test_business_api_rejects_unauthenticated_requests():
    from flask import Flask
    app=Flask(__name__); app.secret_key="test-secret"; app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    app.register_blueprint(create_business_studio_blueprint(lambda:FakeStore()))
    assert app.test_client().get("/api/businesses").status_code==401
