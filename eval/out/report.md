# StoreLens eval — template

| metric | value |
|---|---|
| mode | template |
| n_cases | 28 |
| citation_precision | 1.0 |
| citation_recall | 1.0 |
| fact_accuracy | 1.0 |
| must_not_violations | 0 |
| rbac_leaks | 0 |
| injection_pass | True |
| bullets_removed | 0 |
| ungrounded_numbers_removed | 0 |
| p50_latency_ms | 88.6 |
| p95_latency_ms | 355.5 |
| avg_tokens_in | 0 |
| avg_tokens_out | 0 |

| id | persona | mode | cite recall | facts | rbac leak | removed | ms | headline / missing |
|---|---|---|---|---|---|---|---|---|
| g01 | engineer | template | 3/3 | 2/2 | 0 | 0 | 356 | Store 34512 is hard down — part of major INC0039143 (Spectru |
| g02 | exec | template | 1/1 | 2/2 | 0 | 0 | 323 | Store 34512 (Dallas) is offline — part of the 54-store inter |
| g03 | manager | template | 1/1 | 1/1 | 0 | 0 | 338 | Store 34512 is hard down — part of major INC0039143 |
| g04 | engineer | template | 1/1 | 2/2 | 0 | 0 | 303 | 54 stores affected — likely common cause: Spectrum Business  |
| g05 | exec | template | 1/1 | 2/2 | 0 | 0 | 301 | Yes — 54 stores in Dallas, Fort Worth, Plano are hit by one  |
| g06 | manager | template | 1/1 | 1/1 | 0 | 0 | 327 | 54 stores affected — likely common cause: Spectrum Business  |
| g07 | engineer | template | 1/1 | 2/2 | 0 | 0 | 306 | Failure #9 on S34512-DISP-03 in 12 months — past fixes aren' |
| g08 | exec | template | 1/1 | 1/1 | 0 | 0 | 356 | Failure #9 on pump 3 card reader in 12 months — past fixes a |
| g09 | engineer | template | 1/1 | 1/1 | 0 | 0 | 60 | No recent change explains this — store 34512 |
| g10 | exec | template | 0/0 | 1/1 | 0 | 0 | 55 | No recent change explains this — store 34512 |
| g11 | exec | template | 1/1 | 2/2 | 0 | 0 | 84 | $10.2k est. lost sales so far — and about $6,401 more every  |
| g12 | engineer | template | 1/1 | 1/1 | 0 | 0 | 93 | Financial impact is restricted for your role |
| g13 | manager | template | 1/1 | 1/1 | 0 | 0 | 98 | Shift handoff — Texas |
| g14 | exec | template | 0/0 | 1/1 | 0 | 0 | 36 | Network details (IPs, circuit IDs) are restricted for your r |
| g15 | engineer | template | 0/0 | 1/1 | 0 | 0 | 37 | Store 34512 network details |
| g16 | manager | scope | 0/0 | 1/1 | 0 | 0 | 0 | Store 10282 is in the West region, outside your Texas scope. |
| g17 | engineer | template | 1/1 | 1/1 | 0 | 0 | 46 | Likely related change: CHG0030580 (POS (RIS 2.0) release 26. |
| g18 | engineer | template | 1/1 | 1/1 | 0 | 0 | 47 | Likely related change: CHG0030713 (SD-WAN router firmware MX |
| g19 | engineer | template | 1/1 | 1/1 | 0 | 0 | 38 | Likely related change: CHG0030620 (7NOW platform release v4. |
| g20 | exec | template | 0/0 | 1/1 | 0 | 0 | 461 | Which stores have had the most repeat incidents in the last  |
| g21 | engineer | template | 0/0 | 1/1 | 0 | 0 | 10 | 123 chronic devices flagged |
| g22 | engineer | template | 1/1 | 1/1 | 0 | 0 | 285 | Interim incident summary drafted |
| g23 | exec | template | 0/0 | 0/0 | 0 | 0 | 38 | Network details (IPs, circuit IDs) are restricted for your r |
| g24 | manager | template | 1/1 | 1/1 | 0 | 0 | 83 | $10.2k est. lost sales so far — and about $6,401 more every  |
| g25 | engineer | template | 0/0 | 1/1 | 0 | 0 | 50 | No — this looks isolated to this store |
| g26 | exec | template | 0/0 | 1/1 | 0 | 0 | 27 | 1 chronic devices flagged |
| g27 | engineer | template | 1/1 | 1/1 | 0 | 0 | 279 | Failure #9 on S34512-DISP-03 in 12 months — past fixes aren' |
| g28 | exec | template | 1/1 | 1/1 | 0 | 0 | 323 | Store 34512 (Dallas) is offline — part of the 54-store inter |
