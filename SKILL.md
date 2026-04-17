---
name: openclaw-model-council-skill
description: 'OpenClaw skill for structured multi-model comparison. Compare Claude, Gemini, and Codex/self on the same prompt, have them critique each other with a fixed rubric, then add a dedicated OpenAI/Codex judge pass so every candidate gets a transparent peer score, judge score, and final score. Use when the user asks for ''your brothers'' opinion'', ''ask your brothers'', ''council this'', ''let Claude and Gemini debate this'', ''compare all 3'', ''rate your answer too'', or wants ideas, scripts, positioning, naming, or strategy options judged side by side. Default behavior: include your own answer unless the user explicitly asks to exclude it. Save learnings by default, but redact stored artifacts when the prompt or answers look sensitive.'
---

# OpenClaw Model Council Skill

Run structured multi-model comparison instead of relying on a single answer.

## Core workflow

1. Get the exact prompt/question to compare.
2. Produce your own answer first by default.
3. Run `scripts/run_model_council.py` as the wrapper so your answer is included automatically.
4. Let the wrapper save the run JSON and append a distilled learning by default.
5. Read the result.
6. Return in this order:
   - score table for all candidates
   - 2–4 sentence summary for each candidate answer
   - winner
   - why it won
   - your final recommendation or synthesis

## Default behavior

- Include your own answer by default.
- Save each run JSON by default.
- Append a reusable learning to the council learning log by default.
- Redact saved artifacts automatically when the prompt or answers look sensitive.
- Use the current session's self answer as the Codex answer.
- Use `openai-codex/gpt-5.3-codex` as the dedicated judge by default.
- Fall back to `openai-codex/gpt-5.1` if the default judge fails.
- Execute the judge through the OpenClaw-native runtime path (`codex exec`), not through direct provider HTTP calls.
- Exclude your answer only when the user says things like:
  - Claude vs Gemini only
  - exclude yourself
  - don't include your answer
- Disable learning capture only when the user explicitly asks not to log or learn.

## Modes

The wrapper supports these modes:
- `balanced` — default
- `brand` — heavier `brand_fit`, `clarity`, and `originality` weighting
- `strategy` — heavier `correctness` and `practicality` weighting
- `execution` — heavier `actionability` and `practicality` weighting

Use `brand` for hooks, messaging, positioning, scripts, offers, and content.
Use `strategy` for trade-off questions and option comparison.
Use `execution` for implementation decisions and operational next steps.

## Commands

### Default: compare Claude, Gemini, and your answer, then auto-learn
```bash
python3 scripts/run_model_council.py \
  --prompt-file /path/to/prompt.txt \
  --self-answer-file /path/to/self_answer.txt \
  --pretty
```

### Quick one-shot prompt
```bash
python3 scripts/run_model_council.py \
  --prompt "Which hook is stronger for premium brand authority?" \
  --self-answer "<your answer here>" \
  --mode brand \
  --pretty
```

### Claude and Gemini only
```bash
python3 scripts/run_model_council.py --prompt-file /path/to/prompt.txt --exclude-self --pretty
```

### Disable learning capture for one run
```bash
python3 scripts/run_model_council.py \
  --prompt-file /path/to/prompt.txt \
  --self-answer-file /path/to/self_answer.txt \
  --no-learn \
  --pretty
```

### Keep full stored artifacts even when sensitive patterns are detected
```bash
python3 scripts/run_model_council.py \
  --prompt-file /path/to/prompt.txt \
  --self-answer-file /path/to/self_answer.txt \
  --storage-policy full \
  --pretty
```

### Override the OpenAI judge alias explicitly
```bash
python3 scripts/run_model_council.py \
  --prompt-file /path/to/prompt.txt \
  --self-answer-file /path/to/self_answer.txt \
  --judge-model openai-codex/gpt-5.3-codex \
  --judge-fallback-model openai-codex/gpt-5.1 \
  --pretty
```

## Transparency rule

