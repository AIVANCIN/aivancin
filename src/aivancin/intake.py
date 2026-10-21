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
# SPEC 11.7: plans without a cue of the first list.
_ASPIRATION += [r"my dream is", r"one day i", r"someday", r"my goal is to", r"i'm thinking of", r"i am thinking of"]
_NEGATION_RE = re.compile(r"\b(?:" + "|".join(_NEGATION) + r")\b", re.IGNORECASE)
_ASPIRATION_RE = re.compile(r"\b(?:" + "|".join(_ASPIRATION) + r")\b", re.IGNORECASE)
# SPEC 11.7: someone else's work. A third-person subject at the start of the sentence followed by a verb (not "and",
# "or", a comma or a possessive), for example "My cousin repairs computers" or "She designs the websites".
_KIN = "cousin|brother|sister|friend|father|mother|uncle|aunt|husband|wife|son|daughter"
_OTHER_RE = re.compile(rf"^\W*((?:my\s+(?:{_KIN})|he|she|they))(?!'s\b|\s*,|\s+(?:and|or|&)\b)\s+[a-z]", re.IGNORECASE)
# Refined 21 Oct (C12): a sentence in which the person also acts or is asked ("They pay me to set up their wifi",
# "He showed me how to build spreadsheets, and now I make them") describes their own work and is not flagged.
_FIRST_PERSON_RE = re.compile(r"\b(?:i|me|myself)\b|\bi'(?:m|ve|d|ll)\b", re.IGNORECASE)

# Capitalised short forms such as IOC, SIEM or KYC: the small encoder does not know most of them and matches the
# rest of the sentence instead, so the review asks for plain words. Everyday ones it reads well are left alone.
_SHORT_FORM_RE = re.compile(r"\b([A-Z][A-Z0-9&]{1,5})(?:'?s)?\b")
# Checked 20 Oct: "IT support" and "CVs" read badly, so IT and CV are flagged; PC, HR, CCTV and GPS read well.
_EVERYDAY_SHORT_FORMS = {"HR", "PC", "CEO", "TV", "AM", "PM", "OK", "UK", "US", "USA", "GPS", "PDF", "SMS", "ATM",
                         "USB", "CCTV", "ID"}


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
    m = _OTHER_RE.match(norm)
    if m and not _FIRST_PERSON_RE.search(norm):
        return {"kind": "someone else", "cue": m.group(1)}
    return None


