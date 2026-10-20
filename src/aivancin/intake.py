"""Intake: sentence splitting, the negation and aspiration guard, AI activity suggestions and confirmation.

Nothing the model suggests enters a profile without an explicit tick, and a suggestion that only came
from a negated or aspirational sentence needs a second, deliberate override.
"""
import re

import numpy as np

FIELDS = ("current_job", "other_work", "studies")
LEVELS = ("diploma", "degree")
TOP_K = 5

_BOUNDARY = re.compile(r"[.!?;]+(?=\s|$)|\n")
_LETTER = re.compile(r"[^\W\d_]")

# Initial English cue list (SPEC section 2), plus the common n't contractions.
_NEGATION = [r"never", r"not", r"no longer", r"cannot"] + [
    rf"{stem}'?t" for stem in ("haven", "hasn", "don", "didn", "doesn", "can", "won", "isn", "wasn", "aren", "weren", "couldn")
]
_ASPIRATION = [r"would like to", r"i'd like to", r"wants? to", r"hope to", r"plan to", r"planning to", r"learning to"]
_NEGATION_RE = re.compile(r"\b(?:" + "|".join(_NEGATION) + r")\b", re.IGNORECASE)
_ASPIRATION_RE = re.compile(r"\b(?:" + "|".join(_ASPIRATION) + r")\b", re.IGNORECASE)


class IntakeError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def split_sentences(text):
    """Return (start, end, sentence) spans; end is exclusive so the client can highlight the original text."""
    spans, start = [], 0
    cuts = [m.end() for m in _BOUNDARY.finditer(text)] + [len(text)]
    for end in cuts:
        seg = text[start:end]
        lead = len(seg) - len(seg.lstrip())
        body = seg.strip()
        if body.endswith("\n"):
            body = body.rstrip()
        if _LETTER.search(body):
            s = start + lead
            spans.append((s, s + len(body), body))
        start = end
    return spans


def guard(sentence):
    """Flag a sentence that says the work was not done, or is only wanted. Returns None or {kind, cue}."""
    norm = sentence.replace("’", "'").replace("‘", "'")
    m = _NEGATION_RE.search(norm)
    if m:
        return {"kind": "negated", "cue": m.group(0)}
    m = _ASPIRATION_RE.search(norm)
    if m:
        return {"kind": "aspirational", "cue": m.group(0)}
    return None


def suggest(texts, encoder, vectors, onet, k=TOP_K):
    """Top-k activities per sentence by cosine similarity, merged so each activity appears once."""
    sentences = []
    for f in FIELDS:
        for start, end, body in split_sentences(texts.get(f) or ""):
            sentences.append({"index": len(sentences), "field": f, "start": start, "end": end,
                              "text": body, "guard": guard(body)})
    merged = {}
    for s in sentences:
        # One sentence per call keeps each sentence's scores independent of the rest of the input.
        q = encoder.encode([s["text"]])[0]
        sims = vectors @ q
        order = np.argsort(-sims, kind="stable")[:k]
        s["top"] = []
        for rank, i in enumerate(order, 1):
            dwa_id, score = onet.dwa_ids[i], round(float(sims[i]), 4)
            s["top"].append(dwa_id)
            entry = merged.setdefault(dwa_id, {"dwa_id": dwa_id, "name": onet.dwa_names[dwa_id], "triggers": []})
            entry["triggers"].append({"sentence": s["index"], "field": s["field"], "rank": rank, "score": score,
                                      "guarded": s["guard"] is not None})
    suggestions = []
    for entry in merged.values():
        entry["best_score"] = max(t["score"] for t in entry["triggers"])
        entry["needs_override"] = all(t["guarded"] for t in entry["triggers"])
        entry["lanes"] = sorted({t["field"] for t in entry["triggers"] if not t["guarded"] or entry["needs_override"]},
                                key=FIELDS.index)
        suggestions.append(entry)
    suggestions.sort(key=lambda e: (-e["best_score"], e["dwa_id"]))
    return {"sentences": sentences, "suggestions": suggestions}


def _tokens(q):
    return [t for t in re.findall(r"[a-z0-9]+", (q or "").lower()) if len(t) >= 2]


def search_activities(onet, q, limit=20):
    toks = _tokens(q)
    if not toks:
        return []
    hits = [d for d in onet.dwa_ids if all(t in onet.dwa_names[d].lower() for t in toks)]
    hits.sort(key=lambda d: (onet.dwa_names[d].lower(), d))
    return [{"dwa_id": d, "name": onet.dwa_names[d]} for d in hits[:limit]]


