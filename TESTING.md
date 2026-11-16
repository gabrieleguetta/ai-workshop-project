# Test personas and bug log

Run each persona end to end with `python main.py`: constraints → skeleton → recipe and check per dish → numbers table → one swap → shopping list. Paste the opening message, then answer the agent naturally while staying in character.

## Personas

**1. Vegetarian on a tight budget**
> I'm vegetarian, no meat or fish, eggs and dairy are fine. Budget ₪250 a week, 1800–2200 kcal a day. Weeknights max 30 minutes. Plan 5 days, lunch and dinner.

Check: no meat or fish appears anywhere; weekly cost stays under ₪250; legumes and eggs are reused across dishes.

**2. Nut allergy**
> I have a severe tree-nut and peanut allergy. I eat everything else, I hate mushrooms. ₪400 a week, 2000–2400 kcal. Plan 4 days, dinner only.

Check: no nuts, peanut butter or almond milk get through without a flag (TheMealDB recipes often include nuts, e.g. the walnut sauce in its chickpea dish); no mushrooms; every flag comes with a fix.

**3. High-protein runner**
> I run 50 km a week and want at least 140 g protein a day, 2600–3000 kcal. No pork. ₪500 a week, up to 45 minutes cooking. Plan 3 days, breakfast, lunch and dinner.

Check: daily protein ≥ 140 g in the table; no main protein two days in a row; calories inside the range after a portion change.

## What to watch for in every run

- The agent confirms constraints before planning (hard gate).
- Nothing is added to the menu without the user's choice.
- Every numbers table is labeled as estimates.
- Ingredients the tools don't know (`"found": false`) are retried with a suggested name or clearly estimated, never silently dropped from totals.

## Bug log

| # | Date | Persona | What happened | Expected | Owner | Status |
|---|------|---------|---------------|----------|-------|--------|
| 1 |      |         |               |          |       |        |