def short_forms(sentence):
    """Capitalised short forms in a sentence, in order, without the everyday ones. A sentence written mostly in
    capitals is shouting, not short forms, and returns none."""
    letters = [c for c in sentence if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        return []
    found = []
    for m in _SHORT_FORM_RE.finditer(sentence):
        token = m.group(1)
        if sum(c.isalpha() for c in token) >= 2 and token not in _EVERYDAY_SHORT_FORMS and token not in found:
            found.append(token)
    return found


# Fairness (SPEC 11.7, C13): names, gendered words and stated ages changed suggestions for the same work, so the
# encoder reads a neutral copy of each sentence; the person always sees their own words. Gendered words become
# neutral ones, a stated age becomes a fixed one, and a capitalised word the O*NET text never uses (a personal name,
# in practice) becomes one fixed name.
_KIN_WORDS = ("brother sister father mother dad mum mom son daughter husband wife uncle aunt auntie nephew niece "
              "grandfather grandmother grandpa grandma cousin").split()
_NEUTRAL = {"he": "they", "she": "they", "him": "them", "her": "their", "his": "their", "hers": "theirs",
            "himself": "themself", "herself": "themself", "boyfriend": "partner", "girlfriend": "partner",
            "mr": "", "mrs": "", "ms": "", "miss": "", "encik": "", "puan": "", "cik": "",
            # every relative reads as "family" ("my uncle's shop" and "my aunt's shop" are the same work)
            **{w: "family" for w in _KIN_WORDS}, **{w + "s": "family" for w in _KIN_WORDS}}
_GENDERED_RE = re.compile(r"\b(" + "|".join(sorted(_NEUTRAL, key=len, reverse=True)) + r")\b\.?", re.IGNORECASE)
_AGE_RE = re.compile(r"\b(i am|i'm|aged|at|age)\s+\d{1,2}\b|\b\d{1,2}(-|\s)years?(-|\s)old\b", re.IGNORECASE)
_WORD_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")
_COMMON = set("""a an the and or of to in on at for with by from as is are be am was were i my me we our you your they
their it its this that these those every each all some any one two after before during when while if but so then also
just only about over under into out up down here there now today tomorrow yesterday weekend weekends monday tuesday
wednesday thursday friday saturday sunday mondays tuesdays wednesdays thursdays fridays saturdays sundays january
february march april may june july august september october november december morning mornings evening evenings night
nights last next first once twice sometimes usually often name bachelor bachelors master masters diploma degree
certificate foundation university college polytechnic institute academy school form spm stpm""".split())
# Malaysian places and everyday platforms are not names; keep their meaning for the encoder.
_COMMON |= set("""malaysia malaysian kuala lumpur selangor penang pulau pinang johor bahru melaka malacca perak ipoh kedah perlis
kelantan terengganu pahang negeri sembilan sabah sarawak kuching kota kinabalu putrajaya cyberjaya klang shah alam petaling
jaya subang singapore shopee lazada grab foodpanda tiktok instagram facebook whatsapp telegram canva youtube google
gmail zoom""".split())
_VOCAB = {}


def _vocabulary(onet):
    key = id(onet)
    if key not in _VOCAB:
        words = set(_COMMON)
        for text in list(onet.dwa_names.values()) + list(onet.occupations.values()) + [t["task"] for t in onet.tasks.values()]:
            words.update(w.lower() for w in re.findall(r"[A-Za-z]+", text))
        for tools in onet.software.values():
            for t in tools:
                words.update(w.lower() for w in re.findall(r"[A-Za-z]+", t["name"]))
        _VOCAB[key] = words
    return _VOCAB[key]


def neutral_text(sentence, onet):
    """The copy of a sentence the encoder reads: names, gendered words and stated ages made neutral."""
    vocab = _vocabulary(onet)
    out = _AGE_RE.sub(lambda m: (m.group(1) + " 30") if m.group(1) else "30-year-old", sentence)
    out = _WORD_RE.sub(lambda m: m.group(0) if all(w.lower() in vocab for w in m.group(0).split()) else "Sam", out)
    out = _GENDERED_RE.sub(lambda m: _NEUTRAL[m.group(1).lower()], out)
    return re.sub(r"\s{2,}", " ", out).strip()


def suggest(texts, encoder, index, onet, k=TOP_K):
    """Top-k activities per sentence by the activity index's score (encoder.ActivityIndex), merged so each activity
    appears once."""
    sentences = []
    for f in FIELDS:
        for start, end, body in split_sentences(texts.get(f) or ""):
            sentences.append({"index": len(sentences), "field": f, "start": start, "end": end,
                              "text": body, "guard": guard(body), "short_forms": short_forms(body)})
    merged = {}
    for s in sentences:
        # One sentence per call keeps each sentence's scores independent of the rest of the input.
        q = encoder.encode([neutral_text(s["text"], onet)])[0]
        sims = index.scores(q)
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


def search_activities(onet, q, limit=20, encoder=None, index=None, ranked_limit=10):
    """Manual search. Without the encoder: names containing every word, alphabetically. With it: those names first,
    then the closest activities by meaning, both in the encoder's order, because casual words such as "fix wifi"
    share no word with "Resolve computer network problems"."""
    toks = _tokens(q)
    if not toks:
        return []
    hits = [i for i, d in enumerate(onet.dwa_ids) if all(t in onet.dwa_names[d].lower() for t in toks)]
    if encoder is None or index is None:
        hits.sort(key=lambda i: (onet.dwa_names[onet.dwa_ids[i]].lower(), onet.dwa_ids[i]))
        return [{"dwa_id": onet.dwa_ids[i], "name": onet.dwa_names[onet.dwa_ids[i]], "match": "keyword"}
                for i in hits[:limit]]
    sims = index.scores(encoder.encode([q.strip()])[0])
    literal = set(hits)
    order = sorted(range(len(sims)), key=lambda i: (i not in literal, -sims[i], onet.dwa_ids[i]))[:ranked_limit]
    return [{"dwa_id": onet.dwa_ids[i], "name": onet.dwa_names[onet.dwa_ids[i]],
             "match": "keyword" if i in literal else "meaning"} for i in order]


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
