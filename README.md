# Nutrition Planner: a Human–AI Collaborative Meal-Planning Agent

Final project for **Workshop in Developing Human–AI Collaborative Systems** (BIU 89-6566).
**Team:** Gabriele Guetta, Guy Gedalia.

A Python terminal conversation that plans the user's requested days, meals and servings.
English only. Every run starts with fresh preferences, conversation,
approvals and menu. Earlier planning sessions are never restored.

## Install and run

Python 3.9+ (also compatible with the originally proposed Python 3.12 environment):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

On macOS/Linux use `source .venv/bin/activate` and `cp .env.example .env`.
Real `.env` files are ignored. Never commit credentials or Codex authentication caches.
Type naturally, for example “Plan Monday lunch and dinner for two; dinner must be quick”.
Type `exit`, or send EOF, to leave.

## Choose a model provider

Set `PLANNER_PROVIDER=auto|openrouter|codex`, or override with
`python main.py --provider codex`. The provider and authentication mode appear at startup.

- **auto:** select OpenRouter if `OPENROUTER_API_KEY` is nonempty; otherwise try local Codex.
- **openrouter:** set `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` in `.env` or the environment.
  Choose an available model supporting JSON Schema structured outputs; no model is hardcoded.
- **codex:** install the official Codex CLI, run `codex login` outside the planner, and confirm
  with `codex login status`. ChatGPT sign-in uses the account's applicable limits. API-key sign-in
  uses usage-based billing and is identified separately. The CLI reuses credentials; this
  application never reads or copies tokens. Optional `CODEX_MODEL` overrides model selection.

`PLANNER_TIMEOUT` defaults to 120 seconds. Provider failures report authentication, usage/credit
limits, connection or malformed-response problems without silently switching providers.
Missing setup exits with a short instruction, never canned meal-planning responses.

The Codex adapter targets CLI 0.169.0 or a compatible version supporting the inspected flags.
Each completion uses a fresh temporary directory, stdin, JSONL and an output schema, read-only
sandbox, `--ephemeral` and `--ignore-user-config`. Shell tools, hooks, apps, plugins, web search,
memories and delegation are disabled. Runtime instructions replace coding instructions. No
repository files, implementation prompt, saved thread IDs or earlier planning transcripts are
passed to it. Older CLIs lacking the isolation flags need an upgrade. Managed CLI policy still
applies; conflicts are reported as provider failures. Non-default authentication-store settings
may need supported managed CLI configuration because user configuration is ignored; never copy
authentication files into this project.

References: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Codex authentication](https://learn.chatgpt.com/docs/auth),
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-basic),
[OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs).

## Collaboration and evidence

The agent progressively collects the framework, explicit allergy/restriction answers, favorite
cuisines and dishes, cooking time, optional calorie targets, budget units and desired variety.
It summarizes the answers for confirmation before proposing food. Multiple days begin with one
complete requested day. After feedback and approval, the user chooses day-by-day planning or
reviewing the remaining days together. A single meal needs no continuation question.

Python owns stable meal IDs, scoped preferences, draft versions, actual user-turn evidence,
pending decisions and approvals. Contextual replies such as “yes” work for an unambiguous pending
decision; stale revisions and invented approval flags cannot change state. General preference
changes offer review of earlier meals without rewriting them. Agreeing to revisit leads to
a list and a separate replacement-choice/delegation decision. Delegation covers only that list
under those preferences. Unaffected approvals remain valid. Corrections are possible throughout.

User-approved and technically verified are separate. Checks over structured evidence produce
passes/fails/cannot verify; missing data is never zero. Daily targets need an agreed planned
portion. Household budgets are not silently divided by diners or meals. Local targets override
corresponding global targets. Approximate goals display observed values without inventing a
tolerance. Preparation times only verify their evidenced serving quantity. Generic recipes
cannot establish package allergy safety or absence of cross-contamination. This is general
meal planning, not medical coaching.

## Food tools and data

`tools.build_tools()` registers `FoodTools` from `food_tools.py`. Python calls it during drafting:
it searches a recipe, scales it to the slot's diners, then estimates nutrition and cost. The LLM
never calls these tools or supplies their numbers.

