# AlphaFlow Early-Signal Integration Implementation Plan

> For Hermes: use subagent-driven-development skill to implement this plan task-by-task.

Goal: Integrate AlphaFlow's existing early-signal detector into the main /api/v1/diagnose/full output so Hermes can consume one unified payload for monitoring, explanation, and push decisions.

Architecture: Keep the current EmergingMainlineDetector as the single source of truth for early-stage trend detection, but promote its output from a standalone endpoint to a first-class field in the full-diagnose aggregation API. Add lightweight decision-support metadata (action plan, confidence, push hints, dedupe keys) without coupling AlphaFlow too tightly to Hermes delivery logic.

Tech Stack: FastAPI, Pydantic, pytest, existing AlphaFlow service/repository structure.

---

## Scope and non-goals

In scope:
- Add emerging early-signal results to /api/v1/diagnose/full.
- Enrich emerging result items with execution-friendly fields.
- Add push-decision hints that Hermes can consume directly.
- Add tests for detector logic and full diagnose aggregation.
- Document the new response contract and rollout order.

Out of scope for the first implementation:
- Real-time external news/catalyst API integration.
- Full Hermes cron rewrite inside this repo.
- Persistent dedupe state inside AlphaFlow beyond deterministic dedupe keys.
- Strategy backtesting or threshold auto-tuning.

---

## Current code baseline

Relevant files already present:
- `app/services/emerging_detector.py`
- `app/api/routes.py`
- `app/models/api.py`
- `app/repositories/polling_policy_repository.py`
- `tests/test_services.py`
- `tests/test_api.py`
- `README.md`
- `HERMES_GUIDE.md`

Current behavior:
- `/api/v1/mainlines/emerging` can detect warming/early_watch/confirmed_hot opportunities.
- `/api/v1/diagnose/full` records snapshots but does not expose emerging opportunities.
- Polling policy already mentions push gates like `emerging suggestion is probe`.

Primary gap:
- Hermes' main monitoring path uses `/api/v1/diagnose/full`, but that response does not yet include the early-signal payload or push hints.

---

## Design decisions

### 1. Single-entry response model
`/api/v1/diagnose/full` becomes the primary AlphaFlow response for Hermes. Hermes should not need to call `/mainlines/emerging` separately during routine monitoring.

### 2. Detector stays authoritative
Do not duplicate early-signal logic in routes or Hermes. Keep scoring and stage classification inside `EmergingMainlineDetector`.

### 3. AlphaFlow provides hints, Hermes decides delivery
AlphaFlow should return:
- structured emerging items
- push priority hints
- dedupe keys

Hermes should still own:
- cooldowns across sessions
- message delivery channel behavior
- user-facing phrasing and escalation policy

### 4. Detect first, record after
In `/api/v1/diagnose/full`, always call `detect()` before `record_snapshot()`. This preserves the previous snapshot as baseline and prevents current-state contamination.

---

## Target API contract

### New fields to add to `FullDiagnoseResponse`

Add these fields to `app/models/api.py`:

1. `emerging_mainlines: list[EmergingMainlineItem]`
2. `polling_policy_hint: PollingPolicyHintResponse`
3. `market_story: MarketStoryResponse`

### Extend `EmergingMainlineItem`

Keep existing fields and add:
- `action_plan: Literal["observe", "probe_small", "hold_and_confirm", "avoid_chase"]`
- `confidence: Literal["low", "medium", "high"]`
- `position_budget_pct: float`
- `catalyst_tags: list[str]`
- `dedupe_key: str`

### New response models

Add to `app/models/api.py`:

```python
class PollingPolicyHintResponse(BaseModel):
    immediate_push_recommended: bool
    summary_push_recommended: bool
    trigger_reasons: list[str] = Field(default_factory=list)
    dedupe_keys: list[str] = Field(default_factory=list)


class MarketStoryResponse(BaseModel):
    headline: str
    strongest_emerging_name: str | None = None
    strongest_emerging_stage: str | None = None
    strongest_emerging_reason: str | None = None
    caution_notes: list[str] = Field(default_factory=list)
```

### Example response shape

