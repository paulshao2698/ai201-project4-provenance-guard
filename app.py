import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

try:
    from groq import Groq
except ImportError:  # pragma: no cover - dependency is optional for offline local mode
    Groq = None


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
SUBMISSIONS_PATH = INSTANCE_DIR / "submissions.json"
AUDIT_LOG_PATH = INSTANCE_DIR / "audit_log.json"

LABELS = {
    "likely_ai": (
        "Provenance Guard found strong signs this text may have been AI-generated. "
        "This label is not a final judgment; the creator can appeal if it is wrong."
    ),
    "likely_human": (
        "Provenance Guard found strong signs this text was written by a person. "
        "No AI-generation concerns are currently flagged."
    ),
    "uncertain": (
        "Provenance Guard could not confidently determine whether this text was "
        "human-written or AI-generated. Readers should treat the attribution as unresolved."
    ),
}

AI_PHRASES = {
    "it is important to note",
    "transformative",
    "paradigm shift",
    "furthermore",
    "moreover",
    "stakeholders",
    "responsible deployment",
    "ethical implications",
    "in conclusion",
    "multifaceted",
    "underscores",
}

HUMAN_MARKERS = {
    " i ",
    " me ",
    " my ",
    " honestly",
    " ok ",
    "okay",
    "lol",
    "gonna",
    "kinda",
    "won't",
    "don't",
    "can't",
    "?",
    "!",
}


def create_app():
    app = Flask(__name__)
    INSTANCE_DIR.mkdir(exist_ok=True)

    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri="memory://",
    )

    @app.get("/")
    def index():
        return jsonify(
            {
                "service": "provenance-guard",
                "status": "ok",
                "message": "Use POST /submit to classify text, POST /appeal to contest a decision, or GET /log to inspect audit entries.",
                "endpoints": {
                    "health": "GET /health",
                    "submit": "POST /submit",
                    "appeal": "POST /appeal",
                    "log": "GET /log?limit=20",
                },
            }
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "provenance-guard"})

    @app.post("/submit")
    @limiter.limit("10 per minute;100 per day")
    def submit():
        payload = request.get_json(silent=True) or {}
        text = (payload.get("text") or "").strip()
        creator_id = (payload.get("creator_id") or "").strip()

        if not text:
            return jsonify({"error": "text is required"}), 400
        if not creator_id:
            return jsonify({"error": "creator_id is required"}), 400

        content_id = str(uuid.uuid4())
        llm_signal = llm_attribution_signal(text)
        stylometric_signal = stylometric_signal_score(text)
        decision = score_decision(llm_signal["score"], stylometric_signal["score"])
        label = build_label(decision["attribution"])

        submission = {
            "content_id": content_id,
            "creator_id": creator_id,
            "text_preview": text[:160],
            "timestamp": utc_now(),
            "status": "classified",
            "attribution": decision["attribution"],
            "confidence": decision["confidence"],
            "combined_ai_score": decision["combined_ai_score"],
            "label": label,
            "signals": {
                "llm": llm_signal,
                "stylometric": stylometric_signal,
            },
            "appeal": None,
        }

        submissions = read_json(SUBMISSIONS_PATH, {})
        submissions[content_id] = submission
        write_json(SUBMISSIONS_PATH, submissions)
        append_audit_entry("classification", submission)

        return jsonify(public_submission_response(submission)), 201

    @app.post("/appeal")
    @limiter.limit("20 per hour")
    def appeal():
        payload = request.get_json(silent=True) or {}
        content_id = (payload.get("content_id") or "").strip()
        creator_reasoning = (payload.get("creator_reasoning") or "").strip()

        if not content_id:
            return jsonify({"error": "content_id is required"}), 400
        if not creator_reasoning:
            return jsonify({"error": "creator_reasoning is required"}), 400

        submissions = read_json(SUBMISSIONS_PATH, {})
        submission = submissions.get(content_id)
        if not submission:
            return jsonify({"error": "content_id not found"}), 404

        appeal_record = {
            "appeal_id": str(uuid.uuid4()),
            "timestamp": utc_now(),
            "creator_reasoning": creator_reasoning,
        }
        submission["status"] = "under_review"
        submission["appeal"] = appeal_record
        submissions[content_id] = submission
        write_json(SUBMISSIONS_PATH, submissions)
        append_audit_entry("appeal_received", submission)

        return jsonify(
            {
                "content_id": content_id,
                "status": "under_review",
                "message": "Appeal received. A human reviewer should inspect this decision.",
                "appeal": appeal_record,
            }
        )

    @app.get("/log")
    def log():
        limit = request.args.get("limit", default=20, type=int)
        limit = max(1, min(limit, 100))
        entries = read_json(AUDIT_LOG_PATH, [])
        return jsonify({"entries": entries[-limit:]})

    return app


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data):
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)


def append_audit_entry(event_type, submission):
    entries = read_json(AUDIT_LOG_PATH, [])
    signals = submission["signals"]
    entry = {
        "event_type": event_type,
        "timestamp": utc_now(),
        "content_id": submission["content_id"],
        "creator_id": submission["creator_id"],
        "status": submission["status"],
        "attribution": submission["attribution"],
        "confidence": submission["confidence"],
        "combined_ai_score": submission["combined_ai_score"],
        "llm_score": signals["llm"]["score"],
        "llm_mode": signals["llm"]["mode"],
        "stylometric_score": signals["stylometric"]["score"],
        "stylometric_metrics": signals["stylometric"]["metrics"],
        "label": submission["label"],
        "appeal_reasoning": (
            submission["appeal"]["creator_reasoning"] if submission.get("appeal") else None
        ),
    }
    entries.append(entry)
    write_json(AUDIT_LOG_PATH, entries)


