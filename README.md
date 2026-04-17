# OpenClaw Model Council Skill

**Transparent multi-model comparison + learning for OpenClaw**

`openclaw-model-council-skill` is an OpenClaw skill for structured multi-model evaluation.

It takes the same prompt, collects candidate answers from multiple models, runs peer review between them, adds an isolated native judge pass, and returns a transparent final ranking with reusable learnings.

This is not just another skill.

It is a **reference implementation for transparent multi-model judgment inside OpenClaw**.

---

## Why This Exists

OpenClaw can already route work across strong models.

But in practice, when people compare outputs manually, they usually run into the same problems:

- one answer is privileged by default
- model comparisons are informal
- scoring logic is unclear
- winner decisions feel subjective
- good lessons are lost after the comparison ends

`openclaw-model-council-skill` solves that by turning model comparison into a **structured evaluation system** with visible rules, visible scores, and a learning loop.

---

## What It Actually Does

For a given prompt, the skill:

1. gets candidate answers from:
   - Gemini
   - Claude
   - self / current OpenClaw answer

2. runs a peer-review layer:
   - Gemini reviews Claude + self
   - Claude reviews Gemini + self
   - no model reviews itself

3. runs an isolated native judge pass through the OpenClaw / Codex runtime

4. calculates:
   - **Peer Score**
   - **Judge Score**
   - **Final Score**

5. returns:
   - score table
   - scoring map
   - winner breakdown
   - concise response summaries
   - winner
   - why it won
   - recommendation

6. saves:
   - raw run JSON
   - distilled learning entry
   - reusable winner/loser patterns

---

## Why It’s Valuable

This skill solves a higher-order workflow problem.

### It helps you:
- compare models transparently
- understand **why** one answer won
- avoid hidden or hand-wavy judgment
- turn one good comparison into a lasting improvement
- build reusable evaluation logic over time

That makes it more than a utility skill.

It is closer to:
## a **decision framework packaged as a skill**

---

## Best Use Cases

Use it when comparison matters more than raw speed:

- script and hook evaluation
- brand messaging
- product positioning
- naming
- offer framing
- strategic trade-offs
- architecture choices
- implementation options where multiple strong answers exist

---

## When Not To Use It

Do **not** use this for:

- trivial questions where one strong answer is enough
- urgent tasks where debate adds noise
- hard factual verification that should use browsing or primary sources directly

---

## Scoring Design

The scoring is intentionally explicit.

### Peer layer
- Claude peer score = Gemini’s review of Claude
- Gemini peer score = Claude’s review of Gemini
- Self peer score = average of Claude’s and Gemini’s reviews of self

### Judge layer
The judge runs through the **OpenClaw-native runtime** using `codex exec`.

Default judge:
- `openai-codex/gpt-5.3-codex`

Fallback:
- `openai-codex/gpt-5.1`

The judge is **not** implemented as:
- raw `https://api.openai.com/v1/...`
- direct OpenAI auth code
- external provider-side bolt-on logic

### Final score
For each candidate:

- **Final per-category score = average(peer score, judge score)**
- **Final total = mode-weighted average of the final per-category scores**

---

## Modes

Modes do **not** change who rates whom.
They only change category weighting in the final score.

Available modes:

- `balanced`
- `brand`
- `strategy`
- `execution`

### Use `brand` for:
- hooks
- scripts
- content
- messaging
- positioning

### Use `strategy` for:
- decisions
- capital allocation
- trade-offs
- long-term choices

### Use `execution` for:
- operational decisions
- implementation choices
- practical next steps

---

## Output Order

Pretty output always follows this order:

1. **Score table**
2. **Scoring map**
3. **Winner breakdown**
4. **Response summaries**
5. **Winner**
6. **Why it wins**
7. **Recommendation**

That makes the output easier to trust and easier to inspect.

---

## Learning Flow

The skill includes a persistent learning loop.

For normal runs, it saves:

### 1. Raw run history
Stored as JSON for replay / audit.

### 2. Distilled learning
A reusable lesson based on:
- why the winner won
- why others lost
- what to apply next time

This makes the skill useful not just for one comparison, but for improving answer quality over time.

---

## Repository Layout

```text
openclaw-model-council-skill/
├── README.md
├── LICENSE
├── .gitignore
├── SKILL.md
├── references/
│   └── rubric.md
└── scripts/
    ├── model_council.py
    └── run_model_council.py
```

---

## Requirements

- OpenClaw / Codex runtime available locally
- `codex` CLI available in the environment
- Gemini access for Gemini answer + peer review
- Claude access for Claude answer + peer review

Typical environment variables:
- `GEMINI_API_KEY`
- `ANTHROPIC_API_KEY`

The native judge path is intended to use the local OpenClaw/Codex runtime, not direct provider HTTP calls.

---

## Usage

### Default council run
```bash
python3 scripts/run_model_council.py \
  --prompt-file /path/to/prompt.txt \
  --self-answer-file /path/to/self_answer.txt \
  --pretty
```

### Quick one-shot prompt
```bash
python3 scripts/run_model_council.py \
  --prompt "Which message is stronger for premium positioning?" \
  --self-answer "My answer goes here" \
  --mode brand \
  --pretty
```

### Exclude self
```bash
python3 scripts/run_model_council.py \
  --prompt-file /path/to/prompt.txt \
  --exclude-self \
  --pretty
```

### Disable learning for one run
```bash
python3 scripts/run_model_council.py \
  --prompt-file /path/to/prompt.txt \
  --self-answer-file /path/to/self_answer.txt \
  --no-learn \
  --pretty
```