```json
{
  "status": "ok",
  "data": {
    "timestamp": "2026-04-20T10:30:00",
    "market_phase": "主升",
    "top_mainline": {"name": "AI算力/CPO共封装", "score": 29.5, "group": "AI算力"},
    "rankings": [...],
    "emerging_mainlines": [
      {
        "name": "AI算力/CPO共封装",
        "stage": "early_watch",
        "suggestion": "probe",
        "action_plan": "probe_small",
        "confidence": "high",
        "position_budget_pct": 0.05,
        "dedupe_key": "2026-04-20:AI算力/CPO共封装:probe",
        "catalyst_tags": ["资金先行", "扩散增强", "持续性尚低"],
        "reasons": ["短周期评分提升 5.00 分。"]
      }
    ],
    "polling_policy_hint": {
      "immediate_push_recommended": true,
      "summary_push_recommended": false,
      "trigger_reasons": ["emerging suggestion is probe", "mainline score changes by at least 2.0"],
      "dedupe_keys": ["2026-04-20:AI算力/CPO共封装:probe"]
    },
    "market_story": {
      "headline": "AI算力分支出现升温信号，可小仓试探，但不宜把确认信号当成早期潜伏信号追高。",
      "strongest_emerging_name": "AI算力/CPO共封装",
      "strongest_emerging_stage": "early_watch",
      "strongest_emerging_reason": "短周期评分提升且强势持续性尚未过热。",
      "caution_notes": ["避免对 confirmed_hot 分支继续追高"]
    }
  }
}
```

---

## Scoring and action rules

Keep current detector thresholds unless explicitly changed below.

### Stage and suggestion rules
Retain current stages:
- `warming`
- `early_watch`
- `confirmed_hot`

Retain current suggestions:
- `watch`
- `probe`
- `avoid_chase`

### New action plan mapping

Implement in `app/services/emerging_detector.py`:

- `confirmed_hot` -> `avoid_chase`
- `early_watch` and `suggestion == "probe"` -> `probe_small`
- `warming` and `suggestion == "probe"` -> `probe_small`
- `warming` and `suggestion == "watch"` -> `observe`
- fallback -> `observe`

### New position budget mapping

Use simple deterministic rules for first version:

- `avoid_chase` -> `0.0`
- `probe_small` with `high` confidence -> `0.05`
- `probe_small` with `medium` confidence -> `0.03`
- `observe` -> `0.0`

Optional phase-aware boost for later iteration:
- if `market_phase == "主升"` and item group matches top group, allow max `0.08`
- do not implement this in V1 unless tests demand it

### Confidence mapping

Use deterministic rules:

- `high` when any of:
  - `stage == "early_watch" and early_score >= 75`
  - `score_change is not None and score_change >= 3.0`
- `medium` when `early_score >= 60`
- else `low`

### Catalyst tag mapping

Generate rule-based structural tags in detector:

- `capital_strength_score >= 3.5` -> `资金先行`
- `diffusion_score >= 2.5` -> `扩散增强`
- `persistence_score <= 2.2` -> `持续性尚低`
- `rank_change is not None and rank_change > 0` -> `排名加速`
- `score_change is not None and score_change >= 2.0` -> `评分抬升`
- `avoid_chase` -> `确认区过热`

### Dedupe key format

Use deterministic keys only, no server-side dedupe storage yet:

```text
{trade_date}:{mainline_name}:{suggestion}
```

Example:

```text
2026-04-20:AI算力/CPO共封装:probe
```

Hermes will consume these keys for cooldown and dedupe.

---

## Push-hint logic

Add route-level helper functions in `app/api/routes.py` or a new small service later if the route becomes too large.

### Immediate push conditions
Return `immediate_push_recommended = True` if any emerging item satisfies at least one:
- `suggestion == "probe"`
- `current_rank <= 5 and previous_rank is not None and previous_rank > 5`
- `score_change is not None and score_change >= 2.0`

### Summary push conditions
Return `summary_push_recommended = True` if:
- no immediate push is recommended
- at least one item has `suggestion == "watch"` or `suggestion == "avoid_chase"`

### Trigger reasons
Build from deterministic strings already aligned with polling policy wording:
- `emerging suggestion is probe`
- `emerging suggestion is watch`
- `mainline enters top 5`
- `mainline score changes by at least 2.0`
- `confirmed hot mainline avoid_chase`

