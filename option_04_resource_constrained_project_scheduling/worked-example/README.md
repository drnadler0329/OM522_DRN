# Worked example for option 4

Scheduling project activities with limited resources.

This example applies the option's rule to beat (defined in option-4-brief.pdf) to ../hand_checkable/. It shows one feasible decision and how every measure except computation time is recomputed from that decision. It is not an optimal answer. The team's better method, whether the baseline or the researched method, must outperform this rule by more than 0.001 on the instructor's held-out case. solution.json has the structure that a team's decision.json must use. metrics.json is a reference for testing, not a required output, and its key names are not graded (e.g., makespan is the final or project completion time, and on_time_rate is the on-time fraction). Counts such as jobs_completed are consistency checks, not additional measures. The JSON files keep full numeric precision, and the tables below round to three decimals. All times are in days.

The rule first computes Critical Path Method latest start times while ignoring crews and equipment, working backward from the CPM project duration, i.e., the largest earliest completion. It then repeatedly picks, among activities whose predecessors are all scheduled, the one with the smallest latest start (ties by activity identifier) and places it at the earliest time when its predecessors are finished and every resource stays within capacity for its whole duration.

The rule schedules the activities in this order: A002 (latest start 0, placed at day 0), A003 (latest start 4, placed at day 4), A004 (latest start 4, placed at day 4), A005 (latest start 5, placed at day 10), A001 (latest start 6, placed at day 0), A006 (latest start 7, placed at day 12). An activity can be placed earlier than activities scheduled before it, because the rule searches every time from its predecessors' completions onward.

## Decisions

| Activity id | Start | Completion |
| --- | --- | --- |
| A001 | 0 | 4 |
| A002 | 0 | 4 |
| A003 | 4 | 10 |
| A004 | 4 | 10 |
| A005 | 10 | 12 |
| A006 | 12 | 15 |

## Recomputed measures

| Measure | Value |
| --- | ---: |
| Final completion time | 15 |
| Activities completed | 6 |
| Weighted milestone delay | 0 |
| Late milestones | 0 |
| CPM duration (lower bound) | 10 |
| Gap between project completion and CPM duration | 5 |
