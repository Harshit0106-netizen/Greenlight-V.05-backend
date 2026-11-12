"""Assignment, escalation, activity trail and recurring-issue insights (database-backed)."""
from conftest import resolve_all, submit


def reviewer_id(client, h):
    return client.get("/team/members?role=reviewer", headers=h["manager"]).json()[0]["id"]


def test_activity_trail_records_who_did_what(client, h):
    rid = submit(client, h["creator"])["id"]
    resolve_all(client, h["creator"], rid)
    client.post(f"/reviews/{rid}/comments", headers=h["creator"], json={"text": "Legal checked the claim."})
    entries = client.get(f"/reviews/{rid}/activity", headers=h["creator"]).json()["entries"]
    kinds = [e["event_type"] for e in entries]
    assert kinds[-1] == "review_submitted" and "scan_completed" in kinds and "content_edited" in kinds and "comment_added" in kinds
    edit = next(e for e in entries if e["event_type"] == "content_edited")
    assert edit["detail"]["before"] and edit["detail"]["after"] and edit["actor"]["role"] == "creator"
    assert next(e for e in entries if e["event_type"] == "scan_completed")["actor"] is None  # automated
    assert all(e["summary"] for e in entries)


def test_open_items_follow_the_review(client, h):
    rid = submit(client, h["creator"])["id"]
    items = client.get(f"/reviews/{rid}/activity", headers=h["creator"]).json()["open_items"]
    assert [i["kind"] for i in items] == ["findings"]
    resolve_all(client, h["creator"], rid)
    kinds = [i["kind"] for i in client.get(f"/reviews/{rid}/activity", headers=h["manager"]).json()["open_items"]]
    assert kinds == ["awaiting_decision", "unassigned"]
    client.post(f"/reviews/{rid}/approve", headers=h["reviewer"], json={"note": "ok"})
    assert client.get(f"/reviews/{rid}/activity", headers=h["manager"]).json()["open_items"] == []


def test_only_managers_assign_and_only_to_reviewers(client, h):
    rid = submit(client, h["creator"])["id"]
    rv = reviewer_id(client, h)
    assert client.post(f"/reviews/{rid}/assign", headers=h["creator"], json={"assignee_id": rv}).status_code == 403
    assert client.post(f"/reviews/{rid}/assign", headers=h["manager"], json={"assignee_id": "U-nope"}).status_code == 422
    r = client.post(f"/reviews/{rid}/assign", headers=h["manager"], json={"assignee_id": rv, "note": "Please look today"})
    assert r.status_code == 200 and r.json()["assignee"]["id"] == rv
    act = client.get(f"/reviews/{rid}/activity", headers=h["manager"]).json()
    assert act["assignee"]["id"] == rv and act["entries"][0]["event_type"] == "assigned" and act["entries"][0]["reason"] == "Please look today"
    assert client.post(f"/reviews/{rid}/assign", headers=h["manager"], json={"assignee_id": None}).json()["assignee"] is None


def test_escalation_needs_a_note_and_makes_the_review_visible_to_reviewers(client, h):
    rid = submit(client, h["creator"])["id"]  # still has open findings, so reviewers cannot see it yet
    assert client.get(f"/reviews/{rid}", headers=h["reviewer"]).status_code in (403, 404)
    assert client.post(f"/reviews/{rid}/escalate", headers=h["manager"], json={"note": ""}).status_code == 422
    assert client.post(f"/reviews/{rid}/escalate", headers=h["creator"], json={"note": "urgent"}).status_code == 403
    assert client.post(f"/reviews/{rid}/escalate", headers=h["manager"], json={"note": "Launch is Friday"}).status_code == 200
    assert client.post(f"/reviews/{rid}/escalate", headers=h["manager"], json={"note": "again"}).status_code == 422
    assert client.get(f"/reviews/{rid}", headers=h["reviewer"]).status_code == 200
    act = client.get(f"/reviews/{rid}/activity", headers=h["reviewer"]).json()
    assert act["escalation"]["note"] == "Launch is Friday" and act["open_items"][0]["kind"] == "escalation"
    # escalating never lets anyone skip the findings
    assert client.post(f"/reviews/{rid}/approve", headers=h["reviewer"], json={"note": "x"}).status_code in (409, 422)


def test_versions_comments_and_workspace_feed(client, h):
    rid = submit(client, h["creator"])["id"]
    resolve_all(client, h["creator"], rid)
    client.post(f"/reviews/{rid}/comments", headers=h["manager"], json={"text": "Looks close."})
    versions = client.get(f"/reviews/{rid}/versions", headers=h["creator"]).json()
    assert versions[-1]["number"] == 1 and versions[-1]["reason"] and len(versions) >= 2 and versions[0]["number"] > 1
    assert [c["text"] for c in client.get(f"/reviews/{rid}/comments", headers=h["creator"]).json()] == ["Looks close."]
    assert client.get("/audit", headers=h["creator"]).status_code == 403
    assert client.get("/audit", headers=h["reviewer"]).status_code == 403
    feed = client.get("/audit?limit=5", headers=h["manager"]).json()
    assert 0 < len(feed) <= 5 and feed[0]["at"] >= feed[-1]["at"]
    assert client.get(f"/audit?review_id={rid}&event_type=comment_added", headers=h["admin"]).json()[0]["detail"]["text"] == "Looks close."


def test_recurring_issues_appear_after_the_same_mistake_twice(client, h):
    text = "We offer #1 fastest delivery, guaranteed."
    first, second = submit(client, h["creator"], text, "One")["id"], submit(client, h["creator"], text, "Two")["id"]
    assert first != second
    assert client.get("/insights/patterns", headers=h["creator"]).status_code == 403
    out = client.get("/insights/patterns", headers=h["manager"]).json()
    assert out["reviews_analyzed"] == 2
    p = out["patterns"][0]
    assert p["reviews_affected"] == 2 and p["recommendation"]["text"] and p["examples"]
    assert out["categories"] and "hotspots" in out
    assert client.get("/insights/patterns?days=3", headers=h["admin"]).status_code == 422  # window below the minimum
