<p align="center"><img src="assets/icon/icon-readme.png" width="160" alt="AIVANCIN app icon"></p>

# AIVANCIN

An AI reads what an underemployed graduate actually does, in their own words, and shows the closest graduate jobs that use that work, what each still requires, what AI is already doing in those tasks, and a free, verified plan for the gaps.

Built for SDC 2026, Domain One: The Aiversecity Future-Ready Workforce Domain.

## Status

Work in progress. This first part is the intake engine and its API:

- free text about the current job, other work and studies is split into sentences;
- a local sentence encoder ranks O*NET 31.0's 2,087 detailed work activities by meaning for each sentence (top five) and records which sentence triggered each suggestion;
- a deterministic guard flags negated or aspirational sentences ("I have never...", "I want to..."); their suggestions need a second, deliberate confirmation;
- nothing is accepted without an explicit tick; activities can also be added by keyword search, and a chosen job title contributes its own O*NET activities, each of which can be unticked;
- if the model or its vectors are missing or do not match, the API says AI suggestions are unavailable and manual search keeps working.

Role matching, AI-usage evidence, pathways, SDG tags and the web interface come next.

## Run

Tested with Python 3.13.

```sh
python -m venv .venv
.venv/Scripts/activate          # Windows; use .venv/bin/activate elsewhere
pip install -r requirements-dev.txt
uvicorn aivancin.app:app        # API at http://127.0.0.1:8000, docs at /docs
pytest                          # runs offline; network access is blocked in tests
```

Everything the app needs is bundled in `data/`; it makes no network requests and needs no API key. `python tools/build_snapshots.py` rebuilds `data/` from the original sources (pinned model revision and hashes) and reproduces the committed files byte for byte.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Whether AI suggestions are available, and the bundled snapshots |
| `GET /api/personas` | Synthetic demo personas (Hafiz, Priya) |
| `POST /api/suggest` | `{current_job, other_work, studies}` text to sentences and activity suggestions |
| `GET /api/activities?q=` | Keyword search of the activity list |
| `GET /api/occupations?q=`, `GET /api/occupations/{code}/activities` | Job title search and a title's activities |
| `POST /api/profile` | Validates confirmations, hours, budget and degree level; returns the confirmed profile or field-level errors |

## Structure

```
aivancin/   snapshots.py (hashed data loading), encoder.py (ONNX encoder, vector checks), intake.py, app.py
data/       manifest.json, onet/ extracts, model/, vectors/, personas.json
tools/      build_snapshots.py
tests/      offline tests
assets/     app icon
```

## Data, model and licences

- **O*NET 31.0** (files dated 25 August 2026): this product includes information from the O*NET 31.0 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license. O*NET® is a trademark of USDOL/ETA. AIVANCIN has modified all or some of this information (column selection and sorting). USDOL/ETA has not approved, endorsed, or tested these modifications.
- **all-MiniLM-L6-v2** by sentence-transformers, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, quantized ONNX, [Apache 2.0](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2). Runs locally on CPU with ONNX Runtime.
- Every snapshot's source URL, release, access date, licence and SHA-256 hashes are in `data/manifest.json`.
- Personas are synthetic. No real personal data is used.
- Libraries: FastAPI, Uvicorn, ONNX Runtime, Hugging Face Tokenizers, NumPy; pytest and HTTPX for tests.

## AI use

The product's AI is the local all-MiniLM-L6-v2 sentence encoder, used only to suggest activities from the user's text; the user confirms every activity. No generative model runs in the product. Development used an AI coding assistant (Claude Code).

## Licence

Apache License 2.0, see [LICENSE](LICENSE).
