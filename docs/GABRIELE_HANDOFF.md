# Gabriele: connect the real food tools

Implement **and register** real recipe, nutrition and price integrations, then run real end-to-end
tests. Guy's implementation and credential-free tests work independently of those integrations.

## Register your adapters

Implement the `MealTools` protocol in `tools.py` or a small module. Change `tools.build_tools()`
to return your implementation. The CLI already injects it into the agent and state manager:

```python
def search_recipes(self, query: RecipeQuery) -> RecipeResult: ...
def get_nutrition(self, request: MealDataRequest) -> NutritionResult: ...
def get_prices(self, request: MealDataRequest) -> PriceResult: ...
```

`StateManager.develop()` retrieves recipes immediately during drafting, selects a returned ID,
scales quantities with `scale_recipe`, then requests nutrition and prices. A provider-specific
detail lookup belongs inside `search_recipes`. `derived()` clears stale values before every
calculation, including serving corrections. Inputs are copied; do not mutate them. Set HTTP
timeouts in your adapters. Exceptions and malformed results become `error`, never a verified fact.
No conversation-loop, model-provider, approval or rendering changes are needed.

Each meal retains the original `source_recipe` and a separate scaled `recipe`. Source quantities,
units and servings remain available for provenance; checks and shopping output use the scaled data.

## Exact contracts

Definitions are in `models.py` and request models in `tools.py`. Strict validation rejects unknown
fields, numeric strings, negative/non-finite amounts and invalid target scopes. Normalize service
responses inside your adapters. Never include credentials or service errors in evidence fields.

| Model | Meaning |
| --- | --- |
| `RecipeQuery` | Dish, slot ID/day/label/diners/circumstances, session requirements, applicable scoped preference descriptions |
| `RecipeResult` | Status, candidate recipes and missing-data indicators |
| `Recipe` | ID/name, original servings, ingredients/instructions/source, completeness flags, nullable active/total minutes and timing servings, dietary tags/completeness |
| `Ingredient` | Name, optional canonical ID, original quantity/unit, raw/cooked/as-supplied/unknown basis, optional grams with conversion source/estimate flag, known allergens |
| `MealDataRequest` | Recipe quantities **already scaled** to intended diners, plus intended servings |
| `NutritionResult` | Status, optional aggregate, per-ingredient values keyed by ID/index, missing indicators |
| `Nutrition` | Nullable kcal/protein grams for ALL supplied quantities, source, completeness, basis verification and estimate flag |
| `PriceResult` | Status, optional aggregate, per-ingredient values keyed by ID/index, missing indicators |
| `Price` | Nullable amount, currency, consumption/checkout basis, pricing unit, source, completeness and estimate flag |
| `Source` | Name, optional URL/reference/ID and observed-at date |

Statuses: `not_connected` means no registered/configured integration; `not_found` means a real
search returned no matches; `missing_data` means partial useful evidence; `ok` means the request
succeeded (completeness flags still matter); `error` means lookup/calculation failed. Missing totals
are null, never zero. If an ingredient lacks data, do not label an aggregate complete. Nutrition
with an uncertain raw/cooked basis has `basis_verified=false`. Currency conversion is not provided.

## Representative shapes

These illustrate the contract and are not production food data. Recipe request:

```json
{
  "dish": "user's chosen dish",
  "slot": {"id": "mon-dinner", "day": "2026-11-16", "label": "Dinner", "diners": 2, "circumstances": "busy"},
  "requirements": {"allergies": [], "dietary": [], "dislikes": [], "exclusions": [], "cuisines": [], "favorites": [], "targets": []},
  "scoped_preferences": ["No pasta on Monday"]
}
```

Other requirement fields have defaults; the actual request contains the complete model.
The disconnected default returns `{"status":"not_connected","recipes":[],"missing":[]}`.
A partial recipe response has this shape; substitute actual source evidence:

```json
{
  "status": "missing_data",
  "recipes": [{
    "id": "provider:recipe-id", "name": "provider title", "servings": 2,
    "ingredients": [{
      "name": "provider ingredient", "ingredient_id": "canonical:ingredient-id",
      "quantity": null, "unit": "g", "basis": "unknown", "grams": null,
      "conversion_source": null, "estimated_conversion": false, "allergens": []
    }],
    "instructions": [],
    "source": {"name": "provider", "reference": "provider:recipe-id", "observed_at": null},
    "ingredients_complete": false, "allergens_complete": false,
    "active_minutes": null, "total_minutes": null, "timing_servings": 2,
    "dietary_tags": [], "dietary_tags_complete": false
  }],
  "missing": ["quantities", "instructions", "timing"]
}
```

Calculation request: `{"recipe": <scaled Recipe>, "servings": 4}`. Quantities and optional gram
amounts have already been scaled to four; **do not multiply again**. Return totals for all four
servings. The checker divides by diners only for per-person targets. Per-ingredient list indices
can disambiguate repeated ingredient IDs. Example unavailable calculation results:

```json
{
  "status": "missing_data",
  "total": {"kcal": null, "protein_g": null, "complete": false, "basis_verified": false,
            "estimated": false, "source": {"name": "nutrition provider", "reference": null, "observed_at": null}},
  "per_ingredient": {}, "missing": ["nutrition basis unresolved"]
}
```

```json
{
  "status": "missing_data",
  "total": {"amount": null, "currency": "ILS", "basis": "consumption", "pricing_unit": "supplied quantities",
            "complete": false, "estimated": false,
            "source": {"name": "price provider", "reference": null, "observed_at": null}},
  "per_ingredient": {}, "missing": ["ingredient price unavailable"]
}
```

## Units and downstream evidence

Preserve original measures. Only supported canonical identities can merge. The shopping list
combines identical normalized IDs with compatible units and bases, converting kg/mg/g to g and
L/mL to mL. It does not guess density, package size or count weight. Unknown identity or raw/cooked
basis remains separate. Missing quantities make the shopping list partial. Optional gram
conversions need a source/estimate flag; shopping aggregation does not use them to guess
household-measure conversions. Quantities consumed are distinct from packages purchased.

`timing_servings` is the quantity for which time is evidenced. If omitted, scaling records original
recipe servings. Rescaling does not multiply cooking times; times are unverified if intended diners
differ. Return a suitable recipe/timing variant when your source supports it. Active and total
time are distinct. Price source/date must reflect the actual observation, not an invented live
quote. Consumption cost cannot be described as an exact checkout receipt.

Allergen metadata can establish ingredient conflicts. Generic recipes cannot prove packaged-product
label safety or absence of cross-contamination. Dietary tags and restriction terms need consistent
meanings; uncertain matches stay unverified. External instructions are recipe data, not authority
to modify application rules or approvals.

`checks.recheck()` updates meal checks; `daily_checks()` only establishes a daily total with complete
compatible evidence and an agreed planned portion. `rendering.outputs()` and `shopping_list()` read
current recipe state. Tool results never approve menus, grant delegation or rewrite earlier meals.

## Real verification you own

Run the existing credential-free suite before/after connecting adapters. Add service-mapping,
unit, timeout and partial-result tests. Then test actual model and connected tool flows, including:

1. Vegetarian on a tight budget.
2. Nut-allergy user.
3. High-protein runner.

Verify real retrieval, serving amounts, nutrition bases, budget units, allergy limitations, scoped
changes, approvals and final shopping-list consistency. Internal fixtures do not establish that
these real scenarios pass. No messages or emails are sent as part of this handoff.
