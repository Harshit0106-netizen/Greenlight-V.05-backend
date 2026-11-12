"""Image checks inside the review workflow (database-backed)."""
import base64
import io

import pytest
from conftest import resolve_all

PIL = pytest.importorskip("PIL.Image")
CLEAN = "We offer fast delivery in select cities."


def png(colour=(49, 92, 232), size=(60, 40)):
    buf = io.BytesIO()
    PIL.new("RGB", size, colour).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def asset(alt="", colour=(49, 92, 232), name="hero.png"):
    return {"filename": name, "content_type": "image/png", "data_base64": png(colour), "alt_text": alt}


def create(client, headers, assets):
    r = client.post("/reviews", headers=headers, json={"title": "With image", "content": CLEAN, "intent_note": "", "assets": assets})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_review_without_images_is_unchanged(client, h):
    r = client.post("/reviews", headers=h["creator"], json={"title": "Plain", "content": CLEAN, "intent_note": ""})
    assert r.status_code == 201 and r.json()["finding_count"] == 0 and r.json()["status"] == "in_review"


def test_missing_alt_text_becomes_a_finding_that_blocks_the_review(client, h):
    made = create(client, h["creator"], [asset(alt="")])
    assert made["finding_count"] == 1 and made["status"] == "scanned"
    rid = made["id"]
    assert client.get(f"/reviews/{rid}/findings", headers=h["creator"]).json() == []  # text endpoint keeps its contract
    [a] = client.get(f"/reviews/{rid}/assets", headers=h["creator"]).json()
    f = a["findings"][0]
    assert f["type"] == "accessibility" and f["method"] == "rule_based" and a["width"] == 60 and a["height"] == 40
    assert client.get(f"/reviews/{rid}", headers=h["creator"]).json()["finding_count"] == 1
    # too-vague alt text is refused; real alt text resolves the finding and makes the review ready
    bad = client.patch(f"/findings/{f['finding_id']}", headers=h["creator"], json={"action": "edited", "reason": "", "edited_text": "image"})
    assert bad.status_code == 422
    ok = client.patch(f"/findings/{f['finding_id']}", headers=h["creator"],
                      json={"action": "edited", "reason": "", "edited_text": "A blue Zippy Bottle on a wooden table"})
    assert ok.status_code == 200 and ok.json()["review_status"] == "in_review"
    assert client.get(f"/reviews/{rid}/assets", headers=h["creator"]).json()[0]["alt_text"].startswith("A blue")


def test_off_palette_image_is_flagged_and_replacing_it_resolves_the_finding(client, h):
    made = create(client, h["creator"], [asset(alt="Orange bottle on a desk", colour=(228, 87, 46))])
    rid = made["id"]
    [a] = client.get(f"/reviews/{rid}/assets", headers=h["creator"]).json()
    assert [f["type"] for f in a["findings"]] == ["visual"] and a["findings"][0]["source"]["id"] == "A-1"
    visual_id = a["findings"][0]["finding_id"]
    edit = client.patch(f"/findings/{visual_id}", headers=h["creator"], json={"action": "edited", "reason": "", "edited_text": "x"})
    assert edit.status_code == 422  # an image finding cannot be fixed by typing; replace the image instead
    r = client.put(f"/reviews/{rid}/assets/{a['id']}", headers=h["creator"],
                   json={"content_type": "image/png", "data_base64": png((49, 92, 232))})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.get(f"/reviews/{rid}", headers=h["creator"]).json()["status"] == "in_review"
    kinds = [e["event_type"] for e in client.get(f"/reviews/{rid}/activity", headers=h["creator"]).json()["entries"]]
    assert "asset_added" in kinds and "asset_replaced" in kinds


def test_bad_uploads_are_rejected_and_nothing_is_created(client, h):
    junk = {"filename": "x.png", "content_type": "image/png", "data_base64": base64.b64encode(b"not an image").decode()}
    r = client.post("/reviews", headers=h["creator"], json={"title": "t", "content": CLEAN, "intent_note": "", "assets": [junk]})
    assert r.status_code == 422
    assert client.get("/reviews", headers=h["creator"]).json() == []
    wrong_type = dict(asset(alt="A real description"), content_type="image/jpeg")
    assert client.post("/reviews", headers=h["creator"], json={"title": "t", "content": CLEAN, "intent_note": "", "assets": [wrong_type]}).status_code == 422
    assert client.post("/reviews", headers=h["creator"], json={"title": "t", "content": CLEAN, "intent_note": "",
                                                               "assets": [dict(asset(), data_base64="%%%")]}).status_code == 422


def test_adding_an_image_later_sends_a_ready_review_back_to_resolving_only_while_editable(client, h):
    rid = create(client, h["creator"], [])["id"]  # no findings: already in review, so no more changes
    r = client.post(f"/reviews/{rid}/assets", headers=h["creator"], json=asset(alt=""))
    assert r.status_code == 422
    sid = client.post("/reviews", headers=h["creator"], json={"title": "t", "content": "We offer #1 fastest delivery, guaranteed.", "intent_note": ""}).json()["id"]
    added = client.post(f"/reviews/{sid}/assets", headers=h["creator"], json=asset(alt="Blue bottle on a table"))
    assert added.status_code == 201 and added.json()["findings"] == []


def test_only_the_creator_changes_images_and_files_are_served_with_a_token(client, h):
    rid = create(client, h["creator"], [asset(alt="")])["id"]
    sid = client.get(f"/reviews/{rid}/assets", headers=h["creator"]).json()[0]["id"]
    assert client.post(f"/reviews/{rid}/assets", headers=h["manager"], json=asset(alt="x y z w")).status_code == 403
    assert client.get(f"/reviews/{rid}/assets/{sid}/file").status_code == 401
    r = client.get(f"/reviews/{rid}/assets/{sid}/file", headers=h["manager"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
