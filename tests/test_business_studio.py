import pytest

from business_app.provisioning.business_studio import (
    create_business_studio_blueprint,
    normalize_business_payload, execute_github_command, encrypt_command_response,
    decrypt_github_command,
)


class FakeStore:
    def __init__(self):
        self.businesses = [{"id":"base-template","slug":"base-template","name":"תבנית בסיס","industry":"general","isTemplate":True,"status":"ACTIVE"}]
        self.proposals = []
        self.tasks = []
        self.approved = []
    def list_businesses(self): return self.businesses
    def accept_command(self, command_id, issue_number, command_type, payload):
        self.command = {"commandId":command_id,"issueNumber":issue_number,"type":command_type,"payload":payload}
        return {"new":True,"commandId":command_id,"status":"RECEIVED","result":None}
    def complete_command(self, command_id, status, result=None, error_code=None):
        self.command_result={"status":status,"result":result,"error":error_code}
    def list_commands(self): return []
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
    assert client.get("/api/commands").status_code==200
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

def test_github_command_allowlist_creates_pending_proposal_without_approval():
    _,store=make_client()
    result=execute_github_command(store,"create_business_proposal",{
        "name":"דני מיזוג אוויר","slug":"dani-ac","industry":"מיזוג אוויר"
    })
    assert result["proposalStatus"]=="PENDING_APPROVAL"
    assert result["createdTasks"]==2
    assert store.approved==[]


def test_github_command_rejects_unsupported_actions():
    _,store=make_client()
    with pytest.raises(ValueError):
        execute_github_command(store,"approve_business",{"proposalId":"proposal-1"})


