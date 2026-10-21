"""FastAPI service, started by src/main.py."""
import datetime as dt
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import intake, snapshots
from .cohort import Cohort
from .encoder import AIUnavailable, load_ai
from .matching import Matcher
from .report import Reporter, ReportError, fig
from .routes import Planner

STATIC = Path(__file__).resolve().parent / "static"
ICON = Path(__file__).resolve().parents[2] / "assets" / "icon"
ART = Path(__file__).resolve().parents[2] / "assets" / "art"
FONTS = Path(__file__).resolve().parents[2] / "assets" / "fonts"

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

    matcher = Matcher(snaps)
    reporter = Reporter(snaps, matcher)
    planner = Planner(snaps, matcher, reporter)
    cohort = Cohort(snaps, planner)
    cohort_cache = {}
    app = FastAPI(title="AIVANCIN")
    # Route responses carry every figure with its sources (several hundred kB of JSON); compress them.
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    def suggestions_for(texts):
        if encoder is None:
            return None
        return intake.suggest({f: texts.get(f) or "" for f in intake.FIELDS}, encoder, vectors, onet)

    @app.get("/api/status")
    def status():
        return {"ai_suggestions": "available" if encoder else "unavailable",
                "ai_unavailable_reason": ai_reason,
                "rejected_releases": snaps.rejected,
                "releases": snaps.evidence.ids if snaps.evidence else [],
                "source_status": snaps.source_status or None,
                "checks": snaps.checks or None,
                "supported_destinations": fig(len(planner.dests), "aivancin-routes") if planner.dests else None,
                "snapshots": [{k: r.get(k) for k in ("source_id", "title", "publisher", "url", "release", "access_date",
                                                     "licence", "window_start", "window_end")}
                              for r in snaps.manifest["snapshots"]]}

    @app.get("/api/personas")
    def list_personas():
        return personas

    @app.get("/api/activities")
    def activities(q: str = ""):
        return intake.search_activities(onet, q, encoder=encoder, index=vectors)

    @app.get("/api/occupations")
    def occupations(q: str = ""):
        return intake.search_occupations(onet, q)

    @app.get("/api/occupations/{code}/activities")
    def occupation_activities(code: str):
        if code not in onet.occupations:
            raise HTTPException(404, "Unknown job title.")
        return {"code": code, "title": onet.occupations[code], "activities": intake.title_activities(onet, code)}

    @app.get("/api/fields")
    def fields(q: str = ""):
        return intake.search_fields(snaps.cip, q)

    @app.post("/api/suggest")
    def suggest(texts: dict):
        result = suggestions_for(texts)
        if result is None:
            return {"available": False, "message": AI_UNAVAILABLE}
        return {"available": True, **result}

    @app.post("/api/profile")
    def profile(req: dict):
        try:
            return intake.build_profile(req, onet, suggestions_for(req), snaps.cip)
        except intake.IntakeError as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})

    @app.post("/api/results")
    def results(req: dict):
        """Rebuild the confirmed profile from the submitted intake, then rank and explain roles."""
        try:
            prof = intake.build_profile(req, onet, suggestions_for(req), snaps.cip)
            return reporter.report(prof, req.get("options") or {})
        except (intake.IntakeError, ReportError) as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})

    @app.post("/api/routes")
    def routes(req: dict):
        """Compare up to three supported routes with their first learning blocks (SPEC section 10)."""
        try:
            prof = intake.build_profile(req, onet, suggestions_for(req), snaps.cip)
            return planner.routes(prof, req.get("options") or {})
        except (intake.IntakeError, ReportError) as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})

    @app.post("/api/explore")
    def explore(req: dict):
        """Every supported role before the person describes their work (SPEC section 10.7)."""
        try:
            return planner.explore(req.get("options") or {})
        except ReportError as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})

    @app.get("/api/cohort")
    def cohort_view():
        """The bundled synthetic cohort's routes, shared gaps, shared steps and SDG counts (SPEC 11.10). No input."""
        today = dt.date.today()
        if today not in cohort_cache:
            cohort_cache.clear()
            cohort_cache[today] = cohort.summary(today)
        if cohort_cache[today] is None:
            raise HTTPException(status_code=404, detail="No cohort is bundled.")
        return cohort_cache[today]

    @app.post("/api/update")
    def update(req: dict):
        """What changed in the chosen route's plan between two evidence bundles (SPEC section 10.6)."""
        try:
            prof = intake.build_profile(req, onet, suggestions_for(req), snaps.cip)
            return planner.compare(prof, req.get("options") or {})
        except (intake.IntakeError, ReportError) as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})

    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
        app.mount("/art", StaticFiles(directory=ART), name="art")
        app.mount("/icon", StaticFiles(directory=ICON), name="icon")
        app.mount("/fonts", StaticFiles(directory=FONTS), name="fonts")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(STATIC / "index.html")

    return app


app = create_app()
