*This project has been created as part of the 42 curriculum by acaldeir, rusilva-.*

# Agent Smith

An autonomous coding agent that reads a task, explores a codebase, writes and
runs code, observes the result, and iterates until it has a solution. It is
evaluated on two benchmarks: **MBPP** (short function-writing problems) and
**SWE-bench** (real bug fixes in large open-source repositories).

## Description

The goal of the project is to build an agent that solves programming tasks the
way an engineer does: not by generating a single answer, but by reasoning,
executing, and refining. Each turn the model produces one short thought and one
Python code block. The code runs in a sandbox and its output comes back as the
next observation. The loop continues until the agent submits a final answer or
exhausts its budget.

Model-generated code runs in a separate child process with an import
allowlist, disabled dangerous builtins, a path-checked `open`, no access to
private attributes, and hard limits on time and memory. This keeps mistakes
and runaway code away from the agent. It is not a security boundary against
deliberately hostile code, so the SWE-bench repositories are only touched
inside their Docker container. The agent reaches the outside world only
through a small set of tools exposed over the Model Context Protocol (MCP): for
MBPP a single `run_tests`, for SWE-bench a handful of repository inspection and
editing tools that operate inside the task's Docker container.

## Instructions

**Requirements:** Python ≥ 3.10, [uv](https://docs.astral.sh/uv/), and Docker
(only needed to run SWE-bench evaluation containers).

**Install:**

```bash
uv sync
```

**API keys** are read from the environment. Create a `.env` file at the repository
root by copying `.env.example` (`.env` itself is git-ignored). Keys may be
comma-separated. The agent rotates through them when a provider rate-limits:

```
MISTRAL_API_KEY=key1,key2
# optional, for the multi-model benchmark
GROQ_API_KEY=...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=...
```

Providers and their endpoints are declared in `configs/models.json`.

**Run the sandbox** on its own (reads a statement from stdin, one per line in a
terminal or a whole payload from a pipe):

```bash
uv run sandbox configs/sandbox_template.json
```

**Solve a single task** through the real entry points (task files arrive in the
format provided to the agent):

```bash
uv run python -m agent_mbpp     --task-file task.json --output solution.json
uv run python -m agent_swebench --task-file task.json --output solution.json
```

A different model can be selected with `--model-name` and `--provider-url`.

**Run the benchmark grid** (every task × every model) and build the report tables:

```bash
uv run python benchmarks/run.py --benchmark swebench \
    --tasks-dir <dir> --models "model/a,model/b"
uv run python benchmarks/report.py --results-dir benchmarks/results
uv run --with matplotlib python benchmarks/plots.py \
    --results-dir benchmarks/results --out-dir benchmarks/figures
```

**Checks:**

```bash
uv run pytest
uv run mypy
```

## System architecture

The project is split so that the agent, the sandbox, and the tools depend only
on a shared contract, never on each other's internals.

- **`contract/`**: the shared vocabulary. It holds the data models
  (`SandboxConfig`, the per-step trace, the `solution.json` schema), the
  `Sandbox` protocol the loop programs against, and the fixed feedback strings.
- **`agent/`**: the reasoning side. `loop.py` runs the Thought→Code→Observation
  cycle. `profiles.py` holds everything benchmark-specific (prompt, limits,
  formats). `providers.py` talks to the LLM endpoint with key rotation and
  retries. `budget.py` tracks the token/time budget. `extract.py` pulls the code
  block out of a reply. `docker_bridge.py` starts and tears down the SWE-bench
  evaluation container.
- **`sandbox/`**: the execution side. `cli.py` is the entry point,
  `supervisor.py` (`LocalSandbox`) spawns and watches the cell, and `cell.py` is
  the locked-down child process where model code actually runs. `mcp_client.py`
  connects to a tool server.
- **`mcp_tools_mbpp.py` / `mcp_tools_swebench.py`**: the MCP tool servers.
- **`agent_mbpp.py` / `agent_swebench.py`**: the two benchmark entry points that
  build the profile, wire up the sandbox and tools, and hand control to the loop.
- **`benchmarks/`**: `run.py` drives the full task×model grid through the real
  entry points. `report.py` turns the resulting `solution.json` files into the
  report tables.

## Agent loop explanation

One task is one call to `agent/loop.py`. Each iteration:

1. The recent conversation is sent to the model, capped by the smaller of the
   remaining output budget and the per-step ceiling, so a single reply can never
   spend the whole budget.
2. The reply is parsed: exactly one thought and one ```python block. If no block
   is found the agent is told so and the turn is retried.
3. The code runs in the sandbox and its printed output becomes the next
   observation. `final_answer(...)` ends the task.
4. The budget is charged the reply's real token cost, taken from the provider's
   usage counts. The loop stops on a submitted answer, or when iterations,
   tokens, or wall-clock time run out.

The two benchmarks share this loop and differ only through their `Profile`: MBPP
uses a spartan prompt and a tight budget (its tokens are paid every turn), while
SWE-bench uses a method-oriented prompt and far larger limits.

## Sandbox design

The sandbox is two processes. The **supervisor** (`LocalSandbox`) prepares the
environment and launches the **cell** as a separate Python process, feeding the
code on stdin and reading its output back under a wall-clock and memory limit
(`RLIMIT_AS`). Isolating execution in a child process means a crash, a memory
kill, or a runaway loop takes down only the cell, never the agent.

Inside the cell, before any model code runs:

- a `sys.meta_path` finder blocks every import that is not on the allowlist.
- the dangerous builtins (`eval`, `exec`, `compile`, `open`, `input`,
  `breakpoint`) are replaced with stubs that raise, and `__import__` is guarded.
- `open` is swapped for a version that resolves the real path and refuses
  anything outside the allowed directories, which stops path-traversal escapes.
- the code is parsed before it runs, and any private attribute (`_sys`,
  `__class__`, `__subclasses__`) is refused, together with `getattr` and
  `vars`, since those are the paths from an allowed module back to `os`.

The allowlist, the writable directories, and the limits all come from the
sandbox config file, so the same cell enforces whatever policy the config
declares.

## Tool implementation details

Tools are provided by an MCP server started as a subprocess and spoken to over
stdio. At startup the supervisor asks the server for its tool schemas, turns
them into a text manual for the system prompt, and injects a wrapper for each
tool into the cell's namespace. When model code calls a tool, the wrapper sends
a JSON request over a pipe to the supervisor, which forwards it to the MCP
server and writes the result back, so the untrusted cell never holds a network
connection or a client of its own.

- **MBPP** exposes one tool, `run_tests`, which runs the candidate function and
  the task assertions in a fresh process and reports, as JSON, whether they all
  passed.
- **SWE-bench** exposes repository tools (`read_file`, `list_files`,
  `search_code`, `search_function_or_class_definition_in_code`,
  `find_references`, `edit_file`, `run_command`, `run_tests`, `get_patch`) that
  operate inside the task's Docker container (or a local testbed checkout).

## Benchmark results and analysis

The full comparison lives in [`BENCHMARK_REPORT.md`](BENCHMARK_REPORT.md): the
same agent driven by **18 models across four providers** on eight SWE-bench
Verified issues (twelve completed the full suite), with every model × task
result (pass, iterations, tokens, time), provider reliability, the
intermediary efficiency metrics, and the ablations behind the final agent
configuration. Charts are in [`benchmarks/figures/`](benchmarks/figures/).

Headline findings: `mistral-large-latest` wins on every axis that matters.
It scores **7/8**, reproduced task for task by a second full pass, with the
fewest iterations and the least input context among the leaders, and its only
miss is a task none of the twelve models solved. The best free model
(`gemini-flash-lite`) matches the pass count but needs several API keys to
survive one suite. Iteration count is a leading indicator of failure, the
agent piece that moved accuracy most was a verification gate rather than any
prompt tweak, and a third of the fleet was stopped by provider ceilings or
protocol mismatches before the model could think, so the report treats the
provider layer as part of the system under test.

## Resources

- SWE-bench: Jimenez et al., *SWE-bench: Can Language Models Resolve Real-World
  GitHub Issues?* (2023).
- MBPP: Austin et al., *Program Synthesis with Large Language Models* (2021).
- ReAct: Yao et al., *ReAct: Synergizing Reasoning and Acting in Language
  Models* (2022), the reason/act loop this agent follows.
- Model Context Protocol: <https://modelcontextprotocol.io>.
- Provider APIs (OpenAI-compatible): Mistral, Groq, Google Gemini, OpenRouter.
- Tooling: [uv](https://docs.astral.sh/uv/), pytest, flake8, mypy.

### AI usage

AI was used to support specific aspects of the project, namely:

- Clarifying a few doubts about the project requirements and the scope of the
  SWE-bench and MBPP.
- Answering questions about concepts such as the Model Context Protocol (MCP),
  process isolation and sandboxing, and reasoning-and-acting agent loops.
- Getting a second opinion on the initial project skeleton.
- Discussing and evaluating different approaches to dividing the work between
  team members.
- Assisting with debugging by explaining error messages and pointing to likely
  causes.
- Suggesting possible optimizations to some parts of the code, including the
  agent's method prompt and the tools' behavior.
- Structuring and drafting the README and BENCHMARK_REPORT.
