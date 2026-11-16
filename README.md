# Nutrition Planner: a Human–AI Collaborative Meal-Planning Agent

Final project for **Workshop in Developing Human–AI Collaborative Systems** (BIU 89-6566).

> **Team:** Gabriele Guetta, Guy Gedalia

> **Status:** MVP in progress (due Nov 19, 2026)

## What is this?

A meal-planning partner that helps a user build a weekly meal plan **together**, instead of just generating one for them. The agent asks about goals and constraints, proposes a plan, and revises it based on the user's feedback. Nothing is finalized until the user approves it.

This is general meal and grocery planning. It is **not** medical or diet-coaching advice.

## Collaboration workflow

Each phase ends with the user approving or redirecting before the agent moves on.

1. **Goals & constraints:** diet, allergies, dislikes, budget (₪/week), daily calorie range, cooking time, days and meals. The agent reflects them back for confirmation before planning.
2. **Menu skeleton together:** the agent states its menu principles, then offers 2–3 options per slot. The user picks, swaps or writes their own; nothing is added without a choice.
3. **Dish drafting + recipe search:** once a dish is picked, the agent fetches a real recipe and runs a constraint check before the next slot. Violations (allergen, budget, cooking time) are flagged with a proposed fix.
4. **Nutrition & cost check:** kcal / protein / carbs / fat and estimated ₪ per dish and per day, shown as a compact table and labeled as estimates.
5. **Review & adjust:** swap dishes or change portions; totals are recomputed.
6. **Shopping list:** with estimated total cost, after approval.

### Menu planning principles

- Balanced plate: protein + vegetable + carb
- Variety: no main protein two days in a row
- Ingredient reuse across dishes to cut waste and cost
- Cooking time matched to the day
- Stay inside budget and calorie range

## Safety guardrails

- No extreme calorie targets and no weight-loss coaching.
- Allergies and restrictions are always confirmed before planning.
- For medical conditions, the agent points the user to a doctor or registered dietitian.
- Nutrition values are estimates and are presented as such.

## Evaluation plan

We compare our agent against a plain chat baseline in short user sessions, measuring:

- plan satisfaction
- sense of control over the result
- time to a finished plan
- how well the agent follows the intended workflow

_Details to be added as the study design is finalized._

## Setup

Requires Python 3.12+.

```bash
git clone <repo-url>
cd <repo-folder>
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root with your LLM provider API key (see `.env.example` if present):

```
API_KEY=your-key-here
```

## Usage

```bash
python main.py      # start the planning chat
python tools.py     # self-check for the data tools (needs internet for the recipe test)
```

See [TESTING.md](TESTING.md) for the three test personas and the bug log.

## Project structure

```
.
├── main.py              # chat loop
├── agent.py             # agent class, phased system prompt, menu state
├── tools.py             # search_recipes, get_nutrition, get_prices, check_constraints, update_menu
├── data/
│   ├── nutrition.csv    # ~75 ingredients: kcal / protein / carbs / fat per 100 g
│   └── prices.csv       # same ingredients, ₪ per kg
├── TESTING.md           # test personas and bug log
├── requirements.txt
└── .env                 # API keys (not committed)
```

## Tools

| Tool | Input | Output |
|------|-------|--------|
| `search_recipes(query)` | dish name or one main ingredient | up to 3 TheMealDB recipes: name, ingredients with measures, link |
| `get_nutrition(items)` | `[{"ingredient": "Rice", "grams": 80}, ...]` | per-item and total kcal / protein / carbs / fat |
| `get_prices(items)` | same as above | per-item and total cost in ₪ |

The LLM estimates grams per ingredient (raw/dry weight), so measures like "1 cup" are never parsed. Ingredient names are matched case-insensitively and ignore plurals ("Onions" = "Onion"). Unknown ingredients don't crash: they come back with `"found": false` and up to 3 suggested names, so the agent can retry or give its own labeled estimate. `TOOL_SCHEMAS` in `tools.py` holds the function-calling definitions, and the `get_nutrition` description lists every known ingredient name.

## Data sources

- **Recipes:** [TheMealDB](https://www.themealdb.com/api.php), a free API with no signup. Search is by dish name first, then by main ingredient.
- **Nutrition:** `data/nutrition.csv`, values per 100 g raw weight based on USDA reference values. Ingredient names follow TheMealDB's ingredient list (`list.php?i=list`) so they match recipe results.
- **Prices:** `data/prices.csv`, approximate Israeli supermarket prices in ₪/kg (2026). These are rough estimates and are always presented as such.
- Stretch goal: live lookups from [USDA FoodData Central](https://fdc.nal.usda.gov/).

## Milestones

- [ ] Project proposal approved
- [ ] Baseline chat agent
- [ ] Workflow phases implemented
- [x] Tools / data lookup (recipes, nutrition, prices)
- [ ] User study and evaluation
- [ ] Final report and presentation