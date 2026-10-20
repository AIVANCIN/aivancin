"""FastAPI service, started by src/main.py."""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import intake, snapshots
from .encoder import AIUnavailable, load_ai

AI_UNAVAILABLE = "AI suggestions are unavailable. You can still add activities by searching the list."


def create_app(data_dir=snapshots.DEFAULT_DATA_DIR):
    snaps = snapshots.load(data_dir)
    onet = snaps.onet
    try:
        encoder, vectors = load_ai(snaps)
        ai_reason = None
    except AIUnavailable as exc:
        encoder = vectors = None
        ai_reason = str(exc)
    personas = json.loads((Path(data_dir) / "personas.json").read_text(encoding="utf-8"))

    app = FastAPI(title="AIVANCIN")

    def suggestions_for(texts):
        if encoder is None:
            return None
        return intake.suggest({f: texts.get(f) or "" for f in intake.FIELDS}, encoder, vectors, onet)

    @app.get("/api/status")
    def status():
        return {"ai_suggestions": "available" if encoder else "unavailable",
                "ai_unavailable_reason": ai_reason,
                "snapshots": [{k: r[k] for k in ("source_id", "title", "release", "access_date", "licence")}
                              for r in snaps.manifest["snapshots"]]}

    @app.get("/api/personas")
    def list_personas():
        return personas

    @app.get("/api/activities")
    def activities(q: str = ""):
        return intake.search_activities(onet, q)

    @app.get("/api/occupations")
    def occupations(q: str = ""):
        return intake.search_occupations(onet, q)

    @app.get("/api/occupations/{code}/activities")
    def occupation_activities(code: str):
        if code not in onet.occupations:
            raise HTTPException(404, "Unknown job title.")
        return {"code": code, "title": onet.occupations[code], "activities": intake.title_activities(onet, code)}

    @app.post("/api/suggest")
    def suggest(texts: dict):
        result = suggestions_for(texts)
        if result is None:
            return {"available": False, "message": AI_UNAVAILABLE}
        return {"available": True, **result}

    @app.post("/api/profile")
    def profile(req: dict):
        try:
            return intake.build_profile(req, onet, suggestions_for(req))
        except intake.IntakeError as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})

    return app


app = create_app()
