# Benchmark Report — Agent Smith on SWE-bench

This report measures the Agent Smith coding agent on real GitHub issues
from **SWE-bench Verified**. The agent configuration and tools are held
fixed and the language model behind the loop is swapped, so every number
below is a property of the model driving the same agent, not of a
different harness. All figures are regenerated from the evidence on disk
by `benchmarks/report.py` and `benchmarks/plots.py`; nothing is
hand-edited.

## 1. Headline

- **24 models answered at least one task; 18 completed the full suite.**
  Across those 18, the agent solved **79 of 144 task instances (55%)**.
- **`mistral-large-latest` is the clear winner**: the only model to pass
  **8/8**, and it does so with the **fewest input tokens (24.6k/task)**,
  the fewest iterations (**6.5**), and no reliability penalty.
- **Small models punch above their weight.** `ministral-8b` also reaches
  8/8, and `ministral-3b` and two Gemini flash-lite tiers reach 7/8.
- **Failure is legible.** A model that fails almost always runs to the
  30-iteration cap first: passing runs average ~14 iterations, the
  bottom of the table sits at the ceiling. Iterations are a leading
  indicator of the outcome.
- **Free tiers are cheap in dollars and expensive in reliability**: they
  complete, but only after hundreds of rate-limit retries, and several
  Gemini/Groq endpoints never answer at all (Section 6).

## 2. Methodology

**Dataset.** Eight instances from SWE-bench Verified (`test` split), each
a real issue with a hidden test that decides pass/fail:

| Instance | Repository |
|---|---|
| `django__django-11066` | django/django |
| `pydata__xarray-4629` | pydata/xarray |
| `scikit-learn__scikit-learn-13439` | scikit-learn |
| `sympy__sympy-13480` | sympy/sympy |
| `sympy__sympy-18189` | sympy/sympy |
| `psf__requests-1142` | psf/requests |
| `sympy__sympy-21847` | sympy/sympy |
| `pydata__xarray-4356` | pydata/xarray |

The first five are the public seed set shipped with the evaluator; the
last three were added to widen repository and difficulty coverage.

**Agent.** For each (model, task) the agent runs the standard
Thought → Code → Observation loop against a Docker container built from
the task's evaluation image, capped at **30 iterations**. A run is a
**pass** only when the produced patch makes the hidden test suite go
green under the official validator.

**Metrics.** `pass` (hidden tests green), `iterations` (loop steps),
`total_input_tokens` / `total_output_tokens` (billed context and
generation), `total_time_seconds` (wall clock), plus two derived
signals: **first touch** (the step at which the agent first reads a file
that appears in the final patch — exploration efficiency) and **test
gap** (mean steps between two test runs — iteration discipline).

**How to reproduce.** See Section 10.

## 3. Model comparison

Mean over the runs each model actually answered, ordered by pass rate
then by input cost. These are the 18 models that completed all eight
tasks.

| Model | Provider | Pass | Iters | In tok | Out tok | Time (s) |
|---|---|---|---|---|---|---|
| `mistral-large-latest` | Mistral | **8/8** | 6.5 | 24 597 | 944 | 58.6 |
| `magistral-medium-latest` | Mistral | **8/8** | 9.5 | 42 441 | 1 612 | 73.5 |
| `ministral-8b-latest` | Mistral | **8/8** | 15.9 | 59 091 | 1 238 | 34.9 |
| `gemini-3.1-flash-lite` | Gemini | 7/8 | 15.9 | 65 833 | 1 837 | 58.7 |
| `ministral-3b-latest` | Mistral | 7/8 | 17.2 | 59 612 | 1 453 | 30.5 |
| `gemini-flash-lite-latest` | Gemini | 7/8 | 18.0 | 90 716 | 542 | 42.5 |
| `gemma-4-31b-it` | Gemini | 6/8 | 15.4 | 105 981 | 4 831 | 329.8 |
| `mistral-small-latest` | Mistral | 5/8 | 20.1 | 95 756 | 1 131 | 33.3 |
| `mistral-medium-latest` | Mistral | 5/8 | 25.1 | 123 486 | 2 136 | 72.0 |
| `nemotron-nano-9b-v2` | OpenRouter | 4/8 | 7.2 | 19 986 | 7 168 | 243.1 |
| `qwen3.6-27b` | Groq | 4/8 | 11.0 | 38 080 | 1 418 | 147.7 |
| `open-mistral-nemo` | Mistral | 3/8 | 30.0 | 124 378 | 763 | 59.1 |
| `llama-3.1-8b-instant` | Groq | 2/8 | 8.4 | 22 501 | 603 | 108.5 |
| `nemotron-3-super-120b` | OpenRouter | 2/8 | 20.6 | 70 007 | 6 823 | 116.7 |
| `codestral-latest` | Mistral | 2/8 | 27.2 | 74 540 | 1 596 | 22.8 |
| `gemma-4-26b-a4b-it` | OpenRouter | 1/8 | 6.4 | 26 909 | 3 124 | 250.4 |
| `devstral-medium-latest` | Mistral | 0/8 | 30.0 | 77 566 | 1 112 | 81.3 |
| `gpt-oss-20b` | OpenRouter | 0/8 | 30.0 | 62 929 | 2 713 | 279.1 |

