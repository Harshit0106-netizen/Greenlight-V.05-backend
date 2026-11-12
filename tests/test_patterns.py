"""Recurring-issue grouping: pure, no database needed."""
from datetime import datetime, timedelta, timezone

from app.services.patterns import build_patterns, classify, parse_use

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
SOURCES = {
    "T-8": {"kind": "term", "title": "Zippy bottle", "text": "Use: Zippy Bottle. Approved product name."},
    "G-4.2": {"kind": "guideline", "title": "Avoid unsupported superlatives", "text": "..."},
    "G-2.1": {"kind": "guideline", "title": "Tone", "text": "..."},
    "G-5.1": {"kind": "guideline", "title": "Accessibility basics", "text": "..."},
}


def row(i, review, type_, quote, source, days_ago=3, decision=None, creator="U-1", severity="medium", reason=""):
    return {"finding_id": f"FD-{i}", "review_id": review, "creator_id": creator, "type": type_, "severity": severity, "quote": quote,
            "reason": reason, "source_id": source, "source_kind": SOURCES.get(source, {}).get("kind"), "source_version": "v3",
            "at": NOW - timedelta(days=days_ago), "decision": decision}


def test_parse_use():
    assert parse_use("Use: Zippy Bottle. Approved product name.") == "Zippy Bottle"
    assert parse_use("nothing here") is None


def test_classify_families():
    assert classify(row(1, "A", "terminology", "Zippy bottle", "T-8"), SOURCES)[1] == "“Zippy bottle” used instead of “Zippy Bottle”"
    assert classify(row(1, "A", "unsupported_claim", "#1 fastest delivery, guaranteed", "G-4.2"), SOURCES)[0] == "rank"
    assert classify(row(1, "A", "unsupported_claim", "best colours", "G-4.2"), SOURCES)[0] == "superlative"
    assert classify(row(1, "A", "unsupported_claim", "100% safe", "G-4.2"), SOURCES)[0] == "guarantee"
    assert classify(row(1, "A", "tone", "miss out!!!", "G-2.1"), SOURCES)[0] == "pressure"
    assert classify(row(1, "A", "tone", "AMAZING", "G-2.1"), SOURCES)[0] == "caps"
    assert classify(row(1, "A", "accessibility", "banner.png", "G-5.1", reason="An image is referenced without alternative text."), SOURCES)[0] == "alt_text"
    assert classify(row(1, "A", "visual", "#ff0000", "A-1"), SOURCES)[0] == "palette"


def test_a_pattern_needs_two_different_reviews():
    rows = [row(1, "CL-1", "terminology", "Zippy bottle", "T-8"), row(2, "CL-1", "terminology", "zippy bottle", "T-8")]
    assert build_patterns(rows, SOURCES, NOW)["patterns"] == []  # two hits, but one review
    rows.append(row(3, "CL-2", "terminology", "Zippy bottle", "T-8", creator="U-9"))
    p = build_patterns(rows, SOURCES, NOW)["patterns"]
    assert len(p) == 1 and p[0]["occurrences"] == 3 and p[0]["reviews_affected"] == 2 and p[0]["creators_affected"] == 2
    assert p[0]["source"]["id"] == "T-8" and p[0]["examples"][0]["quote"] in ("Zippy bottle", "zippy bottle")


def test_trend_and_window():
    old = [row(1, "CL-1", "tone", "!!!", "G-2.1", days_ago=80), row(2, "CL-2", "tone", "!!!", "G-2.1", days_ago=70)]
    assert build_patterns(old, SOURCES, NOW)["patterns"][0]["trend"] == "falling"
    new = [row(1, "CL-1", "tone", "!!!", "G-2.1", days_ago=5), row(2, "CL-2", "tone", "!!!", "G-2.1", days_ago=2)]
    assert build_patterns(new, SOURCES, NOW)["patterns"][0]["trend"] == "new"
    rising = old[:1] + [row(3, "CL-3", "tone", "!!!", "G-2.1", days_ago=5), row(4, "CL-4", "tone", "!!!", "G-2.1", days_ago=4)]
    assert build_patterns(rising, SOURCES, NOW)["patterns"][0]["trend"] == "rising"
    # anything older than the window is ignored
    assert build_patterns([row(1, "CL-1", "tone", "!!!", "G-2.1", days_ago=200), row(2, "CL-2", "tone", "!!!", "G-2.1", days_ago=199)], SOURCES, NOW)["findings_analyzed"] == 0


def test_disputes_change_the_recommendation():
    calm = [row(1, "CL-1", "unsupported_claim", "best", "G-4.2", decision="edited"), row(2, "CL-2", "unsupported_claim", "fastest", "G-4.2", decision="edited")]
    assert build_patterns(calm, SOURCES, NOW)["patterns"][0]["recommendation"]["action"] == "clarify_guideline"
    pushed = [row(1, "CL-1", "unsupported_claim", "best", "G-4.2", decision="disputed"), row(2, "CL-2", "unsupported_claim", "fastest", "G-4.2", decision="disputed")]
    out = build_patterns(pushed, SOURCES, NOW)["patterns"][0]
    assert out["recommendation"]["action"] == "add_fact" and out["dispute_rate"] == 1.0 and out["outcomes"]["disputed"] == 2


def test_sorted_by_severity_and_hotspots():
    rows = [row(1, "CL-1", "tone", "!!!", "G-2.1", severity="medium"), row(2, "CL-2", "tone", "!!!", "G-2.1", severity="medium"),
            row(3, "CL-1", "unsupported_claim", "best", "G-4.2", severity="high"), row(4, "CL-2", "unsupported_claim", "best", "G-4.2", severity="high")]
    out = build_patterns(rows, SOURCES, NOW)
    assert [p["severity"] for p in out["patterns"]] == ["high", "medium"]
    assert {h["source_id"] for h in out["hotspots"]} == {"G-4.2", "G-2.1"}
    assert {c["type"]: c["recurring_patterns"] for c in out["categories"]} == {"tone": 1, "unsupported_claim": 1}
