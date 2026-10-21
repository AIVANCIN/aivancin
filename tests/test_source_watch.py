"""Source watch (SPEC 11.7, C14) on recorded responses; the test network block stays on, nothing is fetched."""
import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_sources", ROOT / "tools" / "check_sources.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

TREE_CURRENT = json.dumps([{"type": "directory", "path": "release_2025_09_15"}, {"type": "directory", "path": "release_2026_06_26"},
                           {"type": "file", "path": "README.md"}])
TREE_NEWER = json.dumps([{"type": "directory", "path": "release_2026_06_26"}, {"type": "directory", "path": "release_2026_10_02"}])
ONET_PAGE = "<h2>O*NET 31.0 Database</h2><p>Previous: O*NET 30.1 Database</p>"
DOSM_CSV = "date,sex,occupation,recipients,wages_median,wages_mean\n2024-01-01,both,overall,1,2,3\n2025-01-01,both,overall,1,2,3\n"
OUTLOOK = "<div>Projected growth (2025-2035)\n   Faster than average</div>"


def recorded(pages):
    def get(url, timeout=60):
        if url not in pages:
            raise urllib.error.URLError("not recorded")
        body = pages[url]
        if isinstance(body, Exception):
            raise body
        return body
    return get


def test_each_source_is_current_on_matching_upstream():
    get = recorded({cs.AEI_TREE: TREE_CURRENT, cs.ONET_DB: ONET_PAGE, cs.OUTLOOK_PAGE: OUTLOOK,
                    **{u: DOSM_CSV for u in cs.DOSM.values()}})
    assert cs.check_aei(get)["status"] == "current"
    assert cs.check_onet(get)["status"] == "current"
    assert cs.check_dosm(get, "dosm-wages-occupation", bundled="2025-01-01")["status"] == "current"
    assert cs.check_outlook(get, bundled="2025-2035")["status"] == "current"


def test_newer_upstream_versions_are_reported_not_applied():
    aei = cs.check_aei(recorded({cs.AEI_TREE: TREE_NEWER}))
    assert aei["status"] == "newer available" and aei["upstream"] == "2026-10-02" and "adapter" in aei["detail"]
    onet = cs.check_onet(recorded({cs.ONET_DB: "<h2>O*NET 32.0 Database</h2>"}))
    assert onet["status"] == "newer available" and onet["upstream"] == "32.0"
    dosm = cs.check_dosm(recorded({cs.DOSM["dosm-sru-age"]: DOSM_CSV.replace("2025-01-01", "2026-07-01")}), "dosm-sru-age",
                         bundled="2026-04-01")
    assert dosm["status"] == "newer available" and dosm["upstream"] == "2026-07-01"
    out = cs.check_outlook(recorded({cs.OUTLOOK_PAGE: OUTLOOK.replace("2025-2035", "2026-2036")}), bundled="2025-2035")
    assert out["status"] == "newer available" and out["upstream"] == "2026-2036"


@pytest.mark.parametrize("body", [urllib.error.HTTPError("u", 503, "down", None, None), TimeoutError()])
def test_a_failed_check_is_unknown(body):
    get = recorded({cs.AEI_TREE: body, cs.ONET_DB: body, cs.OUTLOOK_PAGE: body, **{u: body for u in cs.DOSM.values()}})
    res = cs.check_all(get)
    assert {r["status"] for r in res["sources"]} == {"unknown"} and all("check failed" in r["detail"] for r in res["sources"])


def test_a_changed_page_is_unknown_not_current():
    get = recorded({cs.AEI_TREE: json.dumps([{"type": "directory", "path": "data"}]), cs.ONET_DB: "<p>Database</p>",
                    cs.OUTLOOK_PAGE: "<p>Outlook</p>", **{u: "a,b\n1,2\n" for u in cs.DOSM.values()}})
    res = cs.check_all(get)
    assert {r["status"] for r in res["sources"]} == {"unknown"}


def test_the_watch_never_touches_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "OUT", tmp_path / "status.json")
    before = {p: p.read_bytes() for p in (ROOT / "data").rglob("*.csv")}
    monkeypatch.setattr(cs, "fetch", recorded({cs.AEI_TREE: TREE_NEWER}))
    cs.main()
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))["sources"][0]["status"] == "newer available"
    assert all(p.read_bytes() == b for p, b in before.items())


def test_bundled_status_is_in_the_manifest_and_api(snaps):
    st = snaps.source_status
    assert st and {r["source"] for r in st["sources"]} == {"aei", "onet", "dosm-wages-occupation", "dosm-sru-age",
                                                            "dosm-sru-sex", "onet-online-outlook"}
    assert snaps.source("aivancin-source-status")["release"] == st["checked_on"]
