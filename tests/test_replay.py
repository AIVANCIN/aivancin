"""The evidence-to-plan replay (tools/checks/replay.py) on its fixture, as part of the suite."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("replay", ROOT / "tools" / "checks" / "replay.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def test_replay_passes_every_check():
    res = replay.run()
    failed = [c for c in res["checks"] if not c["pass"]]
    assert not failed and res["of"] == 12, failed


def test_replay_fails_when_the_expected_change_is_wrong(tmp_path):
    """The replay can fail: expecting a different step to change is reported, not absorbed."""
    import json
    fx = json.loads(replay.FIXTURE.read_text(encoding="utf-8"))
    fx["expected"]["changed"][0]["step"] = "learn.wwl.describe-authentication-authorization-cybersecurity"
    bad = tmp_path / "fixture.json"
    bad.write_text(json.dumps(fx), encoding="utf-8")
    res = replay.run(bad)
    assert not res["all_pass"]
    assert [c["check"] for c in res["checks"] if not c["pass"]] == ["expected change only", "dated provenance on the change"]
