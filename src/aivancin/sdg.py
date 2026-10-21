"""SDG tags with reasons (SPEC 11.5). Each tag names the target and why it applies to this step or route.

SDG 5 is national context from DOSM, never an attribute of the person and never a factor in any order.
"""
SRC = "un-sdg-targets"
TARGETS = {  # wording checked on sdgs.un.org, 21 Oct 2026
    "4.3": "By 2030, ensure equal access for all women and men to affordable and quality technical, vocational and "
           "tertiary education, including university",
    "4.4": "By 2030, substantially increase the number of youth and adults who have relevant skills, including technical "
           "and vocational skills, for employment, decent jobs and entrepreneurship",
    "8.2": "Achieve higher levels of economic productivity through diversification, technological upgrading and "
           "innovation, including through a focus on high-value added and labour-intensive sectors",
    "8.5": "By 2030, achieve full and productive employment and decent work for all women and men, including for young "
           "people and persons with disabilities, and equal pay for work of equal value",
    "5": "Achieve gender equality and empower all women and girls",
}


def _names(gaps):
    return "; ".join(g["name"].rstrip(".") for g in gaps)


def step_tags(step, catalogue_src):
    """4.4 on every step, naming the gaps it serves; 4.3 when it costs RM0; 8.2 when its checkpoint adds AI checking
    or it is an AI-practice step."""
    if step["kind"] == "prerequisite":
        why = "Builds the base a later step in this block requires: skills for employment."
    elif step["kind"] == "ai_practice":
        why = f"Builds skill in directing and checking AI on: {_names(step['serves'])}." if step["serves"] else \
            "Builds skill in directing and checking AI output, a skill for employment."
    else:
        why = f"Builds skills for employment: {_names(step['serves'])}."
    tags = [{"goal": "4.4", "reason": why, "src": [SRC, "onet31-task-dwas", catalogue_src]}]
    if step["cost_rm"]["v"] == 0:
        tags.append({"goal": "4.3", "reason": "Free online course: affordable lifelong learning.", "src": [SRC, catalogue_src]})
    if step["form"] == "ai" or step["kind"] == "ai_practice":
        tags.append({"goal": "8.2", "reason": "Practises directing and checking AI output: productivity through "
                                              "technological upgrading.", "src": [SRC, catalogue_src]})
    return tags
