#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENGINE = BASE_DIR / 'model_council.py'
DEFAULT_LEARNING_LOG = Path.home() / '.openclaw/workspace/memory/learnings/model-council.md'
DEFAULT_RUNS_DIR = Path.home() / '.openclaw/workspace/memory/learnings/model-council-runs'
RUBRIC_KEYS = ["correctness", "practicality", "clarity", "originality", "brand_fit", "actionability"]
MODES = {
    'balanced': {
        'category_weights': {key: 1.0 for key in RUBRIC_KEYS},
    },
    'brand': {
        'category_weights': {
            'correctness': 1.0,
            'practicality': 1.0,
            'clarity': 1.15,
            'originality': 1.15,
            'brand_fit': 1.3,
            'actionability': 1.0,
        },
    },
    'strategy': {
        'category_weights': {
            'correctness': 1.2,
            'practicality': 1.15,
            'clarity': 1.0,
            'originality': 1.0,
            'brand_fit': 0.9,
            'actionability': 1.05,
        },
    },
    'execution': {
        'category_weights': {
            'correctness': 1.0,
            'practicality': 1.2,
            'clarity': 1.0,
            'originality': 0.9,
            'brand_fit': 0.85,
            'actionability': 1.3,
        },
    },
}
SENSITIVE_PATTERNS = [
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    re.compile(r'\b(api[_-]?key|access[_-]?token|secret|password)\b', re.IGNORECASE),
    re.compile(r'\bsk-[A-Za-z0-9]{20,}\b'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bBearer\s+[A-Za-z0-9._-]{20,}\b', re.IGNORECASE),
]


def weighted_average(scores: dict, weights: dict[str, float]) -> float:
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError('Category weights must sum to a positive number.')
    weighted_sum = sum(scores[key] * weights[key] for key in RUBRIC_KEYS)
    return round(weighted_sum / total_weight, 2)


def apply_mode_weights(data: dict, mode: str) -> dict:
    category_weights = MODES[mode]['category_weights']
    for name, score in data['final_scores'].items():
        score['mode'] = mode
        score['category_weights'] = category_weights
        score['mode_peer_total'] = weighted_average(score['peer_average'], category_weights)
        score['mode_judge_total'] = weighted_average(score['judge_review']['scores'], category_weights)
        score['mode_final_total'] = weighted_average(score['final_by_category'], category_weights)
    ranked = sorted(data['final_scores'].items(), key=lambda kv: kv[1]['mode_final_total'], reverse=True)
    peer_ranked = sorted(data['final_scores'].items(), key=lambda kv: kv[1]['mode_peer_total'], reverse=True)
    judge_ranked = sorted(data['final_scores'].items(), key=lambda kv: kv[1]['mode_judge_total'], reverse=True)
    data['winner'] = ranked[0][0]
    data['runner_up'] = ranked[1][0] if len(ranked) > 1 else None
    data['winner_breakdown'] = {
        'peer_winner': peer_ranked[0][0],
        'judge_winner': judge_ranked[0][0],
        'final_winner': ranked[0][0],
    }
    data['mode'] = mode
    return data


def summarize_answer(text: str, limit: int = 220) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + '...'


def summarize_candidate(name: str, data: dict, score: dict) -> str:
    answer = summarize_answer(data['candidates'][name], 320)
    strengths = score.get('strengths', [])[:2]
    weaknesses = score.get('weaknesses', [])[:2]
    parts = [f"{name.capitalize()} argues: {answer}."]
    if strengths:
        parts.append("Its strongest qualities are " + '; '.join(strengths) + '.')
    if weaknesses:
        parts.append("Its main weaknesses are " + '; '.join(weaknesses) + '.')
    return ' '.join(parts)


def sanitize_slug(text: str, limit: int = 80) -> str:
    chars = []
    for ch in text.lower():
        if ch.isalnum():
            chars.append(ch)
        elif ch in {' ', '-', '_'}:
            chars.append('-')
    slug = ''.join(chars)
    while '--' in slug:
        slug = slug.replace('--', '-')
    slug = slug.strip('-')
    return slug[:limit] or 'council-run'


def detect_pattern(strengths: list[str], weaknesses: list[str]) -> tuple[str, str, str]:
    joined_s = ' '.join(strengths).lower()
    joined_w = ' '.join(weaknesses).lower()

    if 'specific' in joined_s or 'concrete' in joined_s or 'split' in joined_s or 'number' in joined_s:
        winner_pattern = 'Concrete, specific recommendations beat abstract advice.'
    elif 'brand' in joined_s or 'premium' in joined_s:
        winner_pattern = 'Stronger brand-fit and sharper positioning language win.'
    elif 'practical' in joined_s or 'action' in joined_s:
        winner_pattern = 'Actionable structure wins over theory-only answers.'
    else:
        winner_pattern = 'Clear, well-supported reasoning wins.'

    if 'generic' in joined_w or 'vague' in joined_w:
        losing_pattern = 'Generic phrasing loses to specific context-aware reasoning.'
    elif 'less detailed' in joined_w or 'brief' in joined_w or 'thin' in joined_w:
        losing_pattern = 'Thin reasoning loses to fuller justification.'
    elif 'brand' in joined_w:
        losing_pattern = 'Weak brand fit loses in brand-sensitive questions.'
    else:
        losing_pattern = 'Weaker justification and lower context fit lose.'

    rule = f"Apply next time: {winner_pattern} Avoid this failure mode: {losing_pattern}"
    return winner_pattern, losing_pattern, rule


def text_looks_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def data_looks_sensitive(data: dict) -> bool:
    if text_looks_sensitive(data.get('prompt', '')):
        return True
    return any(text_looks_sensitive(answer) for answer in data.get('candidates', {}).values())


def redact_text(text: str, label: str) -> str:
    words = len(text.split())
    return f"[redacted {label}; {words} words]"


def prepare_storage_data(data: dict, storage_policy: str) -> dict:
    stored = copy.deepcopy(data)
    redact = storage_policy == 'redact-all' or (
        storage_policy == 'redact-sensitive' and data_looks_sensitive(data)
    )
    stored['storage_policy'] = storage_policy
    stored['storage_redacted'] = redact
    if not redact:
        return stored

    stored['prompt'] = redact_text(stored.get('prompt', ''), 'prompt')
    stored['candidates'] = {
        name: redact_text(answer, f'{name} answer')
        for name, answer in stored.get('candidates', {}).items()
    }
    return stored


def append_learning(data: dict, learning_log: Path) -> None:
    learning_log.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec='seconds')
    winner = data['winner']
    runner = data.get('runner_up')
    win = data['final_scores'][winner]
    winner_pattern, losing_pattern, rule = detect_pattern(win.get('strengths', []), win.get('weaknesses', []))

    lines = []
    if not learning_log.exists():
        lines.append('# OpenClaw Model Council Skill Learnings\n\n')
    lines.append(f"## {ts} | mode={data.get('mode', 'balanced')} | winner={winner}\n")
    lines.append(f"**Prompt:** {summarize_answer(data['prompt'], 300)}\n\n")
    lines.append('**Winner breakdown:**\n')
    lines.append(f"- peer_winner: {data.get('winner_breakdown', {}).get('peer_winner')}\n")
    lines.append(f"- judge_winner: {data.get('winner_breakdown', {}).get('judge_winner')}\n")
    lines.append(f"- final_winner: {data.get('winner_breakdown', {}).get('final_winner')}\n\n")
    lines.append('**Why winner won:**\n')
    for item in win.get('strengths', [])[:3]:
        lines.append(f"- {item}\n")
    if runner:
        lines.append('\n**Why others lost:**\n')
        for name, score in sorted(data['final_scores'].items(), key=lambda kv: kv[1]['mode_final_total'], reverse=True):
            if name == winner:
                continue
            for item in score.get('weaknesses', [])[:2]:
                lines.append(f"- {name}: {item}\n")
    lines.append('\n**Winner pattern:**\n')
    lines.append(f"- {winner_pattern}\n")
    lines.append('\n**Losing pattern:**\n')
    lines.append(f"- {losing_pattern}\n")
    lines.append('\n**Transferable rule:**\n')
    lines.append(f"- {rule}\n\n")
    with learning_log.open('a') as f:
        f.writelines(lines)


