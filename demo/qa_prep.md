# StoreLens — judge Q&A bank (SPEC §16)

1. **Why not just a ServiceNow report or dashboard?** Reports answer known questions; ops questions vary. StoreLens adds cross-ticket synthesis (parent/child stitching, blast radius), "which fix holds" recurrence and role tailoring — with a deterministic analytics layer underneath that any report could also call.
2. **How do you prevent hallucinations?** Tool-only answers; the LLM never does arithmetic; temperature 0; a validator removes any bullet citing an unknown INC/CHG/PRB/KB or containing a number that isn't in a tool result (counts shown in the trace drawer). Eval: citation precision 1.0, RBAC leaks 0 (`eval/out/summary.json`).
3. **What if the LLM is down?** Every panel is deterministic and never calls the LLM. Chat falls back live → replay cache → template mode automatically; the whole demo passes offline (preflight tests it).
4. **How do you know the $ numbers?** Hourly sales baseline × service-loss profile, integrated per store-local hour, capped at 72h; majors deduplicated per store. Gross sales, not margin; the value model applies a 50% recapture factor. Matches the generator's reference column within 1% on 300 random incidents (tested).
5. **Is this real data?** No — mock data only, by rule; RFC1918 mock IPs. The schema mirrors ServiceNow tables (incident, sys_journal_field, change_request, problem, kb_knowledge, cmdb_ci).
6. **How does it get the data in production?** Read-only ServiceNow Table API sync every 5 minutes into the same schema, with a service account; nothing written back.
7. **Security / who sees what?** RBAC in the data layer: network fields and IPs/circuit IDs masked (also regex-redacted in free text), $ nulled for roles without financial access, region scope for managers — before the model or UI sees anything. Audit log of every question. We showed the VP view.
8. **Cost to run?** Per-answer tokens × volume × the approved endpoint's price — see `/api/value-model` (`llm_monthly_cost`). Deterministic panels cost ~0.
9. **How is "likely cause" decided?** Poisson anomaly test vs the same weekday/hour over 8 weeks; then attribute lift (support ≥ 60%, lift ≥ 1.5×) against stores in the same markets. Labeled "likely", not "confirmed".
10. **Change correlation is just timing, isn't it?** No. Staggered rollouts are a natural experiment: changed vs not-yet-changed stores, risk ratio with 95% CI and diff-in-diff; enterprise changes get a fleet z-score. It also produces negative evidence ("no change touched this store").
11. **Adoption?** The store number is the interface; Teams card delivery; zero training.
12. **What did users say?** See slide 2 — time-trial numbers and quotes from `config/validation.yaml` (fill before the demo; `[TBD]` until then).
13. **Why should this beat ServiceNow's own AI features?** Complementary: it joins store context (sales, services, network) and cross-system evidence ServiceNow alone doesn't hold; it could be embedded in ServiceNow. Confirm what's licensed internally before claiming more.
14. **What breaks at 13k stores?** Nothing algorithmic: DuckDB/pandas and TF-IDF handle millions of rows; the index updates incrementally.
15. **What's next after the pilot?** Proactive alerts (blast radius flags clusters ~12 min after the first child ticket vs ~30 min to declare a major in the mock data), auto-suggested parent links, a chronic-device replacement program.

**Honest caveat if asked about "which fix holds":** in this mock data, fleet-wide fix types recur at similar rates (4–5%); StoreLens says so instead of inventing a winner, and the device's own history (4 of 8 repairs failed again within 30 days on pump 3) plus the known-error workaround drive the "replace the reader" recommendation.
