"""Plain-English wording for audit events and the "what still needs attention" list.

Pure functions (no database, no framework) so the wording is easy to test and the same rules can be mirrored
by the frontend's demo backend. Events written before these details existed still read sensibly.
"""
from __future__ import annotations


def _clip(text: object, limit: int = 70) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _q(text: object, limit: int = 70) -> str:
    return f"“{_clip(text, limit)}”"


def describe(event_type: str, detail: dict | None = None, reason: str = "") -> str:
    d = detail or {}
    t = event_type
    if t == "review_submitted":
        if d.get("via") == "api":
            ref = f" (ref {d['external_id']})" if d.get("external_id") else ""
            base = f"Submitted through the API from {d.get('source_system') or 'an external system'}{ref}"
        else:
            base = "Submitted for review"
        return f"{base} · Brand Kit {d['brand_kit_version']}" if d.get("brand_kit_version") else base
    if t == "scan_completed":
        n = int(d.get("findings", 0))
        mode = " (rules only; no AI was used)" if d.get("mode") == "rules_only" else ""
        assets = int(d.get("assets", 0))
        extra = f", {assets} of them about images" if assets else ""
        return f"Automated checks found {n} potential issue{'s' if n != 1 else ''}{extra}{mode}"
    if t == "finding_decision":
        quote = _q(d["quote"]) if d.get("quote") else "a finding"
        action = d.get("action")
        if action == "accepted":
            return f"Confirmed {quote} as a valid issue"
        if action == "disputed":
            return f"Marked {quote} as not applicable"
        if action == "edited":
            return f"Resolved {quote} by changing it"
        return f"Decided on {quote}"
    if t == "content_edited":
        if d.get("before") and d.get("after"):
            return f"Changed {_q(d['before'])} to {_q(d['after'])}"
        return "Edited the draft"
    if t == "comment_added":
        return f"Commented: {_q(d['text'], 140)}" if d.get("text") else "Added a comment"
    if t == "assigned":
        return f"Assigned to {d.get('assignee_name') or 'a reviewer'}"
    if t == "unassigned":
        return f"Removed the assignment{' (was ' + d['assignee_name'] + ')' if d.get('assignee_name') else ''}"
    if t == "escalated":
        return "Escalated for reviewer attention"
    if t == "status_changed":
        return f"Status changed from {d.get('from', '?').replace('_', ' ')} to {d.get('to', '?').replace('_', ' ')}"
    if t == "approved":
        return "Approved for publication"
    if t == "changes_requested":
        return "Requested changes"
    if t == "asset_added":
        return f"Added image {_q(d.get('filename', 'image'))}"
    if t == "asset_replaced":
        return f"Replaced image {_q(d.get('filename', 'image'))}"
    if t == "asset_alt_updated":
        return f"Updated alt text for {_q(d.get('filename', 'image'))}: {_q(d.get('after'))}" if d.get("after") else "Updated alt text"
    return t.replace("_", " ").capitalize()


def compute_open_items(status: str, unresolved: int, assignee_name: str | None,
                       escalation: dict | None, last_change_note: str = "") -> list[dict]:
    """What still needs attention on a review. Empty once approved."""
    if status == "approved":
        return []
    items: list[dict] = []
    if escalation:
        who = escalation.get("by_name") or "a manager"
        note = f": {_clip(escalation['note'], 140)}" if escalation.get("note") else ""
        items.append({"kind": "escalation", "label": f"Escalated by {who}{note}"})
    if status == "changes_requested":
        note = f": {_clip(last_change_note, 140)}" if last_change_note else ""
        items.append({"kind": "changes_requested", "label": f"A reviewer asked for changes{note}"})
    if unresolved:
        items.append({"kind": "findings", "label": f"{unresolved} finding{'s' if unresolved != 1 else ''} still need a decision from the creator",
                      "count": unresolved})
    if status == "in_review":
        items.append({"kind": "awaiting_decision", "label": "Waiting for a reviewer to approve or request changes"})
        if not assignee_name:
            items.append({"kind": "unassigned", "label": "Not assigned to a reviewer yet"})
    return items