def save_run_json(data: dict, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    slug = sanitize_slug(data['prompt'])
    out = runs_dir / f'{stamp}-{slug}.json'
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    return out


def print_table(data: dict) -> None:
    print("| Candidate | Peer Score | Judge Score | Final Score |")
    print("|---|---:|---:|---:|")
    for name, score in sorted(data['final_scores'].items(), key=lambda kv: kv[1]['mode_final_total'], reverse=True):
        print(f"| {name} | {score['mode_peer_total']:.2f} | {score['mode_judge_total']:.2f} | {score['mode_final_total']:.2f} |")
    print()


def print_scoring_map(data: dict) -> None:
    judge_name = next(iter(data['rating_map']['judge_layer']))
    judge_config = data.get('judge_config', {})
    print("Scoring map:")
    print("- Claude peer score: Gemini's review of Claude.")
    print("- Gemini peer score: Claude's review of Gemini.")
    if data.get('self_included'):
        print("- Self peer score: average of Claude's and Gemini's reviews of self.")
    print(f"- Judge score: {judge_name} rates every candidate in an isolated judge-only pass.")
    print(
        f"- Judge model: {judge_config.get('requested_model')} "
        f"(resolved by native runtime to: {judge_config.get('resolved_model')}, fallback label: {judge_config.get('fallback_model')})."
    )
    print(f"- Judge execution path: {judge_config.get('execution_path')}.")
    print("- Who rated whom:")
    print(f"  Gemini -> {', '.join(data['rating_map']['peer_layer']['gemini'])}")
    print(f"  Claude -> {', '.join(data['rating_map']['peer_layer']['claude'])}")
    print(f"  {judge_name} -> {', '.join(data['rating_map']['judge_layer'][judge_name])}")
    print("- Peer review counts:")
    for name, score in data['final_scores'].items():
        print(f"  {name}: {score['peer_review_count']}")
    print("- Final score: average of peer score and judge score, category by category.")
    print("- Table totals: peer, judge, and final columns all use the current mode's category weights so they stay comparable.")
    print()


def print_winner_breakdown(data: dict) -> None:
    breakdown = data.get('winner_breakdown', {})
    print("Winner breakdown")
    print(f"- peer_winner: {breakdown.get('peer_winner')}")
    print(f"- judge_winner: {breakdown.get('judge_winner')}")
    print(f"- final_winner: {breakdown.get('final_winner')}")
    print()


def print_pretty(data: dict, run_path: Path | None = None, learning_path: Path | None = None) -> None:
    print_table(data)
    print_scoring_map(data)
    print_winner_breakdown(data)

    print("Response summaries")
    for name, score in sorted(data['final_scores'].items(), key=lambda kv: kv[1]['mode_final_total'], reverse=True):
        print(f"- **{name}**: {summarize_candidate(name, data, score)}")
        print()

    winner = data['winner']
    runner_up = data.get('runner_up')
    win = data['final_scores'][winner]

    print(f"Winner: {winner}")
    print("Why it wins:")
    for item in win.get('strengths', [])[:3]:
        print(f"- {item}")
    print()

    print("Recommendation:")
    print(f"- Use {winner} as the base answer.")
    if runner_up:
        print(f"- Check whether {runner_up} has one angle or phrasing worth merging into the final version.")
    print()

    print("Run details:")
    print(f"- Mode: {data.get('mode', 'balanced')}")
    print(f"- Self included: {data.get('self_included')}")
    if run_path:
        print(f"- Run saved: {run_path}")
    if learning_path:
        print(f"- Learning log: {learning_path}")
    if data.get('storage_redacted'):
        print("- Stored artifacts were redacted because the prompt or answers looked sensitive.")


def main() -> None:
    ap = argparse.ArgumentParser(description='Wrapper for model_council.py with self answer included by default')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--prompt')
    g.add_argument('--prompt-file')
    ap.add_argument('--self-answer')
    ap.add_argument('--self-answer-file')
    ap.add_argument('--exclude-self', action='store_true')
    ap.add_argument('--gemini-model')
    ap.add_argument('--claude-model')
    ap.add_argument('--judge-model')
    ap.add_argument('--judge-fallback-model')
    ap.add_argument('--judge-provider', help=argparse.SUPPRESS)
    ap.add_argument('--mode', choices=sorted(MODES.keys()), default='balanced')
    ap.add_argument('--pretty', action='store_true', help='Print a human-readable summary instead of raw JSON')
    ap.add_argument('--no-learn', action='store_true', help='Do not save run JSON or append learning log')
    ap.add_argument(
        '--storage-policy',
        choices=['full', 'redact-sensitive', 'redact-all'],
        default='redact-sensitive',
        help='How saved artifacts should handle prompt and answer text when learning capture is enabled.',
    )
    ap.add_argument('--learning-log', default=str(DEFAULT_LEARNING_LOG))
    ap.add_argument('--runs-dir', default=str(DEFAULT_RUNS_DIR))
    args = ap.parse_args()

    prompt = args.prompt or Path(args.prompt_file).read_text(encoding='utf-8').strip()
    if not prompt:
        raise SystemExit('Prompt is empty')

    self_answer = None
    if not args.exclude_self:
        if args.self_answer:
            self_answer = args.self_answer.strip()
        elif args.self_answer_file:
            self_answer = Path(args.self_answer_file).read_text(encoding='utf-8').strip()
        else:
            raise SystemExit('Self is included by default. Pass --self-answer or --self-answer-file, or use --exclude-self.')

    with tempfile.TemporaryDirectory(prefix='model-council-') as td:
        td_path = Path(td)
        prompt_file = td_path / 'prompt.txt'
        prompt_file.write_text(prompt, encoding='utf-8')

        cmd = [sys.executable, str(ENGINE), '--prompt-file', str(prompt_file)]
        if args.gemini_model:
            cmd += ['--gemini-model', args.gemini_model]
        if args.claude_model:
            cmd += ['--claude-model', args.claude_model]
        if args.judge_model:
            cmd += ['--judge-model', args.judge_model]
        if args.judge_fallback_model:
            cmd += ['--judge-fallback-model', args.judge_fallback_model]
        if args.judge_provider:
            cmd += ['--judge-provider', args.judge_provider]
        if args.exclude_self:
            cmd += ['--exclude-self']
        else:
            self_file = td_path / 'self_answer.txt'
            self_file.write_text(self_answer or '', encoding='utf-8')
            cmd += ['--self-answer-file', str(self_file)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or f'Engine failed with exit code {exc.returncode}.'
            raise SystemExit(detail)
        data = json.loads(result.stdout)
        data = apply_mode_weights(data, args.mode)

        run_path = None
        learning_path = None
        storage_data = prepare_storage_data(data, args.storage_policy)
        display_data = copy.deepcopy(data)
        display_data['storage_policy'] = storage_data['storage_policy']
        display_data['storage_redacted'] = storage_data['storage_redacted']
        if not args.no_learn:
            run_path = save_run_json(storage_data, Path(args.runs_dir))
            learning_path = Path(args.learning_log)
            append_learning(storage_data, learning_path)

        if not args.pretty:
            print(json.dumps(display_data, indent=2, ensure_ascii=False))
            return

        print_pretty(display_data, run_path=run_path, learning_path=learning_path)


if __name__ == '__main__':
    main()