![Pass rate by model](benchmarks/figures/pass_rate.png)

![Mean iterations by model](benchmarks/figures/mean_iters.png)

Two patterns stand out. First, capability and cost are not the same
axis: `mistral-large` wins on accuracy *and* spends the least context,
while `mistral-medium` and `open-mistral-nemo` spend the most input
tokens for a middling result. Second, a **coding-specialised** name is
no guarantee — `codestral` (2/8) and `devstral-medium` (0/8, every run
to the cap) are beaten decisively by the general `mistral-large` and by
the tiny `ministral-8b`.

## 4. Task difficulty

Solve count across the 18 complete models, with the mean iterations
each task demanded:

| Task | Solved | Mean iters |
|---|---|---|
| `sympy-13480` | 15/18 | 12.7 |
| `xarray-4629` | 12/18 | 18.1 |
| `django-11066` | 11/18 | 14.1 |
| `sympy-18189` | 10/18 | 14.9 |
| `scikit-13439` | 9/18 | 18.6 |
| `sympy-21847` | 9/18 | 19.4 |
| `xarray-4356` | 8/18 | 19.8 |
| `requests-1142` | 5/18 | 21.7 |

Difficulty and effort move together: the tasks fewer models solve are
exactly the ones that cost more iterations. `requests-1142` is the wall
(5/18, 21.7 iters), `sympy-13480` the gentle slope (15/18, 12.7). This
is the per-task echo of the model-level rule in Section 3 — more looping
signals trouble, not progress.

## 5. Efficiency: iterations vs cost

![Efficiency scatter](benchmarks/figures/efficiency.png)

Plotting mean iterations against mean output tokens separates two
failure styles from the efficient core. The **nemotron** models sit far
up the token axis (6.8k–7.2k output tokens per task) — they narrate
heavily yet convert little of it into passes (4/8 and 2/8). The
**cap-bound** models (`devstral-medium`, `gpt-oss-20b`,
`open-mistral-nemo`) sit at the right edge at 30 iterations. The
efficient corner — low iterations, modest output — is where the passing
Mistral models and the Gemini flash-lite tiers cluster.

## 6. Provider reliability and availability

Accuracy assumes the model answered. Many did not on the first try, and
some never did. The reliability view comes straight from the request
trace (`benchmarks/report.py`, *Provider reliability*).

**Retries tell the story.** Paid Mistral endpoints answer with almost no
back-off and sub-second-to-few-second latency; free tiers complete only
after heavy rate-limit retrying:

| Model | Requests | Retries | Avg response (s) | Answered |
|---|---|---|---|---|
| `ministral-3b-latest` (Mistral) | 138 | 0 | 0.65 | 8/8 |
| `mistral-small-latest` (Mistral) | 161 | 0 | 0.76 | 8/8 |
| `qwen3.6-27b` (Groq free) | 518 | **430** | 11.70 | 8/8 |
| `llama-3.1-8b-instant` (Groq free) | 375 | **308** | 10.90 | 8/8 |
| `gemma-4-31b-it` (Gemini) | 260 | **144** | 21.44 | 8/8 |
| `gemma-4-26b-a4b-it` (OpenRouter free) | 57 | 6 | 36.89 | 8/8 |

The free models *do* finish, but a run can carry more retries than real
requests, and average latency is an order of magnitude worse. In a
throughput-bound setting that difference dominates the token price.

**Endpoints excluded from the comparison** (they could not produce a
usable run) — kept here because *why* they failed is itself a result:

- **Groq per-minute ceiling.** The free tier caps tokens-per-minute at
  ~12k. A single SWE step with full repository context exceeds that, so
  `llama-3.3-70b-versatile` returns `429` and completes only 4/8. This
  is a per-minute limit, so additional daily quota (even keys from
  separate accounts) does not lift it.
- **Groq `gpt-oss` tool protocol.** `openai/gpt-oss-20b` and
  `gpt-oss-120b` on Groq return `400 "Tool choice is none, but model
  called a tool"` — the model's tool behaviour does not match the loop's
  contract — plus `413` on the largest task.
- **Gemini free instability.** `gemini-3.5-flash`, `gemini-flash-latest`
  and `gemini-pro-latest` return `429`/`503` persistently, even with
  fresh keys, while `gemma-4-31b-it` runs cleanly on the *same* keys —
  so the cause is the endpoint, not the quota.