def test_command_response_is_encrypted_and_roundtrips():
    import json, base64
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from business_app.provisioning.business_studio import RESPONSE_AAD
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    encrypted=encrypt_command_response({"status":"COMPLETED","name":"עסק לדוגמה"},public)
    aes_key=key.decrypt(base64.b64decode(encrypted["encryptedKey"]),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    plaintext=AESGCM(aes_key).decrypt(base64.b64decode(encrypted["nonce"]),base64.b64decode(encrypted["ciphertext"]),RESPONSE_AAD)
    assert json.loads(plaintext)["name"]=="עסק לדוגמה"

def test_command_envelope_decrypt_roundtrips(monkeypatch):
    import json, base64, os
    from datetime import datetime, timezone
    import hashlib
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from business_app.provisioning.business_studio import COMMAND_AAD
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    monkeypatch.setenv("STUDIO_COMMAND_PRIVATE_KEY_B64",base64.b64encode(private).decode())
    aes_key=os.urandom(32); nonce=os.urandom(12)
    command={"type":"get_businesses","payload":{}}
    ciphertext=AESGCM(aes_key).encrypt(nonce,json.dumps(command).encode(),COMMAND_AAD)
    encrypted_key=key.public_key().encrypt(aes_key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    envelope={
        "schema":"business-studio-command/v1","commandId":"12345678-1234-1234-1234-123456789012",
        "keyId":hashlib.sha256(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()[:24],
        "issuedAt":datetime.now(timezone.utc).isoformat(),
        "encryptedKey":base64.b64encode(encrypted_key).decode(),
        "nonce":base64.b64encode(nonce).decode(),"ciphertext":base64.b64encode(ciphertext).decode(),
        "responsePublicKey":key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    }
    decoded,response_key=decrypt_github_command(envelope)
    assert decoded==command
    assert "BEGIN PUBLIC KEY" in response_key


def test_github_command_ingest_requires_oidc_and_creates_only_pending_proposal(monkeypatch):
    import business_app.provisioning.business_studio as studio
    from flask import Flask
    monkeypatch.setattr(studio,"verify_github_actions_identity",lambda token:{"actor":"mv89-heb"})
    monkeypatch.setattr(studio,"decrypt_github_command",lambda envelope:(
        {"type":"create_business_proposal","payload":{"name":"דני מיזוג אוויר","slug":"dani-ac","industry":"מיזוג אוויר"}},
        "-----BEGIN PUBLIC KEY-----\ninvalid-test-key\n-----END PUBLIC KEY-----"
    ))
    monkeypatch.setattr(studio,"encrypt_command_response",lambda result,key:{"schema":"test","result":result})
    app=Flask(__name__); app.secret_key="test-secret"; app.config.update(TESTING=True)
    store=FakeStore()
    app.register_blueprint(create_business_studio_blueprint(lambda:store))
    client=app.test_client()
    response=client.post("/internal/github/commands",headers={"Authorization":"Bearer github-oidc"},json={
        "repository":"mv89-heb/business-mvp","sender":"mv89-heb",
        "issue":{"number":123,"title":"[Business Studio Command] create proposal","author":"mv89-heb","body":"{\"commandId\":\"12345678-1234-1234-1234-123456789012\"}"}
    })
    assert response.status_code==200
    assert response.json["accepted"] is True
    assert response.json["encryptedResult"]["result"]["status"]=="COMPLETED"
    assert response.json["encryptedResult"]["result"]["proposalStatus"]=="PENDING_APPROVAL"
    assert store.approved==[]
    assert store.command_result["status"]=="COMPLETED"


def test_github_command_ingest_rejects_wrong_sender(monkeypatch):
    import business_app.provisioning.business_studio as studio
    from flask import Flask
    monkeypatch.setattr(studio,"verify_github_actions_identity",lambda token:{"actor":"mv89-heb"})
    app=Flask(__name__); app.secret_key="test-secret"; app.config.update(TESTING=True)
    app.register_blueprint(create_business_studio_blueprint(lambda:FakeStore()))
    response=app.test_client().post("/internal/github/commands",headers={"Authorization":"Bearer github-oidc"},json={
        "repository":"mv89-heb/business-mvp","sender":"someone-else",
        "issue":{"number":124,"title":"[Business Studio Command] create proposal","author":"mv89-heb","body":"{}"}
    })
    assert response.status_code==403

def test_github_command_workflow_is_owner_only_and_uses_oidc():
    from pathlib import Path
    workflow=Path(".github/workflows/studio-command.yml").read_text()
    assert "issues:" in workflow and "types: [opened, edited]" in workflow
    assert "github.event.issue.user.login == 'mv89-heb'" in workflow
    assert "github.event.sender.login == 'mv89-heb'" in workflow
    assert "id-token: write" in workflow
    assert "core.getIDToken('business-studio')" in workflow
    assert "business-developer-studio.onrender.com/internal/github/commands" in workflow
    assert "business-developer-studio.onrender.com/internal/github/command-public-key" in workflow
    assert "startsWith(github.event.issue.title, '[Business Studio Key]')" in workflow

def test_render_public_command_key_endpoint_is_public_and_returns_key_id():
    from flask import Flask
    app=Flask(__name__); app.secret_key="test-secret"; app.config.update(TESTING=True)
    app.register_blueprint(create_business_studio_blueprint(lambda:FakeStore()))
    response=app.test_client().get("/internal/github/command-public-key")
    assert response.status_code==200
    assert response.json["keyId"]
    assert response.json["publicKey"].startswith("-----BEGIN PUBLIC KEY-----")


def test_command_decryption_rejects_stale_key_id(monkeypatch):
    import json, base64, os, hashlib
    from datetime import datetime, timezone
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from business_app.provisioning.business_studio import COMMAND_AAD
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    monkeypatch.setenv("STUDIO_COMMAND_PRIVATE_KEY_B64",base64.b64encode(private).decode())
    aes_key=os.urandom(32); nonce=os.urandom(12)
    encrypted=AESGCM(aes_key).encrypt(nonce,json.dumps({"type":"get_businesses","payload":{}}).encode(),COMMAND_AAD)
    wrapped=key.public_key().encrypt(aes_key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    envelope={"schema":"business-studio-command/v1","commandId":"12345678-1234-1234-1234-123456789012","issuedAt":datetime.now(timezone.utc).isoformat(),"keyId":"stale-key-id","encryptedKey":base64.b64encode(wrapped).decode(),"nonce":base64.b64encode(nonce).decode(),"ciphertext":base64.b64encode(encrypted).decode(),"responsePublicKey":key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()}
    with pytest.raises(ValueError):
        decrypt_github_command(envelope)
