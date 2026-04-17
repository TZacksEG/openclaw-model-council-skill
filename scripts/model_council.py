#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
import shutil
from typing import Any
from urllib.error import HTTPError, URLError

DEFAULT_GEMINI_MODEL = "gemini-3.1"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENCLAW_SELF_MODEL = "openai-codex/gpt-5.4"
DEFAULT_OPENCLAW_JUDGE_MODEL = "openai-codex/gpt-5.3-codex"
DEFAULT_OPENCLAW_JUDGE_FALLBACK_MODEL = "openai-codex/gpt-5.1"
RUBRIC_KEYS = ["correctness", "practicality", "clarity", "originality", "brand_fit", "actionability"]
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}
CODEX_BINARY_CANDIDATES = [
    "codex",
    "/Applications/Codex.app/Contents/Resources/codex",
]


def read_text(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def load_gemini_model() -> str:
    p = Path.home() / ".openclaw/workspace/gemini_config.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("model") or DEFAULT_GEMINI_MODEL
        except Exception:
            pass
    return DEFAULT_GEMINI_MODEL


def http_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    attempts = 3
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRYABLE_HTTP_CODES and attempt < attempts:
                time.sleep(attempt)
                last_error = exc
                continue
            raise RuntimeError(f"Request failed with HTTP {exc.code}: {body[:400]}") from exc
        except URLError as exc:
            if attempt < attempts:
                time.sleep(attempt)
                last_error = exc
                continue
            raise RuntimeError(f"Request failed: {exc.reason}") from exc
    raise RuntimeError(f"Request failed after {attempts} attempts: {last_error}")


def call_gemini(prompt: str, model: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4}}
    data = http_json(url, payload, {"Content-Type": "application/json"})
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