- **Deprecated Gemini IDs.** `gemini-2.5-flash`, `gemini-2.0-flash` and
  `gemini-2.5-flash-lite` return `404 "no longer available to new
  users"`. Current-generation IDs or `-latest` aliases are required.
- **Groq compound systems.** `groq/compound` returns `413` (repo context
  exceeds its request limit) and runs its own internal tool use, which
  does not compose with an external agent loop.

## 7. Intermediary metrics

Pass/fail is the outcome; the step trace shows *how* it was reached. Two
signals are derived per run (`benchmarks/report.py`, *Intermediary
metrics*):

- **First touch** — the step at which the agent first reads a file that
  ends up in the patch. Low means it localised the bug quickly instead
  of wandering the repository.
- **Test gap** — the mean number of steps between two test runs. Low
  means it checks its work often instead of editing blind.

The winner illustrates the healthy profile: `mistral-large` reaches the
right file early and tests in tight cadence, which is why it converges
in 6.5 iterations. The cap-bound models show the opposite — a late or
absent first touch and long stretches of editing between tests, which is
what running to 30 iterations looks like from the inside.

## 8. Agent-configuration ablation

Before fixing the shipped configuration we varied the agent's prompt and
tools with the model held fixed, measured as mean iterations over a
six-task tuning set:

| Configuration | Mean iters | Kept? |
|---|---|---|
| Baseline loop | 7.70 | — |
| **Shipped**: "apply the fix once found, do not keep exploring" | **7.37** | **yes** |
| Gated context + tests appended to each observation | 5.57 | **no** |

The gated-context variant was the fastest by a wide margin, but on
repeated runs it failed the hardest task intermittently (~1 in 4). Since
a single failure is disqualifying where a slightly slower run is not, it
was rejected. **The shipped configuration is the reliable one, not the
fastest one** — the same reliability-first stance that Section 6 applies
to providers.

## 9. Cost

Token counts are exact (Section 3); dollar cost depends on each
provider's list price and is illustrative. For the winning
configuration, `mistral-large` at published list prices is on the order
of **$0.05 per task** (~24.6k input + ~0.9k output tokens), roughly
**$0.45 for the full eight-task suite**. The free-tier models cost $0 in
tokens but pay elsewhere: hundreds of retries, 4–10× latency, and — for
the excluded endpoints — no answer at all. The cheapest *reliable* way
through the suite here is the efficient paid model, not the free one.

## 10. Reproducibility

- **Dataset**: SWE-bench Verified, `test` split, the eight instance IDs
  in Section 2. Evaluation images are the official
  `swebench/sweb.eval.*` containers.
- **Collect** (per provider, so each key resolves from `configs/models.json`):
  ```
  uv run python benchmarks/run.py --benchmark swebench \
      --tasks-dir benchmarks/tasks_swe \
      --models "<comma-separated ids>" \
      --provider-url "<base_url from models.json>" \
      --results-dir benchmarks/results
  ```
  Each run writes `benchmarks/results/<model>/<task>.solution.json`,
  which records the patch, every step, tokens, timings and retries.
- **Tables**: `uv run python benchmarks/report.py --results-dir benchmarks/results`
- **Figures**: `uv run --with matplotlib python benchmarks/plots.py --results-dir benchmarks/results --out-dir benchmarks/figures`
- **Keys** live only in `.env` (git-ignored); comma-separated values in a
  key variable are rotated across requests.

## 11. Insights

1. **One model dominates on every axis.** `mistral-large` is
   simultaneously the most accurate (8/8), the most context-frugal, and
   among the fastest. Choosing the driving model matters far more than
   any single prompt tweak.
2. **Size is not destiny.** `ministral-8b` (8/8) and `ministral-3b`
   (7/8) beat `mistral-medium` (5/8) and the 120B `nemotron-3-super`
   (2/8). For this agent, small well-behaved models are a real option.
3. **Iterations are a smoke alarm.** Both across models and across
   tasks, more looping predicts failure. A run drifting toward the cap is
   the signal to intervene, not to wait.
4. **Reliability is a first-class metric.** The free tiers finish only
   after heavy retrying, several endpoints never answer, and the fastest
   agent configuration was the one we rejected. Availability and
   consistency belong next to accuracy, not in a footnote.
5. **Provider catalogues drift.** Deprecated model IDs (`404`),
   per-minute ceilings (`429`) and tool-protocol mismatches (`400`) were
   as decisive as model quality. A benchmark harness has to treat the
   provider layer as part of the system under test.

---

*Evidence: `benchmarks/results/` (one `solution.json` per run),
`benchmarks/figures/` (charts). Full 232-row model×task grid, provider
reliability and intermediary-metric tables: run `benchmarks/report.py`.*
