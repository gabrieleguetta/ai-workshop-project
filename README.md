# Nutrition Planner: a Human–AI Collaborative Meal-Planning Agent

Final project for **Workshop in Developing Human–AI Collaborative Systems** (BIU 89-6566).

> **Team:** Gabriele Guetta, Guy Gedalia

> **Status:** early development

## What is this?

A meal-planning partner that helps a user build a weekly meal plan **together**, instead of just generating one for them. The agent asks about goals and constraints, proposes a plan, and revises it based on the user's feedback. Nothing is finalized until the user approves it.

This is general meal and grocery planning. It is **not** medical or diet-coaching advice.

## Collaboration workflow

Each phase ends with the user approving or redirecting before the agent moves on.

1. **Goals & constraints:** the agent asks about preferences, dietary restrictions, allergies, budget, cooking time, and number of meals.
2. **Draft plan:** the agent proposes a weekly plan, with options where it makes sense.
3. **Review & adjust:** the user swaps meals, changes portions, or adds constraints, and the agent revises.
4. **Shopping list:** the agent produces a grocery list from the approved plan.

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
python main.py
```

_Update this section once the entry point is finalized._

## Project structure

```
.
├── README.md
├── main.py              # entry point / chat loop
├── agent.py             # agent logic, system prompt, workflow phases
├── tools.py             # tools (e.g., nutrition data lookup)
├── requirements.txt
└── .env                 # API keys (not committed)
```

_Adjust to match the actual repo layout._

## Data sources

_To be decided:_ the LLM's built-in nutrition knowledge, and optionally [Open Food Facts](https://world.openfoodfacts.org/data) or [USDA FoodData Central](https://fdc.nal.usda.gov/).

## Milestones

- [ ] Project proposal approved
- [ ] Baseline chat agent
- [ ] Workflow phases implemented
- [ ] Tools / data lookup
- [ ] User study and evaluation
- [ ] Final report and presentation