def call_claude(prompt: str, model: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    payload = {
        "model": model,
        "max_tokens": 1800,
        "temperature": 0.4,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = http_json(
        "https://api.anthropic.com/v1/messages",
        payload,
        {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text").strip()


def call_provider(prompt: str, provider: str, model: str) -> str:
    if provider == "gemini":
        return call_gemini(prompt, model)
    if provider == "claude":
        return call_claude(prompt, model)
    raise ValueError(f"Unsupported provider: {provider}")


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Review output must decode to a JSON object.")
        return data
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        snippet = text[match.start():]
        try:
            data, _ = decoder.raw_decode(snippet)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError(f"No JSON object found in review output: {text[:300]}")


def normalize_score(value: Any, reviewer_name: str, candidate_name: str, key: str) -> float:
    if isinstance(value, str):
        value = value.strip()
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{reviewer_name} returned a non-numeric score for {candidate_name}.{key}") from exc
    if not 1 <= number <= 10:
        raise ValueError(f"{reviewer_name} returned an out-of-range score for {candidate_name}.{key}: {number}")
    return round(number, 2)


def normalize_review_payload(payload: dict[str, Any], candidates: dict[str, str], reviewer_name: str) -> dict[str, Any]:
    expected_names = [name for name in candidates if name != reviewer_name]
    reviews = payload.get("reviews")
    if not isinstance(reviews, dict):
        raise ValueError(f"{reviewer_name} review payload is missing a reviews object.")

    def normalize_note_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(x).strip() for x in value if str(x).strip()][:4]

    normalized_reviews: dict[str, Any] = {}
    for candidate_name in expected_names:
        item = reviews.get(candidate_name)
        if not isinstance(item, dict):
            raise ValueError(f"{reviewer_name} review payload is missing candidate {candidate_name}.")
        raw_scores = item.get("scores")
        if not isinstance(raw_scores, dict):
            raise ValueError(f"{reviewer_name} review payload is missing scores for {candidate_name}.")
        normalized_scores = {
            key: normalize_score(raw_scores.get(key), reviewer_name, candidate_name, key)
            for key in RUBRIC_KEYS
        }
        normalized_reviews[candidate_name] = {
            "scores": normalized_scores,
            "strengths": normalize_note_list(item.get("strengths", [])),
            "weaknesses": normalize_note_list(item.get("weaknesses", [])),
        }

    winner = payload.get("winner")
    if winner not in expected_names:
        winner = max(
            normalized_reviews.items(),
            key=lambda kv: total_score(kv[1]["scores"]),
        )[0]

    winner_reason = str(payload.get("winner_reason", "")).strip()
    if not winner_reason:
        winner_reason = "Winner inferred from the normalized rubric scores."

    return {
        "reviews": normalized_reviews,
        "winner": winner,
        "winner_reason": winner_reason,
    }


def answer_prompt(user_prompt: str) -> str:
    return (
        "Answer the user's prompt directly. Be decisive, useful, and specific. "
        "Do not mention that this is part of a model comparison.\n\n"
        f"USER PROMPT:\n{user_prompt}"
    )


def review_prompt(user_prompt: str, candidates: dict[str, str], reviewer_name: str) -> str:
    blob = []
    for name, answer in candidates.items():
        if name == reviewer_name:
            continue
        blob.append(f"## {name}\n{answer}\n")
    rubric = ", ".join(RUBRIC_KEYS)
    return f"""
You are {reviewer_name}. Review the candidate answers to the user's prompt.
Score each candidate from 1-10 on: {rubric}.
Be strict, concise, and evidence-based. Penalize fluff, generic advice, weak brand fit, and low actionability.
Do not give special treatment to any model name.
Do not review yourself.
Return valid JSON only in this exact shape:
{{
  "reviews": {{
    "candidate_name": {{
      "scores": {{"correctness": 0, "practicality": 0, "clarity": 0, "originality": 0, "brand_fit": 0, "actionability": 0}},
      "strengths": ["...", "..."],
      "weaknesses": ["...", "..."]
    }}
  }},
  "winner": "candidate_name",
  "winner_reason": "short reason"
}}

USER PROMPT:
{user_prompt}

CANDIDATE ANSWERS:
{''.join(blob)}
""".strip()


def judge_prompt(user_prompt: str, candidates: dict[str, str], judge_name: str) -> str:
    blob = []
    for name, answer in candidates.items():
        blob.append(f"## {name}\n{answer}\n")
    rubric = ", ".join(RUBRIC_KEYS)
    return f"""
You are {judge_name}, an isolated council judge.
Your only job is evaluation. You did not author any candidate answer.
Review every candidate answer to the user's prompt.
Score each candidate from 1-10 on: {rubric}.
Be strict, concise, and evidence-based. Penalize fluff, generic advice, weak brand fit, and low actionability.
Do not give special treatment to any model name.
Return valid JSON only in this exact shape:
{{
  "reviews": {{
    "candidate_name": {{
      "scores": {{"correctness": 0, "practicality": 0, "clarity": 0, "originality": 0, "brand_fit": 0, "actionability": 0}},
      "strengths": ["...", "..."],
      "weaknesses": ["...", "..."]
    }}
  }},
  "winner": "candidate_name",
  "winner_reason": "short reason"
}}

USER PROMPT:
{user_prompt}

CANDIDATE ANSWERS:
{''.join(blob)}
""".strip()


def review_candidates(
    user_prompt: str,
    candidates: dict[str, str],
    reviewer_name: str,
    provider: str,
    model: str,
) -> dict[str, Any]:
    prompt = review_prompt(user_prompt, candidates, reviewer_name)
    feedback = []
    for attempt in range(2):
        attempt_prompt = prompt
        if feedback:
            attempt_prompt = (
                f"{prompt}\n\n"
                "Your previous response was invalid because it did not cleanly match the required JSON schema.\n"
                f"Issue: {feedback[-1]}\n"
                "Return valid JSON only and include every candidate exactly once."
            )
        raw = call_provider(attempt_prompt, provider, model)
        try:
            return normalize_review_payload(extract_json(raw), candidates, reviewer_name)
        except Exception as exc:
            feedback.append(str(exc))
    issues = "; ".join(feedback)
    raise RuntimeError(f"{reviewer_name} review failed validation after 2 attempts: {issues}")


def judge_candidates(
    user_prompt: str,
    candidates: dict[str, str],
    judge_name: str,
    primary_model: str,
    fallback_model: str | None,
) -> tuple[dict[str, Any], str]:
    prompt = judge_prompt(user_prompt, candidates, judge_name)
    feedback = []
    last_model = primary_model
    for attempt in range(2):
        attempt_prompt = prompt
        if feedback:
            attempt_prompt = (
                f"{prompt}\n\n"
                "Your previous response was invalid because it did not cleanly match the required JSON schema.\n"
                f"Issue: {feedback[-1]}\n"
                "Return valid JSON only and score every candidate exactly once."
            )
        raw, last_model = call_codex_judge(attempt_prompt, primary_model, fallback_model)
        try:
            return normalize_review_payload(extract_json(raw), candidates, judge_name), last_model
        except Exception as exc:
            feedback.append(str(exc))
    issues = "; ".join(feedback)
    raise RuntimeError(f"{judge_name} review failed validation after 2 attempts: {issues}")


def total_score(scores: dict[str, float]) -> float:
    return round(sum(scores[k] for k in RUBRIC_KEYS) / len(RUBRIC_KEYS), 2)


def average_dict(dicts: list[dict[str, float]]) -> dict[str, float]:
    return {k: round(statistics.mean(d[k] for d in dicts), 2) for k in RUBRIC_KEYS}


def normalize_openclaw_model_name(model: str | None) -> str | None:
    if not model:
        return model
    if "/" in model:
        _, _, suffix = model.partition("/")
        return suffix or model
    return model


def resolve_codex_binary() -> str:
    for candidate in CODEX_BINARY_CANDIDATES:
        path = shutil.which(candidate) if "/" not in candidate else candidate
        if path and Path(path).exists():
            return path
    raise RuntimeError("Could not find the Codex runtime binary needed for native judge execution.")


def codex_model_attempts(model: str | None) -> list[str]:
    if not model:
        return []
    attempts = [model]
    normalized = normalize_openclaw_model_name(model)
    if normalized and normalized not in attempts:
        attempts.append(normalized)
    return attempts


def call_codex_exec(prompt: str, model: str) -> str:
    codex_bin = resolve_codex_binary()
    with tempfile.TemporaryDirectory(prefix="model-council-judge-") as td:
        td_path = Path(td)
        output_path = td_path / "judge_output.json"
        cmd = [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-m",
            model,
            "-o",
            str(output_path),
            prompt,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Codex judge timed out for model {model}.") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Codex judge exec failed for model {model}: {detail[-1000:]}")
        if not output_path.exists():
            raise RuntimeError(f"Codex judge exec finished without writing output for model {model}.")
        return output_path.read_text(encoding="utf-8").strip()


def call_codex_judge(prompt: str, primary_model: str, fallback_model: str | None) -> tuple[str, str]:
    seen: set[str] = set()
    errors = []
    for role, model in [("primary", primary_model), ("fallback", fallback_model)]:
        for attempt_model in codex_model_attempts(model):
            if attempt_model in seen:
                continue
            seen.add(attempt_model)
            try:
                return call_codex_exec(prompt, attempt_model), attempt_model
            except Exception as exc:
                errors.append(f"{role} judge {attempt_model} failed: {exc}")
    raise RuntimeError("; ".join(errors))


def main() -> None:
    ap = argparse.ArgumentParser(description="OpenClaw Model Council Skill: Claude vs Gemini vs optional self")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prompt")
    g.add_argument("--prompt-file")
    ap.add_argument("--self-answer-file")
    ap.add_argument("--include-self", action="store_true", help="Require a self answer candidate")
    ap.add_argument("--exclude-self", action="store_true", help="Exclude self even if a self answer file exists")
    ap.add_argument("--gemini-model", default=load_gemini_model())
    ap.add_argument("--claude-model", default=DEFAULT_CLAUDE_MODEL)
    ap.add_argument("--judge-model", default=DEFAULT_OPENCLAW_JUDGE_MODEL)
    ap.add_argument("--judge-fallback-model", default=DEFAULT_OPENCLAW_JUDGE_FALLBACK_MODEL)
    ap.add_argument("--judge-provider", help=argparse.SUPPRESS)
    args = ap.parse_args()

    user_prompt = args.prompt or read_text(args.prompt_file)
    if not user_prompt:
        raise SystemExit("Prompt is empty")

    candidates: dict[str, str] = {}
    candidates["gemini"] = call_gemini(answer_prompt(user_prompt), args.gemini_model)
    candidates["claude"] = call_claude(answer_prompt(user_prompt), args.claude_model)
    if args.self_answer_file and not args.exclude_self:
        candidates["self"] = read_text(args.self_answer_file)
    elif args.include_self:
        raise SystemExit("--include-self requires --self-answer-file in the standalone script. The wrapper should generate your answer first and pass it in.")

    judge_name = "codex_judge"

    peer_reviews = {
        "gemini": review_candidates(user_prompt, candidates, "gemini", "gemini", args.gemini_model),
        "claude": review_candidates(user_prompt, candidates, "claude", "claude", args.claude_model),
    }
    judge_review, resolved_judge_model = judge_candidates(
        user_prompt,
        candidates,
        judge_name,
        args.judge_model,
        args.judge_fallback_model,
    )

    final_scores: dict[str, Any] = {}
    for candidate_name in candidates:
        peer_score_dicts = []
        peer_sources: dict[str, Any] = {}
        strengths, weaknesses = [], []
        for reviewer_name, review in peer_reviews.items():
            item = review["reviews"].get(candidate_name)
            if not item:
                continue
            peer_score_dicts.append(item["scores"])
            peer_sources[reviewer_name] = item
            strengths.extend(item.get("strengths", []))
            weaknesses.extend(item.get("weaknesses", []))
        if not peer_score_dicts:
            raise RuntimeError(f"No peer reviews were available for candidate {candidate_name}.")
        peer_avg = average_dict(peer_score_dicts)
        judge_item = judge_review["reviews"].get(candidate_name)
        if not judge_item:
            raise RuntimeError(f"Judge review is missing candidate {candidate_name}.")
        judge_scores = judge_item["scores"]
        final_by_category = average_dict([peer_avg, judge_scores])
        strengths.extend(judge_item.get("strengths", []))
        weaknesses.extend(judge_item.get("weaknesses", []))

        final_scores[candidate_name] = {
            "peer_sources": peer_sources,
            "peer_average": peer_avg,
            "judge_review": judge_item,
            "final_by_category": final_by_category,
            "peer_total": total_score(peer_avg),
            "judge_total": total_score(judge_scores),
            "final_total": total_score(final_by_category),
            "peer_review_count": len(peer_score_dicts),
            "strengths": strengths[:8],
            "weaknesses": weaknesses[:8],
            "formula": {
                "peer_score": f"Average of peer reviews received ({', '.join(peer_sources.keys())})",
                "judge_score": f"{judge_name} isolated judge review",
                "final_score": "Average of peer score and judge score, category by category",
            },
        }

    winner = max(final_scores.items(), key=lambda kv: kv[1]["final_total"])[0]
    peer_winner = max(final_scores.items(), key=lambda kv: kv[1]["peer_total"])[0]
    judge_winner = max(final_scores.items(), key=lambda kv: kv[1]["judge_total"])[0]
    out = {
        "prompt": user_prompt,
        "models": {"gemini": args.gemini_model, "claude": args.claude_model, judge_name: resolved_judge_model},
        "self_included": "self" in candidates,
        "judge_config": {
            "family": "OpenClaw-native Codex runtime",
            "runtime_environment": "OpenClaw",
            "self_model_label": DEFAULT_OPENCLAW_SELF_MODEL,
            "requested_model": args.judge_model,
            "fallback_model": args.judge_fallback_model,
            "resolved_model": resolved_judge_model,
            "runtime_model_attempts": {
                "primary": normalize_openclaw_model_name(args.judge_model),
                "fallback": normalize_openclaw_model_name(args.judge_fallback_model),
            },
            "execution_path": "codex exec",
        },
        "rating_map": {
            "peer_layer": {
                "gemini": ["claude", "self"] if "self" in candidates else ["claude"],
                "claude": ["gemini", "self"] if "self" in candidates else ["gemini"],
            },
            "judge_layer": {judge_name: list(candidates.keys())},
        },
        "score_formula": {
            "peer_score": {
                "claude": "Gemini's review of Claude",
                "gemini": "Claude's review of Gemini",
                "self": "Average of Claude's and Gemini's reviews of self" if "self" in candidates else None,
            },
            "judge_score": f"{judge_name} isolated evaluation of every candidate",
            "final_score": "Average of peer score and judge score, category by category",
        },
        "candidates": candidates,
        "peer_reviews": peer_reviews,
        "judge_review": judge_review,
        "final_scores": final_scores,
        "winner_breakdown": {
            "peer_winner": peer_winner,
            "judge_winner": judge_winner,
            "final_winner": winner,
        },
        "winner": winner,
        "synthesis_notes": [
            "Use the winner as default unless another candidate has a uniquely valuable angle.",
            "Consider merging strongest phrasing or insight from losing answers if it materially improves the final output.",
        ],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
