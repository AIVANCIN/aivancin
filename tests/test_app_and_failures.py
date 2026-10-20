import csv
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from aivancin import snapshots
from aivancin.app import create_app

from .conftest import DATA

SPEC_FIELDS = {"source_id", "title", "publisher", "url", "release", "access_date", "licence",
               "original_sha256", "derived_sha256", "derived_rows", "derived_path"}


def test_manifest_records_every_snapshot(snaps):
    recs = {r["source_id"]: r for r in snaps.manifest["snapshots"]}
    for rec in recs.values():
        if rec.get("kind") == "reference":
            continue  # a cited figure, not a bundled file
        assert SPEC_FIELDS <= set(rec)
        if rec["derived_rows"] is not None and rec["derived_path"].endswith(".csv"):
            with open(DATA / rec["derived_path"], encoding="utf-8", newline="") as f:
                assert sum(1 for _ in csv.reader(f)) - 1 == rec["derived_rows"]
    assert recs["onet31-dwas"]["derived_rows"] == 2087
    assert recs["onet31-task-dwas"]["derived_rows"] == 24087


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(DATA))


def test_api_flow_for_a_persona(client):
    assert client.get("/api/status").json()["ai_suggestions"] == "available"
    hafiz = next(p for p in client.get("/api/personas").json() if p["id"] == "hafiz")
    sug = client.post("/api/suggest", json=hafiz).json()
    assert sug["available"] and len(sug["sentences"]) == 9
    pick = [s["dwa_id"] for s in sug["suggestions"] if s["lanes"] == ["other_work"]][:5]
    req = {**hafiz, "confirmed": [{"dwa_id": d, "source": "suggested"} for d in pick]}
    profile = client.post("/api/profile", json=req).json()
    other = [a for a in profile["activities"] if "suggested" in a["sources"]]
    assert {a["dwa_id"] for a in other} == set(pick)
    assert profile["without_ai_suggestions"] is False


def test_api_rejects_invalid_intake_with_field_messages(client):
    r = client.post("/api/profile", json={"weekly_hours": -1, "budget_rm": 10, "degree_level": "degree"})
    assert r.status_code == 422
    assert set(r.json()["errors"]) == {"weekly_hours", "activities"}
    assert client.get("/api/occupations/00-0000.00/activities").status_code == 404
    hits = client.get("/api/activities", params={"q": "fix wifi"}).json()
    assert hits[0]["dwa_id"] == "4.A.3.b.1.d.1" and hits[0]["match"] == "meaning"


def test_bundled_fonts_are_served_with_their_licences(client):
    for path in ("kl-pro/KLPro-Regular.woff2", "kl-pro/KLPro-Medium.woff2", "kl-pro/KLPro-Semibold.woff2",
                 "kl-pro/KLPro-Bold.woff2", "jost/Jost-SemiBold-latin.woff2"):
        r = client.get(f"/fonts/{path}")
        assert r.status_code == 200 and r.content[:4] == b"wOF2", path
    assert "All rights reserved" in client.get("/fonts/kl-pro/LICENSE.txt").text
    assert "SIL Open Font License" in client.get("/fonts/jost/OFL.txt").text


def copy_data(tmp_path):
    dst = tmp_path / "data"
    shutil.copytree(DATA, dst)
    return dst


def assert_ai_unavailable_but_manual_works(data_dir, reason):
    c = TestClient(create_app(data_dir))
    status = c.get("/api/status").json()
    assert status["ai_suggestions"] == "unavailable" and reason in status["ai_unavailable_reason"]
    assert c.post("/api/suggest", json={"other_work": "I fix laptops."}).json()["available"] is False
    assert c.get("/api/activities", params={"q": "website"}).json()
    assert c.get("/api/activities", params={"q": "fix wifi"}).json() == []  # keyword fallback only
    ok = c.post("/api/profile", json={"weekly_hours": 5, "budget_rm": 0, "degree_level": "degree",
                                      "confirmed": [{"dwa_id": "4.A.3.b.1.f.4", "source": "added_by_you",
                                                     "lane": "other_work"}]}).json()
    assert ok["without_ai_suggestions"] is True
    bad = c.post("/api/profile", json={"weekly_hours": 5, "budget_rm": 0, "degree_level": "degree",
                                       "confirmed": [{"dwa_id": "4.A.3.b.1.f.4", "source": "suggested"}]})
    assert bad.status_code == 422 and "unavailable" in bad.json()["errors"]["confirmed[0]"]


def test_vectors_from_another_model_are_rejected(tmp_path):
    d = copy_data(tmp_path)
    meta_path = d / "vectors" / "dwa_vectors.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["model_sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert_ai_unavailable_but_manual_works(d, "different model")


def test_task_vectors_from_another_task_list_are_rejected(tmp_path):
    d = copy_data(tmp_path)
    meta_path = d / "vectors" / "task_vectors.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["statements_sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert_ai_unavailable_but_manual_works(d, "different task list")


def test_corrupted_task_vectors_are_rejected(tmp_path):
    d = copy_data(tmp_path)
    with open(d / "vectors" / "task_vectors.npy", "r+b") as f:
        f.seek(5000)
        f.write(b"\x00\x01\x02\x03")
    assert_ai_unavailable_but_manual_works(d, "task-statement vector file is corrupted")


def test_missing_task_vectors_report_ai_unavailable(tmp_path):
    d = copy_data(tmp_path)
    (d / "vectors" / "task_vectors.npy").unlink()
    assert_ai_unavailable_but_manual_works(d, "missing vectors/task_vectors.npy")

