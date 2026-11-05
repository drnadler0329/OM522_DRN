# OM522_DRN
Repository for the OM522 Project
The decision owner is the project manager responsible for scheduling activities on a project with limited resources. Each activity has a duration, crew/equipment requirements, and predecessors if applicable. Based on this information, the tool recommends a complete activity schedule so the manager can coordinate work, communicate milestone expectations, and allocate the available resources without scheduling more work than those resources can support.

For construction projects in the bid process, the preconstruction team creates an estimate for cost and duration of the project, which is evaluated by the owner. This tool can be run prior to groundbreaking when the preconstruction team has an idea of all of the activities required for job completion, and it can give a rough schedule for the entire project duration.

Larger activities, such as a pouring a concrete slab, can be broken down into smaller activities like layout, grading, forming, ironwork, and finishing. If there are multiple slabs needing to be poured and a crew for each of the sub-activities, the project manager can run the tool each week to determine which order the carpenters should work on forming for the pours or when the ironworkers should tie the rebar.

Activities on a construction project are always changing, so it can also be rerun whenever a new activity comes up or if the expected crew requirement changes.

The project manager can compare the baseline critical path method schedule with the simulated annealing schedule, and selects a feasible plan with the earliest completion. When completion times tie, weighted milestone delay and then the number of late milestones distinguish the plans. The manager also reviews milestone delays before authorizing the chosen plan.

Constraints every recommended schedule must satisfy
1. Every input activity is scheduled exactly once.
2. Each activity runs without interruption for its full input duration.
3. An activity starts only after all of its predecessors have completed.
4. The combined requirement of running activities never exceeds the available capacity of any resource, including crew and equipment.
5. Deterministic start times are nonnegative whole working days. A resource released by an activity finishing at a time can be reused by an activity starting at that same time.

Milestone due dates are targets, but not determinants of feasibility. Missing one increases the reported delay but does not by itself make a schedule infeasible. Locked starts, purchasing extra resources, alternative ways to perform an activity, and detailed staffing are not accounted for in the model.

The primary measure is project completion time, which is the largest completion time among all activities. The main secondary measure is weighted milestone delay. For each activity with a milestone due date, its days late is the maximum of zero and completion minus due date. That delay is multiplied by the activity's priority weight and summed across milestones. The weights are priority scores per day of delay, not monetary costs. The number of late milestones provides a second view of missed commitments.

These secondary measures reveal a practical tradeoff: assigning scarce resources to shorten the overall project may delay an important intermediate milestone. Assigning resources to a smaller predecessor before the final activity in an important milestone may reduce the total project duration, but it could also cause that milestone to finish late.

The reports also include the CPM duration, which is a lower bound calculated from durations and dependencies while ignoring resource limits. A resource- feasible schedule may take longer than CPM predicts. Scheduling computation time is reported separately from the independent checker's validation time.

Assumptions: Activity durations are fixed, Precedence relationships are fixed, Resource requirements are fixed, Resource capacities are fixed, Milestone dates and weights are fixed, Activities are not interrupted, Time is measured in working days.

On a real project, none of these things are actually fixed. The owner can change the requirements, more people can be hired, equipment can be bought, equipment breaks down, whether pauses work, unforeseen issues arise that affect other work, etc.

## Planned final experiment

The final experiment will relax the assumption that activity durations are known exactly. One possible design multiplies each activity's planned duration by an independent random factor within some probability distribution. This represents  durations that can be shorter or longer than planned. Realized durations, and therefore scenario execution times, may be fractional working days.

CPM latest start baseline

`critical_path_method.py` calculates early start, early finish, late start, late finish, and slack using CPM forward and backward passes. It then implements the option's exact rule to beat: select the eligible activity with the smallest CPM latest start, breaking ties by activity ID only, and place it at its earliest resource-feasible time. An eligible activity has all predecessors already placed in the schedule; its actual start still waits for their completions.

Simulated annealing

`simulated_annealing.py` starts from the exact baseline activity sequence and improves complete sequences using precedence-feasible swaps and insertions. Each candidate is converted to a resource-feasible schedule. The search sometimes accepts a worse current solution to explore other sequences, while retaining the best solution found. Defaults are 3,000 attempted moves, random seed 522, a 120-second search safety cap, and geometric cooling. The cap is checked between moves; loading, checking, and writing add overhead. Neither method guarantees an optimal schedule or a strict improvement on every instance.

Independent decision checking

`check_decision.py` is a separate program. It reads the original input CSVs and an exported `decision.json`, independently checks the required constraints, and recomputes the project measures. It does not import or run either scheduling method.

## Inputs and outputs

Each instance directory under
`option_04_resource_constrained_project_scheduling` contains these input CSVs:

- `activities.csv`: activity IDs, durations, optional milestone dates, priority
  weights, and the unused locked-start column.
- `precedence.csv`: predecessor relationships; repeated relationships add no
  additional requirement.
- `resource_requirements.csv`: resource units occupied by each activity.
- `resource_capacities.csv`: available units of each resource.

Each method writes a `decision.json` containing one `activity_id`, `start`, and `completion` per activity. Its output folder also holds a CSV and a JSON summary. Annealing additionally writes its search history. Reusing an output folder overwrites successful results, so use separate folders for methods, cases, and experiment runs.

Running the tool

```powershell
pixi run python critical_path_method.py --case development_medium --output-dir cpm_output/development_medium
pixi run python simulated_annealing.py --case development_medium --output-dir simulated_annealing_output/development_medium

pixi run python check_decision.py --case development_medium --decision cpm_output/development_medium/decision.json --report cpm_output/development_medium/check_report.json
pixi run python check_decision.py --case development_medium --decision simulated_annealing_output/development_medium/decision.json --report simulated_annealing_output/development_medium/check_report.json
```

The checker must receive the **same input case that generated the decision**.
It prints validation status and recomputed measures; `--report` saves a separate
JSON check report. Its validation time is not the method's scheduling runtime.


Codex was used to draft the code for each of the programs, but it was checked and verified
