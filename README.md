# Provenance Guard

Provenance Guard is a Flask backend for classifying text submissions on a creative sharing platform. It combines two detection signals, returns a confidence-aware transparency label, records every decision in a structured audit log, and lets creators appeal a classification.

The system is designed to be cautious: false positives are harmful on a writing platform, so middle-range scores produce an `uncertain` label instead of accusing a creator of using AI.

## Features

- `POST /submit` accepts text and a creator ID, then returns attribution, confidence, signal scores, label text, and a `content_id`.
- Multi-signal pipeline with an LLM attribution signal and a transparent stylometric heuristic signal.
- Confidence scoring with three output bands: `likely_ai`, `likely_human`, and `uncertain`.
- `POST /appeal` captures creator reasoning, updates the content status to `under_review`, and logs the appeal.
- `GET /log` returns recent structured audit log entries.
- Flask-Limiter protects `/submit` with documented limits.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
flask --app app run
```

Optional Groq setup:

```text
GROQ_API_KEY=your_real_key_here
```

If `GROQ_API_KEY` is missing or still set to `your_key_here`, the app uses a deterministic offline fallback for the LLM signal so the project still works locally.

## API Examples

Submit content:

```powershell
curl -s -X POST http://localhost:5000/submit `
  -H "Content-Type: application/json" `
  -d '{"text":"Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that stakeholders must consider ethical implications and responsible deployment.","creator_id":"test-user-1"}'
```

Appeal a decision:

```powershell
curl -s -X POST http://localhost:5000/appeal `
  -H "Content-Type: application/json" `
  -d '{"content_id":"PASTE-CONTENT-ID-HERE","creator_reasoning":"I wrote this myself, but my formal style may have looked synthetic."}'
```

View the audit log:

```powershell
curl -s http://localhost:5000/log?limit=5
```

## Architecture

The full architecture diagram and implementation plan are in [planning.md](planning.md). In short:

1. `/submit` validates `text` and `creator_id`.
2. The LLM signal returns an AI-likelihood score.
3. The stylometric signal returns an independent AI-likelihood score based on measurable writing features.
4. The scorer combines both signals into a calibrated confidence band.
5. The label builder returns plain-language reader-facing text.
6. The content record and audit entry are saved as structured JSON.
7. `/appeal` updates the original content status and appends an appeal audit entry.

## Detection Signals

### Signal 1: LLM Attribution Review

The LLM signal asks Groq `llama-3.3-70b-versatile` for a structured AI-likelihood score from `0.0` to `1.0`. It captures broad semantic and stylistic patterns such as generic phrasing, overly balanced paragraphs, and polished but impersonal tone.

For local runs without a Groq key, `offline_fallback` uses phrase and tone markers to approximate that signal. The response records `llm_mode` as either `groq` or `offline_fallback` so the audit log is transparent.

Blind spot: an LLM can still mistake polished human writing for AI writing, and the fallback is intentionally simple.

### Signal 2: Stylometric Heuristics

The stylometric signal computes:

- sentence length variance
- type-token ratio
- punctuation density
- contraction count
- AI-phrase hits
- average sentence length

It captures structural regularity that differs from the LLM's holistic review. Blind spot: poems, formal essays, and non-native English writing can look statistically uniform even when written by a human.

## Confidence Scoring

Both signal scores mean "more likely AI" as they approach `1.0`. The combined score is:

```text
combined_ai_score = (0.60 * llm_score) + (0.40 * stylometric_score)
```

Thresholds:

| Combined AI score | Attribution |
| --- | --- |
| `>= 0.70` | `likely_ai` |
| `<= 0.30` | `likely_human` |
| `0.31` to `0.69` | `uncertain` |

The wide uncertain band is deliberate. A score like `0.51` should not sound confident to a reader; it means the evidence is mixed. A score like `0.95` would produce a high-confidence AI label.

Calibration output from `smoke_test.py`:

| Sample | Attribution | Combined AI score | Confidence |
| --- | --- | ---: | ---: |
| Clearly AI-like paragraph | `likely_ai` | `0.772` | `0.772` |
| Casual human restaurant note | `likely_human` | `0.239` | `0.761` |
| Formal human-style paragraph | `uncertain` | `0.532` | `0.668` |
| Lightly edited AI-style paragraph | `uncertain` | `0.456` | `0.656` |

These examples show that the score is not a binary flip at `0.5`; it changes the reader-facing label.

