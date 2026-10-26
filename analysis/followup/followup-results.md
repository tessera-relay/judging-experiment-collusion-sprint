# Follow-up arms: pre-specified tests (O88, O89)

Units: mirror pairs (seeds 2k-1, 2k averaged). Two-sided exact paired permutation test; difference = A - B. J6 soft and hard: Holm across the two (O88). J6 rates are reported alongside (Artemiy, 18:28 UTC), unadjusted. J7: unadjusted p; no correction was specified in O89.

| test | A | B | units | mean A | mean B | A>B / A<B / = | p | Holm p |
|---|---|---|---|---|---|---|---|---|
| J6 soft | all_good | channel_only | 16 | 0 | 0.4375 | 0 / 4 / 12 | 0.125 | 0.125 |
| J6 hard | all_good | channel_only | 16 | 968.2 | 1886 | 3 / 13 / 0 | 0.01172 | 0.02344 |
| J6 hard_rate (alongside) | all_good | channel_only | 16 | 27.54 | 60.64 | 2 / 14 / 0 | 0.001587 | - |
| J6 paused_share (alongside) | all_good | channel_only | 16 | 0.2018 | 0.4676 | 3 / 13 / 0 | 0.002808 | - |
| J7 soft | no_scarcity | channel_only | 8 | 0.125 | 0.5625 | 0 / 2 / 6 | 0.5 | - |
| J7 hard_rate | no_scarcity | channel_only | 8 | 0 | 67.68 | 0 / 8 / 0 | 0.007812 | - |
| J7 paused_share | no_scarcity | channel_only | 8 | 0 | 0.4684 | 0 / 8 / 0 | 0.007812 | - |

## Descriptive

| arms | episodes | any pause | coordinated pause | soft > 0 | hard > 0 | mean turns | mean hard | mean hard rate | mean paused share |
|---|---|---|---|---|---|---|---|---|---|
| channel_only seeds 1-32 | 32 | 28 | 25 | 6 | 25 | 14.1 | 1886 | 60.6 | 0.468 |
| all_good seeds 1-32 | 32 | 16 | 16 | 0 | 16 | 13.8 | 968.2 | 27.5 | 0.202 |
| channel_only seeds 1-16 | 16 | 15 | 13 | 3 | 13 | 13.6 | 2068 | 67.7 | 0.468 |
| no_scarcity seeds 1-16 | 16 | 0 | 0 | 1 | 0 | 10 | 0 | 0 | 0 |
