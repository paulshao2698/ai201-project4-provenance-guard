# Provenance Guard Planning

## Product Goal
Provenance Guard is a backend service for creative platforms that need to add context to submitted writing without pretending AI detection is perfect. The system analyzes a text submission, combines multiple independent signals, returns a calibrated attribution result, stores a structured audit trail, and gives creators an appeal path when they believe the label is wrong.

## Detection Signals

### Signal 1: LLM Attribution Review
- **What it measures:** A holistic model judgment of whether the passage reads like AI-generated or human-written text.
- **Why this helps:** An LLM can notice broad semantic patterns, generic phrasing, overly balanced structure, and tone consistency that simple statistics may miss.
- **Output:** `llm_score`, a float from `0.0` to `1.0`, where higher means more likely AI-generated.
- **Blind spots:** Model judgments can be overconfident, can mistake polished human writing for AI writing, and require a network/API key in production. The project includes a deterministic offline fallback so the app remains runnable.

### Signal 2: Stylometric Heuristics
- **What it measures:** Sentence length variance, vocabulary diversity, punctuation density, first-person markers, contractions, and cliche/transitional phrasing.
- **Why this helps:** AI writing often has smoother sentence lengths, fewer idiosyncratic disruptions, and more generic connective phrases. Human writing often contains uneven rhythm, informal markers, and more surprising word choice.
- **Output:** `stylometric_score`, a float from `0.0` to `1.0`, where higher means more likely AI-generated.
- **Blind spots:** Poems, formal essays, non-native English writing, and heavily edited prose can look statistically uniform even when written by a person.

### Combination
The combined AI-likelihood score is:

```text
combined_ai_score = (0.60 * llm_score) + (0.40 * stylometric_score)
```

The LLM signal gets slightly more weight because it can evaluate meaning and style together. The heuristic signal still contributes heavily because it is transparent, cheap, and independent.

## Uncertainty Representation
The system treats `0.50` as unresolved evidence, not as a decision boundary. Scores near the middle are intentionally labeled uncertain.

| Combined AI score | Attribution | Confidence shown |
| --- | --- | --- |
| `>= 0.70` | `likely_ai` | The score itself |
| `<= 0.30` | `likely_human` | `1 - score` |
| `0.31` to `0.69` | `uncertain` | Closeness to the nearest edge of the uncertain band |

This makes a `0.51` meaningfully different from a `0.95`: `0.51` receives an uncertain label, while `0.95` receives a high-confidence AI label. The broad uncertain band reflects the project's false-positive risk: accusing a human writer of using AI is worse than missing some AI-generated work.

## Transparency Label Design
These exact strings are implemented by `build_label()` and returned from `/submit`:

| Variant | Exact label text |
| --- | --- |
| High-confidence AI | "Provenance Guard found strong signs this text may have been AI-generated. This label is not a final judgment; the creator can appeal if it is wrong." |
| High-confidence human | "Provenance Guard found strong signs this text was written by a person. No AI-generation concerns are currently flagged." |
| Uncertain | "Provenance Guard could not confidently determine whether this text was human-written or AI-generated. Readers should treat the attribution as unresolved." |

## Appeals Workflow
Any creator who has a `content_id` can submit an appeal with `creator_reasoning`. When an appeal is received, the system:

1. Looks up the original submission.
2. Updates the content status from `classified` to `under_review`.
3. Stores the appeal reasoning and timestamp with the content record.
4. Appends an `appeal_received` entry to the audit log alongside the original decision context.

A human reviewer opening an appeal queue would see the content ID, creator ID, original label, confidence, both signal scores, appeal reasoning, and timestamps.

## Anticipated Edge Cases
- A sparse poem with repeated words and short lines may have low vocabulary diversity and uniform sentence structure, pushing the stylometric score toward AI even if the poem is human-written.
- A formal essay by a careful human writer may use balanced transitions and restrained punctuation, making both signals overestimate AI likelihood.
- A lightly edited AI draft with personal details and contractions may land in the uncertain band, because the human edits reduce both signal scores.
- Very short submissions do not provide enough evidence for stable statistics, so the system lowers signal confidence and tends to return uncertain.

## Architecture

```text
Submission flow
POST /submit
  | JSON: text, creator_id
  v
Input validation
  | raw text
  v
LLM attribution signal --------\
  | llm_score, rationale        \
  v                             v
Stylometric signal --------> Confidence scorer
  | metrics, score              | combined score, attribution
  v                             v
Transparency label builder --> Audit log + content store
                                | content_id, scores, label, status
                                v
                              JSON response

Appeal flow
POST /appeal
  | JSON: content_id, creator_reasoning
  v
Submission lookup
  | original decision context
  v
Status update: under_review
  | appeal reasoning + timestamp
  v
Audit log
  |
  v
JSON confirmation
```

Submissions move from validation to two independent detection signals, then into a weighted scoring function and label builder before being stored and logged. Appeals reuse the stored `content_id`, update the original record to `under_review`, and append a structured audit entry that preserves the original classification context.

## API Surface

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health` | GET | Confirm the service is running. |
| `/submit` | POST | Analyze text and return `content_id`, attribution, confidence, signal scores, and label. |
| `/appeal` | POST | Mark a classified submission as `under_review` and log creator reasoning. |
| `/log` | GET | Return recent structured audit entries for grading and documentation. |

## Rate Limit Plan
`/submit` uses `10 per minute;100 per day` per client IP. Ten per minute supports rapid drafting or testing, while the daily cap prevents a single client from flooding the detector as a free batch classifier. `/appeal` is limited to `20 per hour` because appeals should be slower, intentional actions.

## AI Tool Plan

### M3: Submission Endpoint + First Signal
- **Spec sections to provide:** Detection Signals, Architecture, API Surface.
- **Ask:** Generate a Flask skeleton, validation for `/submit`, and the LLM signal wrapper with an offline fallback.
- **Verify:** Call the signal function directly and POST sample text to `/submit`; confirm `content_id`, attribution, placeholder confidence, and audit entries exist.

### M4: Second Signal + Confidence Scoring
- **Spec sections to provide:** Detection Signals, Uncertainty Representation, Architecture.
- **Ask:** Generate stylometric metrics and weighted scoring logic.
- **Verify:** Run four calibration inputs and confirm clearly AI-like text scores higher than casual human text, with borderline cases in the uncertain range.

### M5: Production Layer
- **Spec sections to provide:** Transparency Label Design, Appeals Workflow, Rate Limit Plan, Architecture.
- **Ask:** Generate label mapping, `/appeal`, `/log`, and rate limiting.
- **Verify:** Reach all three label variants, submit an appeal, confirm status changes to `under_review`, and trigger rate-limit `429` responses.