---

## Market story generation

Create a tiny formatter in `app/api/routes.py` first; move to a service only if complexity grows.

Rules:
- If there are no emerging items:
  - headline: `当前未发现明显的早期升温主线，仍以已确认主线和风险控制为主。`
- If top emerging item is `probe_small`:
  - headline should mention opportunity + small position + no chasing.
- If top emerging item is `avoid_chase`:
  - headline should emphasize confirmation rather than early-stage entry.
- `strongest_emerging_reason` should be the first reason, if available.
- `caution_notes` should include confirmed-hot risk if any item has `avoid_chase`.

---

## Phased execution plan

### Phase 0: Contract design and tests first
Success criteria:
- New response schema is defined in code.
- Tests fail before implementation.

### Phase 1: Detector enrichment
Success criteria:
- Detector returns action plan, confidence, budget, catalyst tags, dedupe key.
- Existing behavior for stage/suggestion remains intact.

### Phase 2: Full-diagnose integration
Success criteria:
- `/api/v1/diagnose/full` includes `emerging_mainlines`, `polling_policy_hint`, `market_story`.
- Detect happens before record_snapshot.

### Phase 3: Documentation update
Success criteria:
- README and HERMES_GUIDE document the new contract.
- Example payloads reflect the new fields.

### Phase 4: Hermes-side rollout
Success criteria:
- Hermes cron consumes `emerging_mainlines` from `/diagnose/full`.
- Push behavior follows `polling_policy_hint` and `dedupe_keys`.
- This phase is outside this repo but should be tracked immediately after AlphaFlow changes merge.

---

## Bite-sized implementation tasks

### Task 1: Add failing API model tests for new full-diagnose fields

Objective: Lock the desired response contract before changing production code.

Files:
- Modify: `tests/test_api.py`
- Read for context: `app/models/api.py`, `app/api/routes.py`

Step 1: Add assertions to `test_diagnose_full_aggregates_market_context` for:
- `emerging_mainlines`
- `polling_policy_hint`
- `market_story`

Suggested test extension:

```python
assert "emerging_mainlines" in body["data"]
assert "polling_policy_hint" in body["data"]
assert "market_story" in body["data"]
```

Step 2: Monkeypatch `routes.emerging_detector.detect` to return one synthetic early-watch item.

Example:

```python
monkeypatch.setattr(
    routes.emerging_detector,
    "detect",
    lambda scores, lookback_minutes=30, limit=5: [
        {
            "name": "AI算力/CPO共封装",
            "group": "AI算力",
            "stage": "early_watch",
            "suggestion": "probe",
            "action_plan": "probe_small",
            "confidence": "high",
            "position_budget_pct": 0.05,
            "early_score": 78.0,
            "current_rank": 1,
            "previous_rank": 3,
            "rank_change": 2,
            "current_score": 29.0,
            "previous_score": 26.5,
            "score_change": 2.5,
            "avoid_chase": False,
            "catalyst_tags": ["资金先行", "扩散增强"],
            "reasons": ["短周期评分提升 2.50 分。"],
            "leaders": [],
            "dedupe_key": "2026-04-20:AI算力/CPO共封装:probe",
        }
    ],
)
```

Step 3: Run test to verify failure.

Run:
`pytest tests/test_api.py::test_diagnose_full_aggregates_market_context -v`

Expected: FAIL because the new fields do not yet exist.

Step 4: Commit after implementation later, not yet.

---

### Task 2: Extend API response models

Objective: Define the new response schema in Pydantic.

Files:
- Modify: `app/models/api.py`
- Test: `tests/test_api.py`

Step 1: Add `PollingPolicyHintResponse` and `MarketStoryResponse`.

Step 2: Extend `EmergingMainlineItem` with:
- `action_plan`
- `confidence`
- `position_budget_pct`
- `catalyst_tags`
- `dedupe_key`

Step 3: Extend `FullDiagnoseResponse` with:
- `emerging_mainlines`
- `polling_policy_hint`
- `market_story`

Step 4: Run focused tests.

Run:
`pytest tests/test_api.py::test_diagnose_full_aggregates_market_context -v`

Expected: still FAIL until route fills the fields.

Step 5: Commit.

