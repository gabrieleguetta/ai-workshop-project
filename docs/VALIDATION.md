# Executed local validation

Environment: Windows PowerShell, Python 3.9.6, pytest 8.4.2, installed Codex CLI 0.169.0.
Commands below were actually executed from the repository after the implementation and regression
fixes. The repository initially contained no application or test suite.

| Exact command | Actual final result |
| --- | --- |
| `.venv\Scripts\python -m pytest -q` | **126 passed, 0 failed, 0 skipped** |
| `.venv\Scripts\python -m ruff check .` | All checks passed |
| `.venv\Scripts\python -m ruff format --check .` | 17 files already formatted |
| `.venv\Scripts\python -m mypy` | Success, no issues in 8 source files |
| `.venv\Scripts\python main.py --help` | CLI help with provider overrides displayed successfully |
| `.venv\Scripts\python main.py --provider codex` | Expected setup failure: Codex not signed in; supported login instructions, no traceback |
| `git diff --check` | Passed |

The default tests cover session freshness, confirmed/unanswered requirements, scoped preferences
and targets, first-day and continuation rules, real-turn/revision approval checks, refusal/revisit/
explicit-choice/delegation transitions, separate selected snapshots and current drafts, preservation
of unaffected approvals, quantity/recipe/serving invalidation, unavailable/error/partial evidence,
ingredient aggregation, provider selection, JSONL parsing, timeouts, HTTP/subprocess failures,
runtime isolation, secret-safe errors and CLI startup/exit.

No live model completion ran: Codex reports not signed in and no OpenRouter key/model is configured
in this environment. Provider protocol and failure behavior were tested with deterministic boundary
fakes; that does not establish real account connectivity. Run `codex login` outside the planner,
or configure OpenRouter, to use a real conversation. No credentials were requested, copied or saved.

No real recipe/nutrition/price integrations or real end-to-end persona tests ran. Those tools are
intentionally disconnected and remain Gabriele's responsibility, including the vegetarian on a
tight budget, nut-allergy user and high-protein runner. Internal fixtures do not count as those
real scenarios. There are no skipped failing tests or external food calls in the default suite.
