"""API keys, structured submissions, the publish report and webhooks (database-backed)."""
import pytest
from conftest import resolve_all

from app.services import integration_service

BLOCKS = [
    {"id": "title", "type": "heading", "level": 1, "text": "Summer launch"},
    {"id": "body", "type": "paragraph", "text": "We offer #1 fastest delivery, guaranteed."},
    {"id": "cta", "type": "button", "label": "Click here", "url": "https://acme.example/buy"},
]
PAYLOAD = {"title": "Summer launch page", "blocks": BLOCKS, "source": {"system": "Acme CMS", "external_id": "post-42", "url": "https://cms.acme.example/42"}}


@pytest.fixture
def key(client, h):
    creator = client.get("/team/members?role=creator", headers=h["manager"]).json()[0]["id"]
    r = client.post("/integrations/api-keys", headers=h["admin"], json={"name": "CMS pipeline", "submit_as_user_id": creator})
    assert r.status_code == 201, r.text
    return r.json()


def kh(key):
    return {"X-API-Key": key["key"]}


def test_only_brand_admins_manage_keys_and_the_secret_is_shown_once(client, h, key):
    assert key["key"].startswith("glk_") and "key_hash" not in key
    assert client.post("/integrations/api-keys", headers=h["manager"], json={"name": "x", "submit_as_user_id": "U-1"}).status_code == 403
    listed = client.get("/integrations/api-keys", headers=h["admin"]).json()
    assert listed[0]["prefix"] == key["key"][:12] and "key" not in listed[0]
    assert client.post("/integrations/api-keys", headers=h["admin"], json={"name": "x", "submit_as_user_id": "U-nope"}).status_code == 422


def test_structured_submission_runs_the_same_checks_and_reports_blocks(client, h, key):
    assert client.post("/integrations/reviews", json=PAYLOAD).status_code == 401
    assert client.post("/integrations/reviews", headers={"X-API-Key": "glk_wrong"}, json=PAYLOAD).status_code == 401
    r = client.post("/integrations/reviews", headers=kh(key), json=PAYLOAD)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["finding_count"] >= 2 and created["status"] == "scanned"
    rep = client.get(created["report_url"], headers=kh(key)).json()
    assert rep["ready_to_publish"] is False and rep["blockers"] and rep["source"]["external_id"] == "post-42"
    claim = next(f for f in rep["findings"] if f["type"] == "unsupported_claim")
    assert claim["block_id"] == "body" and next(f for f in rep["findings"] if f["quote"].lower().startswith("click")) ["block_id"] == "cta"
    first = client.get(f"/reviews/{created['id']}/activity", headers=h["creator"]).json()["entries"][-1]
    assert "through the API from Acme CMS" in first["summary"] and first["actor"]["role"] == "creator"


def test_bad_structured_input_is_rejected(client, key):
    assert client.post("/integrations/reviews", headers=kh(key), json={**PAYLOAD, "blocks": [{"type": "image"}]}).status_code == 422
    assert client.post("/integrations/reviews", headers=kh(key), json={**PAYLOAD, "blocks": [{"type": "image", "asset_index": 3}]}).status_code == 422
    assert client.post("/integrations/reviews", headers=kh(key), json={**PAYLOAD, "blocks": []}).status_code == 422


def test_publish_report_flips_only_after_human_approval_and_hash_is_stable(client, h, key):
    created = client.post("/integrations/reviews", headers=kh(key), json=PAYLOAD).json()
    rid = created["id"]
    resolve_all(client, h["creator"], rid)
    mid = client.get(created["report_url"], headers=kh(key)).json()
    assert mid["ready_to_publish"] is False and mid["blockers"] == ["Waiting for a reviewer to approve."]
    client.post(f"/reviews/{rid}/approve", headers=h["reviewer"], json={"note": "Good to go"})
    done = client.get(created["report_url"], headers=kh(key)).json()
    assert done["ready_to_publish"] is True and done["blockers"] == [] and done["approved_by"]["role"] == "reviewer"
    assert len(done["content_sha256"]) == 64 and done["content_sha256"] != mid["content_sha256"]
    ui = client.get(f"/reviews/{rid}/publish-status", headers=h["manager"]).json()
    assert ui["content_sha256"] == done["content_sha256"]


def test_revoked_and_read_only_keys(client, h, key):
    ro = client.post("/integrations/api-keys", headers=h["admin"],
                     json={"name": "read only", "submit_as_user_id": key["submit_as"]["id"], "scopes": ["reviews:read"]}).json()
    assert client.post("/integrations/reviews", headers=kh(ro), json=PAYLOAD).status_code == 403
    assert client.post(f"/integrations/api-keys/{key['id']}/revoke", headers=h["admin"]).json()["revoked_at"]
    assert client.post("/integrations/reviews", headers=kh(key), json=PAYLOAD).status_code == 401


def test_webhooks_are_signed_and_never_carry_the_draft(client, h, key, monkeypatch):
    sent = []
    monkeypatch.setattr(integration_service, "send_http", lambda url, body, headers: sent.append((url, body, headers)) or 200)
    assert client.post("/integrations/webhooks", headers=h["manager"], json={"url": "http://localhost:9/h", "events": ["review.approved"]}).status_code == 403
    hook = client.post("/integrations/webhooks", headers=h["admin"], json={"url": "http://localhost:9/h", "events": ["review.in_review", "review.approved"]}).json()
    assert hook["secret"].startswith("whsec_")
    created = client.post("/integrations/reviews", headers=kh(key), json=PAYLOAD).json()
    resolve_all(client, h["creator"], created["id"])  # becomes ready -> review.in_review
    client.post(f"/reviews/{created['id']}/approve", headers=h["reviewer"], json={"note": "ok"})
    events = [hd["X-Greenlight-Event"] for _, _, hd in sent]
    assert events == ["review.in_review", "review.approved"]
    from app.services import integration_security as sec

    url, body, headers = sent[-1]
    assert sec.verify_signature(hook["secret"], headers["X-Greenlight-Timestamp"], body, headers["X-Greenlight-Signature"])
    assert b"fastest delivery" not in body and created["id"].encode() in body  # ids and statuses only, no draft text
    deliveries = client.get("/integrations/deliveries", headers=h["admin"]).json()
    assert [d["status"] for d in deliveries] == ["delivered", "delivered"]


def test_a_failing_webhook_never_breaks_the_workflow(client, h, key, monkeypatch):
    def boom(url, body, headers):
        raise TimeoutError("receiver is down")

    monkeypatch.setattr(integration_service, "send_http", boom)
    client.post("/integrations/webhooks", headers=h["admin"], json={"url": "http://localhost:9/h", "events": ["review.in_review"]})
    created = client.post("/integrations/reviews", headers=kh(key), json=PAYLOAD).json()
    resolve_all(client, h["creator"], created["id"])
    assert client.get(f"/reviews/{created['id']}", headers=h["creator"]).json()["status"] == "in_review"
    d = client.get("/integrations/deliveries", headers=h["admin"]).json()[0]
    assert d["status"] == "failed" and "receiver is down" in d["error"]


def test_webhook_urls_to_private_addresses_are_refused_in_production(client, h, monkeypatch):
    monkeypatch.setattr(integration_service, "_allow_private", lambda: False)
    r = client.post("/integrations/webhooks", headers=h["admin"], json={"url": "http://localhost:9/h", "events": ["ping"]})
    assert r.status_code == 422