```bash
git add app/models/api.py tests/test_api.py
git commit -m "feat: extend full diagnose response contract for early signals"
```

---

### Task 3: Add failing detector tests for action metadata

Objective: Lock the enriched detector output before changing detector implementation.

Files:
- Modify: `tests/test_services.py`
- Read for context: `app/services/emerging_detector.py`

Step 1: Extend `test_emerging_detector_flags_rank_acceleration` with assertions for:
- `action_plan == "probe_small" or "observe"` depending on threshold
- `confidence`
- `position_budget_pct`
- `catalyst_tags`
- `dedupe_key`

Step 2: Extend `test_emerging_detector_warns_when_theme_is_already_hot` with assertions for:
- `action_plan == "avoid_chase"`
- `position_budget_pct == 0.0`
- `确认区过热` in `catalyst_tags`

Step 3: Run tests to verify failure.

Run:
`pytest tests/test_services.py -k emerging_detector -v`

Expected: FAIL because detector does not return the new keys yet.

---

### Task 4: Enrich `EmergingMainlineDetector.detect()` output

Objective: Keep detector logic centralized while adding execution-friendly metadata.

Files:
- Modify: `app/services/emerging_detector.py`
- Test: `tests/test_services.py`

Step 1: Add helper methods:
- `_action_plan(...)`
- `_confidence(...)`
- `_position_budget_pct(...)`
- `_catalyst_tags(...)`
- `_dedupe_key(...)`

Step 2: Use these helpers when building each item dict.

Step 3: Keep existing stage and suggestion behavior unchanged unless needed to make tests deterministic.

Step 4: Run focused tests.

Run:
`pytest tests/test_services.py -k emerging_detector -v`

Expected: PASS

Step 5: Commit.

```bash
git add app/services/emerging_detector.py tests/test_services.py
git commit -m "feat: enrich emerging detector with action and confidence metadata"
```

---

### Task 5: Add route helper tests for push hints and story generation

Objective: Prevent ad hoc formatting drift in `/diagnose/full`.

Files:
- Modify: `tests/test_api.py`
- Modify: `app/api/routes.py`

Step 1: Add assertions that the synthetic `probe` item yields:
- `immediate_push_recommended is True`
- `summary_push_recommended is False`
- `emerging suggestion is probe` appears in trigger reasons
- dedupe key is echoed in `polling_policy_hint.dedupe_keys`

Step 2: Add assertions for `market_story.headline` and `strongest_emerging_name`.

Step 3: Run focused test to verify failure.

Run:
`pytest tests/test_api.py::test_diagnose_full_aggregates_market_context -v`

Expected: FAIL until route logic is implemented.

---

### Task 6: Integrate emerging detection into `/api/v1/diagnose/full`

Objective: Make full diagnose the single payload Hermes needs.

Files:
- Modify: `app/api/routes.py`
- Test: `tests/test_api.py`

Step 1: In `full_diagnose()`, call:

```python
emerging_items = emerging_detector.detect(scores, lookback_minutes=30, limit=5)
```

before:

```python
emerging_detector.record_snapshot(scores)
```

Step 2: Fill leaders for each emerging item exactly like `/mainlines/emerging` does.

Step 3: Add small helper functions in the same file for V1:
- `_build_polling_policy_hint(emerging_items)`
- `_build_market_story(emerging_items)`

Step 4: Construct `FullDiagnoseResponse` with the new fields.

Step 5: Run focused API tests.

Run:
`pytest tests/test_api.py::test_diagnose_full_aggregates_market_context -v`

Expected: PASS

Step 6: Commit.

```bash
git add app/api/routes.py tests/test_api.py
git commit -m "feat: expose emerging signals in full diagnose response"
```

---

### Task 7: Verify full detector and API suite together

Objective: Catch schema or route mismatches immediately.

Files:
- No new file changes expected
- Test: `tests/test_services.py`, `tests/test_api.py`

Step 1: Run detector and API tests together.

Run:
`pytest tests/test_services.py tests/test_api.py -v`

Expected: PASS

Step 2: If failures appear, fix only the minimal contract mismatch and rerun.

Step 3: Commit.

```bash
git add app/models/api.py app/services/emerging_detector.py app/api/routes.py tests/test_services.py tests/test_api.py
git commit -m "test: verify early-signal contract across detector and API"
```