- Gemini rates Claude + self only
- Claude rates Gemini + self only
- an isolated OpenAI/Codex judge evaluates every candidate in a separate judge-only pass
- the judge is executed through OpenClaw-native model routing, not a direct provider API call
- neither external model rates itself
- self is never rated by itself or by the assistant/coordinator directly
- final output always shows a score table with peer score, judge score, and final score for all candidates first

## Scoring formula

- Claude peer score = Gemini's review of Claude
- Gemini peer score = Claude's review of Gemini
- Self peer score = average of Claude's and Gemini's reviews of self
- Judge score = isolated judge review of the candidate
- Final per-category score = average of peer score and judge score
- Final total score = mode-weighted average of the final per-category scores
- Table totals use the active mode weights for peer, judge, and final columns so the columns stay comparable

This is more structurally fair than judging only `self`, because every candidate now gets the same two-layer structure:
- peer layer
- judge layer

This is better than the previous judge setup because:
- the judge is no longer a direct provider-side bolt-on
- the assistant is no longer indirectly advantaged by a coordinator heuristic
- the same isolated OpenClaw-native judge model evaluates every candidate through the runtime itself

Judge model details:
- OpenClaw self model label: `openai-codex/gpt-5.4`
- Codex environment label: `openai-codex/gpt-5.3-codex`
- fallback label: `openai-codex/gpt-5.1`
- Native judge execution path: `codex exec`
- In runtimes that reject slash-form model labels, the script retries the same requested judge through the native alias accepted by that runtime, still via `codex exec` rather than direct provider API access.

## Learning rule

Each normal run should leave behind two artifacts:
- raw run JSON in `memory/learnings/model-council-runs/`
- distilled reusable rule in `memory/learnings/model-council.md`

The goal is not to store scores for their own sake.
The goal is to extract reusable answer-quality patterns:
- why the winner won
- why others lost
- what rule to apply next time

By default the wrapper uses `--storage-policy redact-sensitive`, which preserves learnings while avoiding verbatim prompt and answer storage when obvious secrets or tokens are present.

## Output handling

The wrapper/engine returns JSON with:
- prompt
- candidates
- self_included
- judge_config
- peer_reviews
- judge_review
- rating_map
- score_formula
- winner_breakdown
- final_scores
- winner
- mode
- synthesis_notes

Final scoring uses:
- peer score for each candidate
- isolated judge score for each candidate
- explicit averaging between peer and judge scores
- mode-specific category weighting only when turning per-category scores into a final total

Do not dump raw JSON to the user unless they ask.
Translate it into:
- score table first
- scoring map second
- winner breakdown third
- concise summary per candidate fourth
- winner fifth
- why it wins sixth
- final recommendation seventh

## Good use cases

- content/script selection
- brand messaging
- product positioning
- naming
- offer framing
- architecture trade-offs
- strategic decisions where comparison matters more than single-model speed

## Avoid

- trivial questions where a single good answer is enough
- requests needing immediate action with no value from debate
- sensitive decisions where more opinions will create noise instead of clarity
- hard factual verification where you should browse or inspect primary sources instead of voting among models

## Limits

- The judge is isolated in role, but it is still a model judgment rather than ground truth.
- External reviewers can still produce noisy judgments, so treat the council as structured comparison, not ground truth.
- `self` still has two peer reviews while `claude` and `gemini` each have one, so the peer layer is not perfectly symmetric.
- The native judge requires the OpenClaw/Codex runtime to be installed and authenticated for the requested model family.
- Some Codex runtimes may reject slash-form model labels like `openai-codex/...`; when that happens, the skill retries the runtime-native alias automatically and reports the resolved model in the run metadata.
- Saved artifact redaction is heuristic. If the prompt is sensitive and you do not want any persistence, use `--no-learn`.

## Resources

- Read `references/rubric.md` when you need to explain or adjust scoring.
- Use `scripts/run_model_council.py` as the default wrapper.
- Use `scripts/model_council.py` as the lower-level engine.