def search_occupations(onet, q, limit=20):
    toks = _tokens(q)
    if not toks:
        return []
    hits = [c for c, t in onet.occupations.items() if all(x in t.lower() for x in toks)]
    hits.sort(key=lambda c: (onet.occupations[c].lower(), c))
    return [{"code": c, "title": onet.occupations[c]} for c in hits[:limit]]


def title_activities(onet, code):
    ids = {d for _, d in onet.occupation_dwas.get(code, [])}
    return sorted(({"dwa_id": d, "name": onet.dwa_names[d]} for d in ids), key=lambda a: (a["name"].lower(), a["dwa_id"]))


def search_fields(cip, q, limit=20):
    """Degree fields from the NCES CIP 2020 list, by keyword."""
    toks = _tokens(q)
    if not toks:
        return []
    hits = [c for c, rec in cip.items() if all(t in rec["title"].lower() for t in toks)]
    hits.sort(key=lambda c: (cip[c]["title"].lower(), c))
    return [{"cip": c, "title": cip[c]["title"]} for c in hits[:limit]]


def build_profile(req, onet, suggestion_result, cip=None):
    """Validate an intake submission and return the confirmed profile.

    `suggestion_result` is recomputed by the server from the submitted text (None when AI suggestions
    are unavailable), so a client cannot confirm an activity the model never suggested or skip the guard.
    """
    errors = {}
    hours = req.get("weekly_hours")
    if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0:
        errors["weekly_hours"] = "Enter the hours you can spend each week (more than 0)."
    budget = req.get("budget_rm")
    if not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget < 0:
        errors["budget_rm"] = "Enter a budget of RM0 or more."
    level = req.get("degree_level")
    if level not in LEVELS:
        errors["degree_level"] = "Choose diploma or degree."
    code = req.get("title_code") or None
    if code is not None and code not in onet.occupations:
        errors["title_code"] = "Choose a job title from the list."
    cip_code = req.get("cip_code") or None
    if cip_code is not None and cip is not None and cip_code not in cip:
        errors["cip_code"] = "Choose what you studied from the list."

    activities = {}

    def add(dwa_id, source, lanes, override=False):
        entry = activities.setdefault(dwa_id, {"dwa_id": dwa_id, "name": onet.dwa_names[dwa_id], "sources": [],
                                               "lanes": [], "guard_override": False})
        if source not in entry["sources"]:
            entry["sources"].append(source)
        entry["lanes"] = sorted(set(entry["lanes"]) | set(lanes), key=FIELDS.index)
        entry["guard_override"] = entry["guard_override"] or override

    if code is not None and "title_code" not in errors:
        unticked = set(req.get("unticked_title_activities") or [])
        for a in title_activities(onet, code):
            if a["dwa_id"] not in unticked:
                add(a["dwa_id"], "title", ["current_job"])

    by_id = {s["dwa_id"]: s for s in (suggestion_result or {}).get("suggestions", [])}
    for i, c in enumerate(req.get("confirmed") or []):
        dwa_id, source = c.get("dwa_id"), c.get("source")
        key = f"confirmed[{i}]"
        if dwa_id not in onet.dwa_names:
            errors[key] = "Unknown activity."
        elif source == "suggested":
            s = by_id.get(dwa_id)
            if suggestion_result is None:
                errors[key] = "AI suggestions are unavailable; add activities by search instead."
            elif s is None:
                errors[key] = "This activity was not suggested for the text you entered."
            elif s["needs_override"] and c.get("guard_override") is not True:
                errors[key] = "You said you have not done this yet. Confirm it again if you have done it."
            else:
                add(dwa_id, "suggested", s["lanes"], override=s["needs_override"])
        elif source == "added_by_you":
            lane = c.get("lane")
            if lane not in FIELDS:
                errors[key] = "Choose where you did this: current job, other work or studies."
            else:
                add(dwa_id, "added_by_you", [lane])
        else:
            errors[key] = "Unknown activity source."

    if not activities and "title_code" not in errors:
        errors["activities"] = "Tick at least one activity or choose your current job title."
    if errors:
        raise IntakeError(errors)
    return {
        "title_code": code,
        "degree_field": (req.get("degree_field") or "").strip(),
        "cip_code": cip_code if "cip_code" not in errors else None,
        "degree_level": level,
        "weekly_hours": hours,
        "budget_rm": budget,
        "without_ai_suggestions": suggestion_result is None,
        "activities": sorted(activities.values(), key=lambda a: a["dwa_id"]),
    }