---

### Task 8: Document the new full-diagnose response in README

Objective: Make the new contract discoverable for maintainers.

Files:
- Modify: `README.md`

Step 1: Update the `/mainlines/emerging` and Hermes sections to state that `/diagnose/full` now includes `emerging_mainlines`.

Step 2: Add a short JSON example showing:
- `emerging_mainlines`
- `polling_policy_hint`
- `market_story`

Step 3: Run no tests; perform manual read-through.

Step 4: Commit.

```bash
git add README.md
git commit -m "docs: document early-signal fields in full diagnose response"
```

---

### Task 9: Update Hermes integration guide

Objective: Make downstream adoption obvious.

Files:
- Modify: `HERMES_GUIDE.md`

Step 1: Update the `/api/v1/diagnose/full` example to show the new fields.

Step 2: Add recommended Hermes consumption rules:
- `probe` -> immediate push
- `watch` -> summary only
- `avoid_chase` -> once per mainline per day
- use `dedupe_keys` for cooldown and duplicate suppression

Step 3: Manual read-through for consistency with README.

Step 4: Commit.

```bash
git add HERMES_GUIDE.md
git commit -m "docs: add Hermes consumption rules for emerging signals"
```

---

### Task 10: Prepare Hermes rollout checklist (tracked, but likely executed outside this repo)

Objective: Ensure AlphaFlow changes immediately translate into better monitoring.

Files:
- Create: `docs/plans/hermes-rollout-checklist.md`

Step 1: Create a short checklist with:
- switch cron consumer to `emerging_mainlines` in `/diagnose/full`
- consume `polling_policy_hint.immediate_push_recommended`
- dedupe by `dedupe_keys`
- keep `/mainlines/emerging` as debug/inspection endpoint only

Step 2: Commit.

```bash
git add docs/plans/hermes-rollout-checklist.md
git commit -m "docs: add Hermes rollout checklist for early-signal integration"
```

---

## Verification checklist

Before marking this plan complete, verify all of the following:

- [ ] `/api/v1/diagnose/full` returns `emerging_mainlines`
- [ ] `/api/v1/diagnose/full` returns `polling_policy_hint`
- [ ] `/api/v1/diagnose/full` returns `market_story`
- [ ] `detect()` still returns `warming`, `early_watch`, and `confirmed_hot`
- [ ] `detect()` now returns `action_plan`, `confidence`, `position_budget_pct`, `catalyst_tags`, and `dedupe_key`
- [ ] `detect()` runs before `record_snapshot()` inside full diagnose
- [ ] `README.md` examples match the actual API response schema
- [ ] `HERMES_GUIDE.md` tells Hermes to consume the unified full-diagnose payload

---

## Rollout order recommendation

Recommended execution order:
1. Task 1-2: lock and define API schema
2. Task 3-4: enrich detector output
3. Task 5-6: integrate into full diagnose
4. Task 7: run tests
5. Task 8-9: update docs
6. Task 10: align Hermes rollout

---

## Future optimization backlog

Do not mix these into the first merge unless required.

### Backlog A: Better catalyst explanation
- Join structural tags with `fetch_catalyst.py`
- Attach policy/event/funding explanations to the top 1-3 emerging items

### Backlog B: Better push ranking
- Prioritize upgrades like `warming -> early_watch`
- Distinguish first top-5 entry from repeated top-5 presence
- Expose `push_priority: high|medium|low`

### Backlog C: Better statefulness
- Add optional AlphaFlow-side memory for repeated avoid-chase notices
- Store last-emitted emerging state in `runtime/data/`

### Backlog D: Better threshold tuning
- Add regression fixtures from historical snapshots
- Tune score thresholds against real trading sessions

---

## Implementation notes for the future implementer

- Prefer minimal edits over introducing new services too early.
- Keep route helpers private until complexity justifies extraction.
- Preserve backwards compatibility for `/mainlines/emerging`; it remains useful for debugging.
- Do not let AlphaFlow own too much delivery policy. Deterministic hints are enough.
- If response-model growth makes `app/models/api.py` unwieldy, split diagnose-specific models into a dedicated module after the first successful merge.