def test_missing_model_reports_ai_unavailable(tmp_path):
    d = copy_data(tmp_path)
    (d / "model" / "model.onnx").unlink()
    assert_ai_unavailable_but_manual_works(d, "missing model/model.onnx")


def test_altered_model_file_is_rejected(tmp_path):
    d = copy_data(tmp_path)
    with open(d / "model" / "model.onnx", "r+b") as f:
        f.seek(1000)
        f.write(b"\x00\x01\x02\x03")
    assert_ai_unavailable_but_manual_works(d, "model file does not match")


def test_altered_onet_extract_fails_loudly(tmp_path):
    d = copy_data(tmp_path)
    with open(d / "onet" / "dwas.csv", "a", encoding="utf-8") as f:
        f.write("9.9.9,Invented activity.,9.9,Invented\n")
    with pytest.raises(snapshots.SnapshotError, match="onet31-dwas"):
        snapshots.load(d)


HAFIZ_PICKS = ["4.A.3.b.1.d.1", "4.A.3.b.1.d.3", "4.A.3.b.1.c.1", "4.A.3.b.1.b.2", "4.A.3.b.1.f.4", "4.A.2.b.2.b.8"]


def hafiz_request(client, **options):
    hafiz = next(p for p in client.get("/api/personas").json() if p["id"] == "hafiz")
    return {**hafiz, "confirmed": [{"dwa_id": d, "source": "suggested"} for d in HAFIZ_PICKS], "options": options}


def test_results_endpoint_ranks_roles_and_validates_options(client):
    r = client.post("/api/results", json=hafiz_request(client, lane="other_work"))
    assert r.status_code == 200
    body = r.json()
    assert body["heading"] == "closest roles to explore" and body["roles"]
    assert body["without_ai_suggestions"] is False
    bad = client.post("/api/results", json=hafiz_request(client, lane="elsewhere"))
    assert bad.status_code == 422 and "lane" in bad.json()["errors"]
    empty = client.post("/api/results", json={**hafiz_request(client), "confirmed": [], "title_code": None})
    assert empty.status_code == 422 and "activities" in empty.json()["errors"]
    assert client.get("/api/fields", params={"q": "computer science"}).json()


def test_results_without_ai_are_labelled(tmp_path):
    d = copy_data(tmp_path)
    (d / "model" / "model.onnx").unlink()
    c = TestClient(create_app(d))
    req = {"weekly_hours": 5, "budget_rm": 0, "degree_level": "degree", "options": {"lane": "other_work"},
           "confirmed": [{"dwa_id": "4.A.3.b.1.f.4", "source": "added_by_you", "lane": "other_work"}]}
    body = c.post("/api/results", json=req).json()
    assert body["without_ai_suggestions"] is True and body["roles"]


def test_corrupted_release_is_dropped_and_the_others_stay_active(tmp_path):
    d = copy_data(tmp_path)
    with open(d / "aei" / "2026-01-15.csv", "a", encoding="utf-8") as f:
        f.write("task,999999,12.00,3,1\n")
    c = TestClient(create_app(d))
    status = c.get("/api/status").json()
    assert "aei-2026-01-15" in status["rejected_releases"]
    assert status["releases"] == ["2025-09-15", "2026-03-24", "2026-06-26"]
    body = c.post("/api/results", json=hafiz_request(c, lane="other_work")).json()
    assert body["roles"] and body["rejected_releases"]


def test_unparseable_release_fails_loudly_at_build_time(tmp_path):
    from aivancin import aei
    bad = tmp_path / "release.csv"
    bad.write_text("geo_id,facet,value\nGLOBAL,onet_task,1\n", encoding="utf-8")
    with pytest.raises(aei.ReleaseFormatError, match="missing columns"):
        aei.adapt_release(aei.RELEASES[0], bad, [])
    empty = tmp_path / "empty.csv"
    empty.write_text("geo_id,facet,variable,cluster_name,value\n", encoding="utf-8")
    with pytest.raises(aei.ReleaseFormatError, match="no task rows"):
        aei.adapt_release(aei.RELEASES[0], empty, [])


def test_web_interface_is_served_from_bundled_files(client):
    page = client.get("/")
    assert page.status_code == 200 and "Check what we read" in page.text
    for path in ("/static/app.js", "/static/app.css", "/static/base.css", "/static/tokens.css", "/icon/icon-rounded.svg"):
        assert client.get(path).status_code == 200, path
    # the page loads nothing from other hosts, so it works offline
    assert "http://" not in page.text.replace("http://www.w3.org", "") and "https://" not in page.text


def test_routes_and_update_endpoints(client):
    req = hafiz_request(client, weeks=4)
    r = client.post("/api/routes", json=req)
    assert r.status_code == 200
    body = r.json()
    assert 1 <= len(body["routes"]) <= 3 and body["supported_destinations"]["v"] == 12
    target = body["routes"][0]["code"]
    u = client.post("/api/update", json=hafiz_request(client, weeks=4, target=target, from_release="2025-09-15",
                                                       to_release="2026-06-26"))
    assert u.status_code == 200 and u.json()["target"] == target
    bad = client.post("/api/routes", json=hafiz_request(client, weeks=6))
    assert bad.status_code == 422 and "weeks" in bad.json()["errors"]
    assert client.post("/api/update", json=hafiz_request(client, weeks=4)).status_code == 422