| Tool | Source | Returns |
| --- | --- | --- |
| `search_recipes` | [TheMealDB](https://www.themealdb.com/api.php) (free, no key, 10 s timeout) | Up to 5 recipes with ingredients, parsed quantities, instructions and a source link |
| `get_nutrition` | `data/nutrition.csv` (~145 ingredients, per 100 g raw, USDA-based) | kcal and protein per ingredient and in total |
| `get_prices` | `data/prices.csv` (same ingredients, ₪/kg) | Consumption cost per ingredient and in total |

How the data is mapped:

- **Search:** first by exact dish name. Otherwise each meaningful word is searched and results
  are ranked by whole-word overlap (a word in the recipe name counts most, in its ingredients
  less, cooking words like "grilled" least). A recipe needs at least one real match, so
  "Grilled chicken" never returns "Grilled eggplant". Last resort: the dish as a main
  ingredient. Candidates that clash with known allergies, exclusions, dislikes or dietary needs
  are ranked last.
- **Ingredient names** match by name, alias and plural ("Red Onions" → Onion, "Plain Flour" →
  Flour). Descriptor words are dropped only as a fallback. There is no fuzzy guessing, so
  "Chicken Stock" never becomes chicken.
- **Measures** such as "1 1/2 cups", "2-3 tbsp", "800g", "2 cloves" and "juice of 1" are parsed into
  quantity and unit. The original measure is kept, and the conversion to grams is flagged as an
  estimate. Counted items ("2 onions") use a per-item weight from the table.
- **Allergens and diet tags** (vegetarian, vegan, pescatarian, dairy-free, gluten-free, nut-free) come
  from table tags. If any ingredient is unknown, they are marked incomplete, so checks report
  *cannot verify* instead of a false pass.
- **Missing data stays missing.** An unknown ingredient or quantity makes the meal's total `null`
  with status `missing_data`, never a partial sum presented as complete.

Known limits:

- **Servings are assumed.** TheMealDB gives no servings, so every recipe is treated as 4 servings
  before scaling.
- **Rough gram conversions.** One cup is taken as 240 g for every food.
- **No cooking times.** TheMealDB has none, so time targets show *cannot verify*.
- **Prices are estimates,** not live supermarket quotes. They are always shown as estimates.
- **Partial coverage.** About 58% (359 of 623) of TheMealDB's non-dessert recipes get complete nutrition.
  The rest show *unavailable* until missing ingredients are added to the CSVs. To add one, put a
  row (with aliases) in `nutrition.csv` and a matching row in `prices.csv`.

See [Gabriele's handoff](docs/GABRIELE_HANDOFF.md) for the contract and
[TESTING.md](TESTING.md) for the end-to-end personas and bug log.

## Development and tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Default tests require no keys, live model calls or unfinished integrations. Small fixtures live
only in `tests/`: tool results, model responses, HTTP transport and subprocess/auth status. They
exercise business rules, actual agent/state/tool/output collaboration, provider selection,
Codex parsing/isolation, failure handling and CLI startup/exit. These are internal tests, not
real end-to-end persona evaluation. Exact executed commands and counts are recorded in
[validation results](docs/VALIDATION.md).

## Code map and ownership

| File | Responsibility |
| --- | --- |
| `main.py` | CLI, configuration, fresh run, clean exit |
| `agent.py` | Runtime instructions and bounded single-agent loop |
| `providers.py` | OpenRouter and isolated Codex structured-response adapters |
| `models.py` | Validated in-memory state and evidence contracts |
| `state.py` | Transactional actions, approvals and replacement authorization |
| `tools.py` | Three interfaces, disconnected defaults, scaling and registration |
| `food_tools.py` | TheMealDB search, measure parsing, nutrition and price estimates |
| `data/` | `nutrition.csv` and `prices.csv` ingredient tables |
| `checks.py` | Deterministic constraints, scope overrides and completeness |
| `rendering.py` | Menu, recipes and consolidated shopping list from state |
| `tests/` | Credential-free unit and internal integration tests |

Guy implements the CLI, model access, collaboration, state, rules, outputs and internal tests.
Gabriele implements **and connects** real recipe/nutrition/price tools and owns real end-to-end
tests, including the vegetarian on a tight budget, nut-allergy user and high-protein runner.
No database, saved preference profile, web UI or multi-agent system is used.

## Course evaluation plan

Compare the agent with a plain chat baseline in user sessions, measuring plan satisfaction,
sense of control, time to a finished plan and adherence to the collaboration workflow.
End-to-end persona runs with the connected tools are tracked in [TESTING.md](TESTING.md);
the user study remains future work.
