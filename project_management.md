# Project Management — Agent Smith

## Alexandre — runtime + tools + README (~110h)

1. Sandbox — security (imports, builtins, paths, network), timeout, memory, feedback, CLI (45h)
2. MCP client — dynamic discovery, generated manual, stdio and HTTP (20h)
3. MBPP tools — `mcp_tools_mbpp.py` with `run_tests` (5h)

Tasks 1 to 3 must be done first — they are required for integration and testing.

4. SWE-bench tools — the 9 mandatory tools, calling the docker bridge via the contract (20h)
5. README — architecture, loop, sandbox, tools, results (20h)

## Rui — agent + benchmark (~140h)

1. Contract with Alexandre — interfaces, Pydantic models, feedback format (4h)
2. Agent loop + code extraction, against a mock sandbox (26h)
3. Providers — multi-provider abstraction, key rotation, retries, usage tracking (22h)
4. System prompt v1 (8h)
5. Docker bridge — lifecycle, mounts, patch, cleanup (22h)
6. Integration — real sandbox, MBPP end to end, then SWE-bench (10h)
7. Tuning — lower iterations and token usage (17h)
8. Benchmark — 5+ models on 3+ tasks, ablation, `BENCHMARK_REPORT.md` (31h)
