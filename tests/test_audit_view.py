"""Pure wording tests: no database needed."""
from app.services.audit_view import compute_open_items, describe


def test_wording_for_each_event():
    assert describe("review_submitted", {"brand_kit_version": "v3"}) == "Submitted for review · Brand Kit v3"
    assert "through the API from Acme CMS (ref post-9)" in describe("review_submitted", {"via": "api", "source_system": "Acme CMS", "external_id": "post-9"})
    assert describe("scan_completed", {"findings": 1, "mode": "rules_only"}).endswith("(rules only; no AI was used)")
    assert describe("scan_completed", {"findings": 3, "assets": 1}) == "Automated checks found 3 potential issues, 1 of them about images"
    assert describe("finding_decision", {"quote": "#1 fastest", "action": "disputed"}) == "Marked “#1 fastest” as not applicable"
    assert describe("content_edited", {"before": "cheap", "after": "affordable"}) == "Changed “cheap” to “affordable”"
    assert describe("assigned", {"assignee_name": "Priya Nair"}) == "Assigned to Priya Nair"
    assert describe("status_changed", {"from": "in_review", "to": "approved"}) == "Status changed from in review to approved"


def test_old_events_without_details_still_read_sensibly():
    assert describe("content_edited", {}) == "Edited the draft"
    assert describe("finding_decision", {"action": "accepted"}) == "Confirmed a finding as a valid issue"
    assert describe("something_new", {}) == "Something new"


def test_long_text_is_clipped():
    out = describe("comment_added", {"text": "x" * 500})
    assert len(out) < 170 and out.endswith("…”")


def test_open_items():
    assert compute_open_items("approved", 3, None, {"by_name": "H"}) == []
    kinds = lambda **kw: [i["kind"] for i in compute_open_items(**kw)]  # noqa: E731
    assert kinds(status="resolving", unresolved=2, assignee_name=None, escalation=None) == ["findings"]
    assert kinds(status="in_review", unresolved=0, assignee_name=None, escalation=None) == ["awaiting_decision", "unassigned"]
    assert kinds(status="in_review", unresolved=0, assignee_name="Priya", escalation=None) == ["awaiting_decision"]
    got = compute_open_items("changes_requested", 1, None, {"by_name": "Harshit", "note": "Legal is waiting"}, "Fix the claim")
    assert [i["kind"] for i in got] == ["escalation", "changes_requested", "findings"]
    assert "Legal is waiting" in got[0]["label"] and "Fix the claim" in got[1]["label"]