def llm_attribution_signal(text):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if api_key and api_key != "your_key_here" and Groq:
        try:
            return groq_signal(text, api_key)
        except Exception as exc:  # Keep the app available when the API is unreachable.
            fallback = offline_llm_fallback(text)
            fallback["rationale"] += f" Groq unavailable, used fallback: {exc.__class__.__name__}."
            return fallback
    return offline_llm_fallback(text)


def groq_signal(text, api_key):
    client = Groq(api_key=api_key)
    prompt = (
        "You are one signal in a provenance system. Return compact JSON only with "
        "ai_score from 0.0 to 1.0 and rationale under 25 words. Higher ai_score "
        "means the text is more likely AI-generated.\n\nTEXT:\n"
        f"{text[:3500]}"
    )
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = completion.choices[0].message.content
    parsed = json.loads(content)
    score = clamp(float(parsed.get("ai_score", 0.5)))
    return {
        "name": "llm_attribution",
        "mode": "groq",
        "score": score,
        "rationale": parsed.get("rationale", "Groq returned a structured attribution score."),
    }


def offline_llm_fallback(text):
    lower = f" {text.lower()} "
    phrase_hits = sum(1 for phrase in AI_PHRASES if phrase in lower)
    human_hits = sum(1 for marker in HUMAN_MARKERS if marker in lower)
    sentences = split_sentences(text)
    avg_sentence_len = average([len(tokenize(sentence)) for sentence in sentences])

    score = 0.48
    score += min(0.42, phrase_hits * 0.07)
    score -= min(0.36, human_hits * 0.045)
    if avg_sentence_len >= 20:
        score += 0.10
    elif avg_sentence_len <= 8:
        score -= 0.08

    return {
        "name": "llm_attribution",
        "mode": "offline_fallback",
        "score": round(clamp(score), 3),
        "rationale": "Keyword and tone fallback used because no live Groq key is configured.",
    }


def stylometric_signal_score(text):
    words = tokenize(text)
    sentences = split_sentences(text)
    word_count = len(words)
    sentence_lengths = [len(tokenize(sentence)) for sentence in sentences] or [word_count]
    avg_sentence_len = average(sentence_lengths)
    sentence_variance = variance(sentence_lengths)
    type_token_ratio = len(set(words)) / word_count if word_count else 0
    punctuation_density = len(re.findall(r"[!?;:]", text)) / max(1, word_count)
    contraction_count = len(re.findall(r"\b\w+'(?:t|re|ve|ll|d|m|s)\b", text.lower()))
    ai_phrase_hits = sum(1 for phrase in AI_PHRASES if phrase in text.lower())

    score = 0.50
    if word_count < 35:
        score += 0.06
    if sentence_variance < 12:
        score += 0.14
    elif sentence_variance > 80:
        score -= 0.13
    if type_token_ratio < 0.58:
        score += 0.12
    elif type_token_ratio > 0.75:
        score -= 0.11
    if 12 <= avg_sentence_len <= 24:
        score += 0.07
    elif avg_sentence_len < 9:
        score -= 0.07
    if punctuation_density > 0.035:
        score -= 0.09
    if contraction_count:
        score -= min(0.14, contraction_count * 0.04)
    score += min(0.12, ai_phrase_hits * 0.035)

    return {
        "name": "stylometric_heuristics",
        "score": round(clamp(score), 3),
        "metrics": {
            "word_count": word_count,
            "sentence_count": len(sentences),
            "average_sentence_length": round(avg_sentence_len, 2),
            "sentence_length_variance": round(sentence_variance, 2),
            "type_token_ratio": round(type_token_ratio, 3),
            "punctuation_density": round(punctuation_density, 3),
            "contraction_count": contraction_count,
            "ai_phrase_hits": ai_phrase_hits,
        },
    }


def score_decision(llm_score, stylometric_score):
    combined = round(clamp((0.60 * llm_score) + (0.40 * stylometric_score)), 3)
    if combined >= 0.70:
        attribution = "likely_ai"
        confidence = combined
    elif combined <= 0.30:
        attribution = "likely_human"
        confidence = round(1 - combined, 3)
    else:
        attribution = "uncertain"
        distance_to_edge = min(abs(combined - 0.30), abs(combined - 0.70))
        confidence = round(0.50 + distance_to_edge, 3)

    return {
        "combined_ai_score": combined,
        "attribution": attribution,
        "confidence": confidence,
    }


def build_label(attribution):
    return LABELS.get(attribution, LABELS["uncertain"])


def public_submission_response(submission):
    return {
        "content_id": submission["content_id"],
        "creator_id": submission["creator_id"],
        "status": submission["status"],
        "attribution": submission["attribution"],
        "confidence": submission["confidence"],
        "combined_ai_score": submission["combined_ai_score"],
        "label": submission["label"],
        "signals": submission["signals"],
    }


def split_sentences(text):
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    return sentences or [text.strip()]


def tokenize(text):
    return re.findall(r"[a-zA-Z']+", text.lower())


def average(values):
    return sum(values) / len(values) if values else 0


def variance(values):
    if len(values) <= 1:
        return 0
    mean = average(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def clamp(value, minimum=0.0, maximum=1.0):
    if math.isnan(value):
        return 0.5
    return max(minimum, min(maximum, value))


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
