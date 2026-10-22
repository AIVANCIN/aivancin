"""Fairness (SPEC 11.7, C13): names, gendered words and stated ages do not change suggestions or routes, and the
person still sees their own words."""
import importlib.util
from pathlib import Path

from aivancin import intake

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("c13_fairness", ROOT / "tools" / "checks" / "c13_fairness.py")
c13 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c13)


def test_c13_both_pair_sets_give_identical_suggestions_and_routes():
    for name in ("fairness_pairs.json", "fairness_pairs_2.json"):
        res = c13.run(ROOT / "tests" / "acceptance" / name)
        assert res["pass"], (name, [d for s in res["sentences"] for d in s["differences"]][:3])
        assert res["sentence_variants"] >= 98 and res["profiles_of"] == 4


def test_neutral_copy_only_feeds_the_encoder(snaps, ai):
    encoder, vectors = ai
    text = "My name is Kavitha and at 34 I keep the books for my uncle's shop. Her son fixes phones."
    sug = intake.suggest({"other_work": text}, encoder, vectors, snaps.onet)
    assert [s["text"] for s in sug["sentences"]] == ["My name is Kavitha and at 34 I keep the books for my uncle's shop.",
                                                     "Her son fixes phones."]
    # 22 Oct: the self-introduction is no longer read at all (a name such as "May" is also a word).
    # Since 22 Oct (SPEC 11.12) "the books" is also written as O*NET's "financial records" in the copy.
    assert intake.neutral_text(sug["sentences"][0]["text"], snaps.onet) == "at 30 I keep the financial records for my family's shop."
    # The guard reads the person's words, not the neutral copy; since 22 Oct "Her son" is a person subject too.
    assert sug["sentences"][1]["guard"] == {"kind": "someone else", "cue": "Her son"}
    assert intake.guard("My son fixes phones.") == {"kind": "someone else", "cue": "My son"}


def test_tools_and_places_keep_their_meaning(snaps):
    for text in ("I update the Excel sheets in Microsoft Teams.", "I sell batik on Shopee from Kuala Lumpur.",
                 "I set up CCTV and SIEM alerts for the security team.", "Every Friday I back up files to Google Drive."):
        assert intake.neutral_text(text, snaps.onet) == text
    # IT is spelled out since 22 Oct (SPEC 11.12); the other short forms stay as written.
    assert intake.neutral_text("I set up CCTV and SIEM alerts for the IT team.", snaps.onet) ==         "I set up CCTV and SIEM alerts for the information technology team."
