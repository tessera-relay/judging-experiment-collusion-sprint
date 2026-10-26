# Figure caption facts (facts only; captions are Artemiy's to write)

Shared facts: "episode" = one run of three judges; arms and n per arm: C4 32, channel_only 32, neutral_pause 32, separate_budget 32, all_good 32, no_scarcity 16 (instructed, n = 4, is left out of the figures; its numbers are in Tables 4 and 6). "Seeded pair" = the two judges given the private board in channel-type arms (the same two names are the designated pair in C4, which has no private board). Dots are jittered vertically for readability; the jitter carries no meaning. CIs: 95% percentile bootstrap over episodes, 10,000 resamples, random seed 1. Source: analysis/report/report_data.py (episodes.csv).

## Figure 1: worker activity by turn
- y: outputs handed in at that turn / 6 workers, averaged over the episodes of that arm still running at that turn. Paused and removed workers hand in nothing.
- A point is drawn only while at least 4 episodes of the arm are still running (x up to 25).
- Runs end when the budget is spent; no_scarcity runs end after 10 turns (not told to the judges), and its drop below 1 comes from worker removals (4 of 16 episodes had one).

## Figure 2: pausing, coverage and dead time per episode
- One dot per episode; black diamond = arm mean; bars = 95% bootstrap CI.
- Left: seeded pair's judge replies that began with that judge's own team paused / (2 x turns started).
- Middle, coverage: outputs scored / (6 workers x turns started), i.e. share of the evaluations that would have happened with no pauses or removals.
- Right, dead time: turns in which no worker handed in an output / turns started.
- Episodes with at least one pause (any judge): C4 29/32, channel_only 28/32, neutral_pause 23/32, separate_budget 24/32, all_good 16/32, no_scarcity 0/16.

## Figure 3: hard measure rate
- One dot per episode; diamond and bars as in Figure 2.
- hard = tokens in the seeded pair's replies written while their own team was paused that the GPT scorer (gpt-5.6-sol) coded as going beyond explaining the pause (rules O40-O45). Rate = hard / (2 x turns started), i.e. per pair judge-turn.

## Figure 4: time to first pause
- Share of episodes in which any judge had paused its team by turn t (cumulative), x from 0 to 20. n per arm in the legend. No pause occurred after turn 11 in any arm shown.

## Figure 5: stated vs actual turns left
- One dot per board message containing an explicit estimate such as "~3 turns left", "2-3 turns max" or "1 turn remaining" (regex in analysis/estimates/turns_left.py; vaguer phrases not counted). Ranges are taken at the upper bound.
- x: the stated number; y: turns the run actually lasted after the turn of the message. Dashed line: stated = actual. Small jitter (±0.2) on both axes.
- Estimates: C4 91, channel_only 105, neutral_pause 89, separate_budget 27, all_good 205 (instructed 13, not shown; no_scarcity none).
- The actual figure includes the effect of pauses made after the estimate; turns-left-estimates.md also compares each estimate with a naive projection (budget left / previous turn's spend).

## Figure 6: pre-specified comparisons
- One grey dot per mirror pair (seeds 2k-1 and 2k averaged; 16 pairs, J7 8 pairs); diamond = mean difference A - B; p = two-sided exact paired permutation test, unadjusted (Holm-adjusted p in Table 3).
- Left: hard (total tokens per episode, not a rate) for J1 channel_only vs C4, J2 separate_budget vs channel_only, J6 all_good vs channel_only, J8 neutral_pause vs channel_only.
- Right: seeded pair's paused share for J6, J7 (no_scarcity vs channel_only seeds 1-16) and J8.
- J1 is the primary test; J6-J8 were specified after the primary analysis (O88-O90).
