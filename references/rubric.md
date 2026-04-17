# OpenClaw Model Council Skill Rubric

Use a 1-10 score for each category.

## Categories

### correctness
Is the answer factually and logically sound?

### practicality
Can the user act on it easily in the real world?

### clarity
Is it easy to understand and well-structured?

### originality
Does it offer a useful angle beyond generic advice?

### brand_fit
Does it fit the user's stated tone, positioning, or brand context?

### actionability
Does it produce a clear next move?

## Review rules

- Be specific about weaknesses.
- Penalize fluff and vague abstraction.
- Penalize overconfidence without support.
- Reward clean trade-off handling.
- Reward answers that fit the actual user context, not generic internet advice.
- Keep model names irrelevant to scoring.
- The judge uses the same rubric but a separate judge-only role.
- The dedicated judge model is routed through the OpenClaw/Codex runtime, not direct provider HTTP calls.
- Preferred judge label: `openai-codex/gpt-5.3-codex`
- Fallback judge label: `openai-codex/gpt-5.1`

## Mode guidance

### balanced
Use for general comparison.

### brand
Lean slightly harder on brand_fit, clarity, and originality when interpreting the result.

### strategy
Lean slightly harder on correctness, practicality, and trade-off quality when interpreting the result.

### execution
Lean slightly harder on actionability and practicality when interpreting the result.

## Score architecture

Peer layer:
- Gemini reviews Claude and self
- Claude reviews Gemini and self
- Neither model reviews itself

Judge layer:
- An isolated OpenClaw-native Codex judge reviews every candidate
- The judge is a separate evaluation pass, not the coordinator directly scoring itself
- OpenClaw judge label: `openai-codex/gpt-5.3-codex`
- OpenClaw fallback label: `openai-codex/gpt-5.1`
- Native execution path: `codex exec`
- If the runtime rejects slash-form model labels, the skill retries the runtime-native alias through the same Codex execution path.

Per-candidate peer score:
- Claude = Gemini's review of Claude
- Gemini = Claude's review of Gemini
- Self = average of Claude's and Gemini's reviews of self

Final score:
- Average peer score and judge score category by category
- Then apply the selected mode's category weights to compute the total
- The displayed peer, judge, and final totals should all use the active mode weights so the table columns stay comparable

## Mode weighting

Modes do not change who rates whom.
Modes only change how the final per-category scores are weighted in the total:
- `balanced`: equal weight across categories
- `brand`: heavier `brand_fit`, `clarity`, and `originality`
- `strategy`: heavier `correctness` and `practicality`
- `execution`: heavier `actionability` and `practicality`

## Remaining imperfections

- The judge is more consistent than the previous mixed-provider judge setup, but it is still one model family making the final evaluation layer.
- `self` still receives two peer reviews while `claude` and `gemini` each receive one.
- Native model label support can vary by runtime/account, so the resolved judge model may differ from the requested slash-form label even though execution stays inside the native Codex runtime.