## Transparency Labels

The `/submit` response returns one of these exact strings:

| Variant | Exact label text |
| --- | --- |
| High-confidence AI | "Provenance Guard found strong signs this text may have been AI-generated. This label is not a final judgment; the creator can appeal if it is wrong." |
| High-confidence human | "Provenance Guard found strong signs this text was written by a person. No AI-generation concerns are currently flagged." |
| Uncertain | "Provenance Guard could not confidently determine whether this text was human-written or AI-generated. Readers should treat the attribution as unresolved." |

## Appeals Workflow

Creators appeal with `content_id` and `creator_reasoning`. The system:

1. Finds the original content record.
2. Updates `status` to `under_review`.
3. Stores appeal reasoning and an appeal timestamp.
4. Adds an `appeal_received` entry to the audit log.

Example appeal response:

```json
{
  "content_id": "example-content-id",
  "status": "under_review",
  "message": "Appeal received. A human reviewer should inspect this decision."
}
```

## Rate Limiting

`POST /submit` is limited to:

```text
10 per minute;100 per day
```

Reasoning: a real creator may submit several drafts quickly, so ten per minute keeps the product usable during normal writing and testing. The daily cap prevents one IP from turning the service into an unlimited batch classifier.

`POST /appeal` is limited to:

```text
20 per hour
```

Appeals should be slower and intentional, so this protects the review queue without blocking normal use.

Rate-limit evidence from a 12-request local test:

```text
[201, 201, 201, 201, 201, 201, 201, 201, 201, 201, 429, 429]
```

## Audit Log

Audit entries are stored in `instance/audit_log.json` and returned by `GET /log`. Each classification entry includes timestamp, content ID, creator ID, attribution, confidence, combined score, both signal scores, stylometric metrics, status, label, and appeal reasoning when present.

Sample structured entries:

```json
[
  {
    "event_type": "classification",
    "status": "classified",
    "attribution": "likely_ai",
    "confidence": 0.772,
    "combined_ai_score": 0.772,
    "llm_score": 0.9,
    "stylometric_score": 0.58,
    "appeal_reasoning": null
  },
  {
    "event_type": "classification",
    "status": "classified",
    "attribution": "likely_human",
    "confidence": 0.761,
    "combined_ai_score": 0.239,
    "llm_score": 0.165,
    "stylometric_score": 0.35,
    "appeal_reasoning": null
  },
  {
    "event_type": "appeal_received",
    "status": "under_review",
    "attribution": "likely_ai",
    "confidence": 0.772,
    "combined_ai_score": 0.772,
    "appeal_reasoning": "I drafted this myself for class, but I used formal language and transitions that may have made the passage look synthetic."
  }
]
```

The real log also includes `content_id`, `creator_id`, timestamp, `llm_mode`, label text, and the full stylometric metrics object.

## Verification

Run the local smoke test:

```powershell
D:\codepath-ai201\.venv\Scripts\python.exe smoke_test.py
```

Observed output:

```text
sample-ai likely_ai 0.772 0.772
sample-human likely_human 0.239 0.761
sample-borderline-formal uncertain 0.532 0.668
sample-borderline-edited uncertain 0.456 0.656
appeal 200 under_review
log_entries 5
```

## Known Limitations

- The offline LLM fallback is a local substitute, not a real model. A deployed version should use Groq or another reviewed model signal consistently.
- Short poems with repetition may be mislabeled because the stylometric signal treats uniform structure and low vocabulary diversity as AI-like.
- Formal human writing may fall into the uncertain or AI-leaning range because polished transitions overlap with common AI prose patterns.
- The JSON file store is simple and inspectable for class grading, but production should use a database with authentication, access controls, and reviewer tooling.

## Spec Reflection

The planning spec helped most with confidence scoring. Defining the uncertain band before coding kept the implementation from becoming a simple `> 0.5` classifier.

One implementation detail diverged from the ideal spec: the LLM signal supports a deterministic offline fallback. I added it because the project should run for graders even when a Groq key is unavailable, and the audit log clearly marks which mode produced the score.

## AI Usage
- I directed the AI tool to turn the project guide into a concrete `planning.md` with architecture, signal definitions, thresholds, label variants, rate limits, and edge cases. I revised the plan to make the false-positive risk explicit and to use a broad uncertain band.
- I used AI assistance to create the smoke test and README evidence. I verified the outputs locally and updated the documentation with actual observed scores and rate-limit status codes.
