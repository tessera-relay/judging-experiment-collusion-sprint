# neutral_pause vs channel_only: pre-specified test J8 (O90)

Units: mirror pairs (seeds 2k-1, 2k averaged). Two-sided exact paired permutation test; difference = A - B. J8 soft and hard: Holm across the two. hard_rate, paused_share and any_pause are reported alongside, unadjusted (O90). any_pause per pair = share of the pair's two episodes with any pause.

| test | A | B | units | mean A | mean B | A>B / A<B / = | p | Holm p |
|---|---|---|---|---|---|---|---|---|
| J8 soft | neutral_pause | channel_only | 16 | 0.9062 | 0.4375 | 8 / 3 / 5 | 0.2686 | 0.5371 |
| J8 hard | neutral_pause | channel_only | 16 | 1912 | 1886 | 9 / 7 / 0 | 0.9433 | 0.9433 |
| J8 hard_rate (alongside) | neutral_pause | channel_only | 16 | 62.31 | 60.64 | 10 / 6 / 0 | 0.8664 | - |
| J8 paused_share (alongside) | neutral_pause | channel_only | 16 | 0.4096 | 0.4676 | 7 / 9 / 0 | 0.4615 | - |
| J8 any_pause (alongside) | neutral_pause | channel_only | 16 | 0.7188 | 0.875 | 4 / 9 / 3 | 0.2668 | - |

## Descriptive

| arms | episodes | any pause | coordinated pause | soft > 0 | hard > 0 | mean turns | mean hard | mean hard rate | mean paused share |
|---|---|---|---|---|---|---|---|---|---|
| channel_only seeds 1-32 | 32 | 28 | 25 | 6 | 25 | 14.1 | 1886 | 60.6 | 0.468 |
| neutral_pause seeds 1-32 | 32 | 23 | 23 | 9 | 23 | 15.5 | 1912 | 62.3 | 0.41 |
