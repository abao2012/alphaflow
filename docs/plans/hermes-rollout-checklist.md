# Hermes Rollout Checklist for AlphaFlow Early-Signal Integration

Goal: Adopt the new `/api/v1/diagnose/full` early-signal fields as the default Hermes monitoring contract.

## Consumption changes

- [ ] Read `data.emerging_mainlines` from `/api/v1/diagnose/full`
- [ ] Stop depending on `/api/v1/mainlines/emerging` for routine cron polling
- [ ] Keep `/api/v1/mainlines/emerging` only for manual inspection/debugging

## Push decision rules

- [ ] If `data.polling_policy_hint.immediate_push_recommended == true`, send immediate push
- [ ] If `data.polling_policy_hint.summary_push_recommended == true`, queue for summary push
- [ ] Use `data.polling_policy_hint.trigger_reasons` in logs/debug output

## Dedupe rules

- [ ] Deduplicate on every value in `data.polling_policy_hint.dedupe_keys`
- [ ] For `avoid_chase`, allow at most one push per mainline per trading day
- [ ] For repeated `watch`, prefer summary aggregation instead of immediate push

## Message semantics

- [ ] `suggestion == probe` -> immediate early-radar push, recommend small test position only
- [ ] `suggestion == watch` -> summary-only observation note
- [ ] `suggestion == avoid_chase` -> risk warning, do not phrase as entry opportunity
- [ ] Reuse `market_story.headline` as the first sentence in push drafts when available

## Verification

- [ ] Manual test: one synthetic `probe` signal triggers immediate push
- [ ] Manual test: one `watch` signal does not trigger immediate push
- [ ] Manual test: repeated `avoid_chase` on same mainline is suppressed within the same day
- [ ] Manual test: no emerging signals yields no false-positive push
