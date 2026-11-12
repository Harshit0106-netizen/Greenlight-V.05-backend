"""Recurring-issue detection: group findings across reviews into patterns a team can act on.

Pure functions over plain dicts so the grouping is deterministic, testable and explainable. There is no AI step:
a pattern is "the same kind of issue, against the same rule, in several different reviews", nothing more.
Individuals are never ranked; only counts of creators are reported.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

I = re.IGNORECASE
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

CLAIM_FAMILIES = [
    ("rank", "“#1” and ranking claims", re.compile(r"#\s?1\b|\bno\.?\s?1\b|\bnumber[\s-]+one\b|\btop[- ]ranked\b", I)),
    ("superlative", "“best”, “fastest” and other superlatives", re.compile(r"\bbest\b|\bfastest\b|\bcheapest\b|\bunbeatable\b|\bunmatched\b", I)),
    ("guarantee", "guarantees and absolute promises", re.compile(r"\bguarantee|\b100\s?%|\bzero[- ]risk\b", I)),
    ("leadership", "leadership and authority claims", re.compile(r"\bmarket[- ]lead|\bindustry[- ]lead|\bleading\b|\bleader\b|\bclinically\b|\bcarbon[- ]neutral\b", I)),
]
URGENCY = re.compile(r"!{2,}|don['’]t miss out|miss out|act now|hurry|last chance|limited time only", I)
_USE = re.compile(r"^\s*use\s*[:\-–]?\s*[\"“'‘]?(?P<use>[^\"”'’.;—–]+?)[\"”'’]?\s*(?:[.;—–].*)?$", I | re.S)

RECOMMENDATIONS = {
    "terminology_disputed": ("review_rule", "Creators often mark this term as not applicable. Check that the rule still matches how the brand writes, or add an example of when the other wording is fine."),
    "terminology": ("add_guidance", "Add a one-line reminder of the approved wording to the style guide, or to the template where this copy is written."),
    "claims_disputed": ("add_fact", "Creators say they have evidence for these claims. Add the supporting facts to the Brand Kit so they stop being flagged, or confirm that the claims cannot be used."),
    "claims": ("clarify_guideline", "Add examples to the guideline of claims that need an approved fact, and list the approved facts creators can use instead."),
    "tone": ("add_guidance", "Add before-and-after examples of the approved voice to the tone guideline, and a short checklist for campaign copy."),
    "accessibility": ("update_template", "Add an alt-text field and a link-text check to the content template so this is fixed before review."),
    "visual": ("update_template", "Put the approved palette into the design template, or add the colour to the Brand Kit if it is intentional."),
    "other": ("add_guidance", "Add a short note to the relevant guideline so creators know what to do."),
}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_use(text: str) -> str | None:
    m = _USE.match(text or "")
    return m.group("use").strip() if m else None


def classify(f: dict, sources: dict[str, dict]) -> tuple[str, str]:
    """(family, human title) for one finding. Findings in the same family against the same source belong together."""
    t, quote, reason = f["type"], f.get("quote") or "", (f.get("reason") or "").lower()
    if t == "terminology":
        src = sources.get(f.get("source_id") or "", {})
        avoid = src.get("title") or quote
        use = parse_use(src.get("text", ""))
        fam = f.get("source_id") or "term"
        return fam, (f"“{avoid}” used instead of “{use}”" if use else f"“{avoid}” used against the terminology guide")
    if t in ("unsupported_claim", "contradicted_claim"):
        for fam, label, rx in CLAIM_FAMILIES:
            if rx.search(quote):
                return fam, f"Unsupported claims: {label}" if t == "unsupported_claim" else f"Claims that contradict an approved fact: {label}"
        return "other", "Unsupported claims" if t == "unsupported_claim" else "Claims that contradict an approved fact"
    if t == "tone":
        if URGENCY.search(quote):
            return "pressure", "Pressure and urgency language"
        if quote.isupper() and len(quote) >= 5:
            return "caps", "ALL-CAPS words"
        return "other", "Tone and messaging"
    if t == "accessibility":
        for fam, title, words in (("alt_text", "Images without descriptive alt text", ("alt text", "image is referenced", "image has")),
                                  ("link_text", "Vague link text", ("link text",)),
                                  ("contrast", "Low text contrast", ("contrast",)),
                                  ("heading", "Heading levels out of order", ("heading",))):
            if any(w in reason for w in words):
                return fam, title
        return "other", "Accessibility issues"
    if t == "visual":
        return "palette", "Colours outside the approved palette"
    return "other", t.replace("_", " ").capitalize()


def _recommend(t: str, dispute_rate: float) -> dict:
    if t == "terminology":
        key = "terminology_disputed" if dispute_rate >= 0.4 else "terminology"
    elif t in ("unsupported_claim", "contradicted_claim"):
        key = "claims_disputed" if dispute_rate >= 0.4 else "claims"
    else:
        key = t if t in RECOMMENDATIONS else "other"
    action, text = RECOMMENDATIONS[key]
    return {"action": action, "text": text}


def _rate(rows: list[dict]) -> float:
    decided = [r for r in rows if r.get("decision")]
    return round(sum(1 for r in decided if r["decision"] == "disputed") / len(decided), 2) if decided else 0.0


def _outcomes(rows: list[dict]) -> dict:
    out = {"edited": 0, "disputed": 0, "accepted": 0, "open": 0}
    for r in rows:
        out[r.get("decision") or "open"] += 1
    return out


def build_patterns(rows: list[dict], sources: dict[str, dict], now: datetime | None = None,
                   days: int = 90, min_reviews: int = 2) -> dict:
    """rows: one dict per finding: finding_id, review_id, creator_id, type, severity, quote, reason, source_id,
    source_kind, source_version, at (datetime of the review), decision (None | accepted | edited | disputed)."""
    now = _aware(now or datetime.now(timezone.utc))
    cutoff = now - timedelta(days=days)
    mid = now - timedelta(days=days / 2)
    rows = [r for r in rows if _aware(r["at"]) >= cutoff]

    groups: dict[tuple, list[dict]] = {}
    titles: dict[tuple, str] = {}
    for r in rows:
        fam, title = classify(r, sources)
        key = (r["type"], fam, r.get("source_id") or "")
        groups.setdefault(key, []).append(r)
        titles[key] = title

    patterns = []
    for key, g in groups.items():
        reviews = {r["review_id"] for r in g}
        if len(reviews) < min_reviews:
            continue
        recent = [r for r in g if _aware(r["at"]) >= mid]
        prior = len(g) - len(recent)
        trend = "new" if prior == 0 and recent else "rising" if len(recent) > prior else "falling" if len(recent) < prior else "steady"
        seen, examples = set(), []
        for r in sorted(g, key=lambda r: _aware(r["at"]), reverse=True):
            q = (r.get("quote") or "").strip()
            if q and q.lower() not in seen and len(examples) < 3:
                seen.add(q.lower())
                examples.append({"review_id": r["review_id"], "quote": q})
        sid = key[2]
        src = sources.get(sid)
        top = min(g, key=lambda r: SEVERITY_RANK.get(r["severity"], 3))["severity"]
        rate = _rate(g)
        patterns.append({
            "id": ":".join(k or "-" for k in key), "type": key[0], "title": titles[key], "severity": top,
            "occurrences": len(g), "reviews_affected": len(reviews), "creators_affected": len({r["creator_id"] for r in g}),
            "first_seen": min(_aware(r["at"]) for r in g).isoformat(), "last_seen": max(_aware(r["at"]) for r in g).isoformat(),
            "trend": trend, "dispute_rate": rate, "outcomes": _outcomes(g),
            "source": ({"kind": src.get("kind") or g[0].get("source_kind") or "guideline", "id": sid,
                        "version": g[0].get("source_version"), "title": src.get("title") or sid} if sid else None),
            "examples": examples, "recommendation": _recommend(key[0], rate),
        })
    patterns.sort(key=lambda p: (SEVERITY_RANK.get(p["severity"], 3), -p["reviews_affected"], -p["occurrences"], p["title"]))

    by_source: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("source_id"):
            by_source.setdefault(r["source_id"], []).append(r)
    hotspots = sorted(
        ({"source_id": sid, "title": sources.get(sid, {}).get("title", sid), "kind": sources.get(sid, {}).get("kind") or g[0].get("source_kind") or "guideline",
          "findings": len(g), "reviews_affected": len({r["review_id"] for r in g}), "dispute_rate": _rate(g)}
         for sid, g in by_source.items() if len({r["review_id"] for r in g}) >= min_reviews),
        key=lambda h: (-h["findings"], h["source_id"]))[:6]

    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    categories = sorted(
        ({"type": t, "findings": len(g), "reviews_affected": len({r["review_id"] for r in g}),
          "recurring_patterns": sum(1 for p in patterns if p["type"] == t)} for t, g in by_type.items()),
        key=lambda c: (-c["findings"], c["type"]))
    return {"window_days": days, "generated_at": now.isoformat(), "reviews_analyzed": len({r["review_id"] for r in rows}),
            "findings_analyzed": len(rows), "patterns": patterns, "hotspots": hotspots, "categories": categories}
