# One file, two interfaces:
# pixi run python cpm.py --case development_small
# pixi run marimo edit cpm.py
import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def scheduling_core():
    import marimo as mo

    import argparse
    import csv
    import heapq
    import json
    import math
    from pathlib import Path


    ROOT = mo.notebook_dir() or Path.cwd()
    DEFAULT_INPUT = ROOT / "option_04_resource_constrained_project_scheduling/hand_checkable"


    def read_csv(path, columns):
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if not set(columns).issubset(reader.fieldnames or []):
                raise ValueError(f"{path.name}: required columns are {columns}")
            return list(reader)


    def number(value, label, positive=False):
        result = float(value)
        if not math.isfinite(result) or result < 0 or (positive and result == 0):
            raise ValueError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
        return result


    def load_project(folder):
        """Load identifiers by value, deduplicating repeated precedence rows."""
        folder = Path(folder)
        tasks = {}
        for row in read_csv(folder / "activities.csv", ["activity_id", "duration", "milestone_due", "priority_weight", "locked_start"]):
            task = row["activity_id"]
            if not task or task in tasks:
                raise ValueError(f"Empty or duplicate activity identifier: {task!r}")
            if row["locked_start"].strip():
                raise ValueError("Locked starts are outside this solver's scope; expected an empty locked_start column.")
            tasks[task] = {
                "duration": number(row["duration"], f"{task} duration", positive=True),
                "due": number(row["milestone_due"], f"{task} milestone_due") if row["milestone_due"].strip() else None,
                "weight": number(row["priority_weight"], f"{task} priority_weight", positive=True),
                "predecessors": set(), "successors": set(), "requirements": {},
            }
        capacities = {}
        for row in read_csv(folder / "resource_capacities.csv", ["resource_id", "capacity"]):
            resource = row["resource_id"]
            if not resource or resource in capacities:
                raise ValueError(f"Empty or duplicate resource identifier: {resource!r}")
            capacities[resource] = number(row["capacity"], f"{resource} capacity", positive=True)
        for row in read_csv(folder / "precedence.csv", ["activity_id", "predecessor_id"]):
            task, predecessor = row["activity_id"], row["predecessor_id"]
            if task not in tasks or predecessor not in tasks:
                raise ValueError(f"Unknown activity in precedence: {predecessor} -> {task}")
            tasks[task]["predecessors"].add(predecessor)
            tasks[predecessor]["successors"].add(task)
        for row in read_csv(folder / "resource_requirements.csv", ["activity_id", "resource_id", "requirement"]):
            task, resource = row["activity_id"], row["resource_id"]
            if task not in tasks or resource not in capacities:
                raise ValueError(f"Unknown activity/resource: {task}, {resource}")
            if resource in tasks[task]["requirements"]:
                raise ValueError(f"Duplicate requirement: {task}, {resource}")
            requirement = number(row["requirement"], f"{task}/{resource} requirement")
            if requirement > capacities[resource]:
                raise ValueError(f"Infeasible: {task} requires {requirement:g} {resource}, capacity is {capacities[resource]:g}")
            tasks[task]["requirements"][resource] = requirement
        for task, data in tasks.items():
            if set(data["requirements"]) != set(capacities):
                raise ValueError(f"Missing activity-resource requirement for {task}")
        return tasks, capacities


    def topological_order(tasks):
        indegree = {task: len(data["predecessors"]) for task, data in tasks.items()}
        ready = [task for task in tasks if indegree[task] == 0]
        heapq.heapify(ready)
        order = []
        while ready:
            task = heapq.heappop(ready)
            order.append(task)
            for successor in sorted(tasks[task]["successors"]):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    heapq.heappush(ready, successor)
        if len(order) != len(tasks):
            raise ValueError("Precedence contains a cycle; no feasible sequence exists.")
        return order


    def cpm(tasks):
        """Forward/backward passes; late times use the earliest project finish."""
        order = topological_order(tasks)
        times = {}
        for task in order:
            start = max((times[p]["EF"] for p in tasks[task]["predecessors"]), default=0)
            times[task] = {"ES": start, "EF": start + tasks[task]["duration"]}
        finish = max((entry["EF"] for entry in times.values()), default=0)
        for task in reversed(order):
            late_finish = min((times[s]["LS"] for s in tasks[task]["successors"]), default=finish)
            times[task]["LF"] = late_finish
            times[task]["LS"] = late_finish - tasks[task]["duration"]
            times[task]["slack"] = times[task]["LS"] - times[task]["ES"]
            times[task]["critical"] = math.isclose(times[task]["slack"], 0, abs_tol=1e-8)
        # Trace one continuous critical path, including when several paths tie.
        path = []
        if order:
            task = next(t for t in reversed(order) if math.isclose(times[t]["EF"], finish, abs_tol=1e-8))
            while True:
                path.append(task)
                predecessors = [p for p in tasks[task]["predecessors"] if times[p]["critical"] and math.isclose(times[p]["EF"], times[task]["ES"], abs_tol=1e-8)]
                if not predecessors:
                    break
                task = min(predecessors)
        return times, finish, list(reversed(path))


    def fits(task, start, schedule, tasks, capacities):
        end = start + tasks[task]["duration"]
        # Usage is constant between start/completion events. Inspect every segment.
        events = {start}
        for other, (s, f) in schedule.items():
            if start < s < end:
                events.add(s)
            if start < f < end:
                events.add(f)
        for instant in events:
            for resource, capacity in capacities.items():
                usage = tasks[task]["requirements"][resource] + sum(
                    tasks[other]["requirements"][resource]
                    for other, (s, f) in schedule.items() if s <= instant < f
                )
                if usage > capacity + 1e-8:
                    return False
        return True


    def resource_schedule(tasks, capacities, times):
        """Exact rule to beat: lowest CPM latest start, then activity ID.

        Tasks can overlap. A task is inserted in the earliest feasible time slot;
        the returned selection order is a precedence-feasible priority sequence.
        """
        remaining, schedule, sequence = set(tasks), {}, []
        while remaining:
            ready = [t for t in remaining if tasks[t]["predecessors"].issubset(schedule)]
            if not ready:
                raise ValueError("No eligible activity; check precedence for cycles.")
            # Published rule to beat: smallest CPM latest start, ties by activity ID.
            task = min(ready, key=lambda t: (times[t]["LS"], t))
            earliest = max((schedule[p][1] for p in tasks[task]["predecessors"]), default=0)
            # A resource-blocked task can first become feasible at a completion event.
            candidates = sorted({earliest} | {f for s, f in schedule.values() if f >= earliest})
            for start in candidates:
                if fits(task, start, schedule, tasks, capacities):
                    schedule[task] = (start, start + tasks[task]["duration"])
                    break
            else:
                raise ValueError(f"No resource-feasible slot for {task}")
            sequence.append(task)
            remaining.remove(task)
        return schedule, sequence


    def validate_schedule(tasks, capacities, schedule):
        """Independently verify complete coverage, durations, precedence and usage."""
        if set(schedule) != set(tasks):
            raise ValueError("Schedule must contain every activity exactly once.")
        for task, (start, finish) in schedule.items():
            if not all(math.isfinite(t) for t in (start, finish)) or start < 0:
                raise ValueError(f"Invalid scheduled times for {task}")
            if not math.isclose(finish - start, tasks[task]["duration"], abs_tol=1e-8):
                raise ValueError(f"Incorrect duration for {task}")
            if any(schedule[p][1] > start + 1e-8 for p in tasks[task]["predecessors"]):
                raise ValueError(f"Precedence violation for {task}")
        for instant in sorted({t for interval in schedule.values() for t in interval}):
            active = [task for task, (s, f) in schedule.items() if s <= instant < f]
            for resource, capacity in capacities.items():
                if sum(tasks[t]["requirements"][resource] for t in active) > capacity + 1e-8:
                    raise ValueError(f"Capacity violation for {resource} at {instant:g}")


    def main(argv=None):
        parser = argparse.ArgumentParser(description="CPM and resource-feasible scheduling; also opens with marimo edit cpm.py")
        parser.add_argument("--case", choices=["hand_checkable", "development_small", "development_medium", "development_large", "edge_case"], default="hand_checkable")
        parser.add_argument("--input-dir", type=Path, help="Use a custom instance folder instead of --case")
        parser.add_argument("--output-dir", type=Path, default=ROOT / "cpm_output")
        args = parser.parse_args(argv)
        args.input_dir = args.input_dir or ROOT / "option_04_resource_constrained_project_scheduling" / args.case
        try:
            tasks, capacities = load_project(args.input_dir)
            times, duration, path = cpm(tasks)
            schedule, sequence = resource_schedule(tasks, capacities, times)
            validate_schedule(tasks, capacities, schedule)
        except (ValueError, OSError) as error:
            parser.exit(1, f"Error: {error}\n")
        start_order = sorted(schedule, key=lambda t: (schedule[t][0], sequence.index(t)))
        rows = [dict(activity_id=t, duration=tasks[t]["duration"], **times[t],
                     scheduled_start=schedule[t][0], scheduled_finish=schedule[t][1]) for t in start_order]
        makespan = max((f for s, f in schedule.values()), default=0)
        tardiness = sum(tasks[t]["weight"] * max(0, schedule[t][1] - tasks[t]["due"])
                        for t in tasks if tasks[t]["due"] is not None)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        with (args.output_dir / "cpm_results.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["activity_id", "duration", "ES", "EF", "LF", "LS", "slack", "critical", "scheduled_start", "scheduled_finish"])
            writer.writeheader()
            writer.writerows(rows)
        decision = {"schedule": [dict(activity_id=t, start=schedule[t][0], completion=schedule[t][1]) for t in start_order]}
        (args.output_dir / "decision.json").write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
        summary = dict(cpm_duration=duration, critical_path=path, priority_sequence=sequence,
                       start_sequence=start_order, scheduled_duration=makespan,
                       weighted_milestone_tardiness=tardiness, feasible=True,
                       method="Rule to beat: CPM latest start, ties by activity ID; optimality not guaranteed")
        (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print("Task      ES      EF      LS      LF   Slack   Start  Finish")
        for row in rows:
            print(f"{row['activity_id']:<6}" + "".join(f"{row[k]:8g}" for k in ("ES", "EF", "LS", "LF", "slack", "scheduled_start", "scheduled_finish")))
        print(f"\nCPM duration (precedence only): {duration:g} working days")
        print("One critical path: " + " -> ".join(path))
        print("Priority sequence: " + " -> ".join(sequence))
        print("Start sequence (ties may run together): " + " -> ".join(start_order))
        print(f"Verified resource-feasible duration: {makespan:g} working days")
        print(f"Results: {args.output_dir.resolve()}")

    return (argparse, csv, heapq, json, math, Path, ROOT, DEFAULT_INPUT, read_csv, number, load_project, topological_order, cpm, fits, resource_schedule, validate_schedule, main, mo,)


@app.cell
def execution_mode(main, mo):
    # Keep this dispatch in a cell: Marimo preserves it when saving the file.
    # Script mode prints/exports results and stops all notebook-only descendants.
    if mo.app_meta().mode == "script":
        main()
        mo.stop(True)
    notebook_ready = True
    return (notebook_ready,)


@app.cell
def visualization_imports(notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    import matplotlib.pyplot as plt
    import networkx as nx
    import seaborn as sns
    import polars as pl
    from matplotlib.patches import Patch

    sns.set_theme(style="whitegrid", palette="colorblind")
    colors = sns.color_palette("colorblind")
    return Patch, colors, nx, pl, plt


@app.cell(hide_code=True)
def _(mo, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    mo.md("""
    # Critical path method and project schedule

    Select the CSV instance below. The **forward pass** calculates early start
    (ES) and early finish (EF). The **backward pass** calculates late start
    (LS) and late finish (LF) against the earliest project finish. Slack is LS − ES.

    CPM considers dependencies. The resource schedule additionally respects crew
    and equipment capacities, using a serial scheduling heuristic that prioritizes
    eligible activities by CPM late start, breaking ties by activity ID.
    This is the published rule to beat. It produces a feasible schedule;
    it does not guarantee the shortest possible schedule. Milestone due dates
    are soft targets, and locked starts are outside the supplied instance scope.
    """)
    return


@app.cell
def instance_control(mo, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    instance = mo.ui.dropdown(
        options=["hand_checkable", "development_small", "development_medium", "development_large"],
        value="hand_checkable",
        label="Project instance",
    )
    instance
    return (instance,)


@app.cell
def load_notebook_data(instance, load_project, pl, ROOT, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    data_directory = ROOT / "option_04_resource_constrained_project_scheduling" / instance.value
    # Polars preserves optional all-empty numeric columns as numeric nulls.
    activities = pl.read_csv(
        data_directory / "activities.csv",
        schema_overrides={"activity_id": pl.String, "duration": pl.Float64,
                          "milestone_due": pl.Float64, "priority_weight": pl.Float64,
                          "locked_start": pl.Float64},
    )
    precedence = pl.read_csv(data_directory / "precedence.csv").unique(maintain_order=True)
    resource_requirements = pl.read_csv(data_directory / "resource_requirements.csv")
    resource_capacities = pl.read_csv(data_directory / "resource_capacities.csv")
    # Use the shared calculation functions defined above in this same file.
    tasks, capacities = load_project(data_directory)
    return activities, capacities, precedence, resource_capacities, resource_requirements, tasks


@app.cell
def _(activities, mo, precedence, resource_capacities, resource_requirements, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    mo.accordion({
        "Input activities": mo.ui.table(activities, selection=None),
        "Precedence requirements": mo.ui.table(precedence, selection=None),
        "Resource requirements": mo.ui.table(resource_requirements, selection=None),
        "Resource capacities": mo.ui.table(resource_capacities, selection=None),
    })
    return


@app.cell
def _(activities, nx, precedence, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    graph = nx.DiGraph()
    for _row in activities.iter_rows(named=True):
        graph.add_node(_row["activity_id"], duration=_row["duration"])
    for _row in precedence.iter_rows(named=True):
        graph.add_edge(_row["predecessor_id"], _row["activity_id"])
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("The precedence graph contains a cycle.")
    order = list(nx.lexicographical_topological_sort(graph))
    return graph, order


@app.cell
def notebook_cpm(cpm, graph, math, tasks, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    # Terminal and notebook modes call the exact same CPM function.
    times, cpm_duration, critical_path = cpm(tasks)
    critical_edges = [
        (_p, _s) for _p, _s in graph.edges
        if times[_p]["critical"] and times[_s]["critical"]
        and math.isclose(times[_p]["EF"], times[_s]["ES"], abs_tol=1e-8)
    ]
    return cpm_duration, critical_edges, critical_path, times


@app.cell
def notebook_schedule(capacities, resource_schedule, tasks, times, validate_schedule, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    schedule, priority_sequence = resource_schedule(tasks, capacities, times)
    validate_schedule(tasks, capacities, schedule)
    scheduled_duration = max((_f for _s, _f in schedule.values()), default=0)
    start_order = sorted(schedule, key=lambda t: (schedule[t][0], priority_sequence.index(t)))
    weighted_tardiness = sum(
        tasks[_t]["weight"] * max(0, schedule[_t][1] - tasks[_t]["due"])
        for _t in tasks if tasks[_t]["due"] is not None
    )
    return priority_sequence, schedule, scheduled_duration, start_order, weighted_tardiness


@app.cell(hide_code=True)
def _(cpm_duration, critical_path, mo, priority_sequence, scheduled_duration, start_order, weighted_tardiness, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    mo.md(f"""
    ## Project results

    - **CPM duration:** {cpm_duration:g} working days
    - **One critical path:** {' → '.join(critical_path)}
    - **Verified resource-feasible duration:** {scheduled_duration:g} working days
    - **Weighted milestone tardiness:** {weighted_tardiness:g}
    - **Precedence-feasible priority sequence:** {' → '.join(priority_sequence)}
    - **Actual start sequence:** {' → '.join(start_order)}

    Tasks with equal scheduled starts run together. The priority sequence is the
    order in which the algorithm places tasks; later placements can fill earlier
    available slots. CPM late times apply to the precedence-only finish, so a
    resource-constrained task can legitimately start after its CPM LS.
    """)
    return


@app.cell
def _(mo, pl, schedule, start_order, tasks, times, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    results = pl.DataFrame([
        {"activity_id": _t, "duration": tasks[_t]["duration"],
         "ES": times[_t]["ES"], "EF": times[_t]["EF"],
         "LS": times[_t]["LS"], "LF": times[_t]["LF"],
         "slack": times[_t]["slack"], "critical": times[_t]["critical"],
         "scheduled_start": schedule[_t][0], "scheduled_finish": schedule[_t][1]}
        for _t in start_order
    ])
    mo.ui.table(results, selection=None, page_size=20)
    return (results,)


@app.cell
def _(Patch, colors, critical_edges, graph, nx, plt, times, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    _positions = {}
    _generations = list(nx.topological_generations(graph))
    for _column, _generation in enumerate(_generations):
        for _row, _task in enumerate(sorted(_generation)):
            _positions[_task] = (_column, (len(_generation) - 1) / 2 - _row)
    _max_rows = max((len(_g) for _g in _generations), default=1)
    _fig, _ax = plt.subplots(figsize=(max(10, min(24, len(_generations) * 2.3)), max(5, _max_rows * 1.5)))
    # Straight segments avoid slow arrow-patch rendering on some Matplotlib
    # installations. Every dependency runs from the left column to the right.
    nx.draw_networkx_edges(graph, _positions, ax=_ax, edge_color="0.7", arrows=False)
    nx.draw_networkx_edges(graph, _positions, ax=_ax, edgelist=critical_edges,
                           edge_color=[colors[1]], width=3, arrows=False)
    nx.draw_networkx_nodes(graph, _positions, ax=_ax, node_size=2400,
                           node_color=[colors[1] if times[_t]["critical"] else colors[0] for _t in graph])
    nx.draw_networkx_labels(graph, _positions, ax=_ax, font_size=8, labels={
        _t: f"{_t} ({graph.nodes[_t]['duration']:g}d)\nES {times[_t]['ES']:g} EF {times[_t]['EF']:g}\nLS {times[_t]['LS']:g} LF {times[_t]['LF']:g}"
        for _t in graph
    })
    _ax.legend(handles=[Patch(color=colors[1], label="Critical"), Patch(color=colors[0], label="Noncritical")], loc="upper left")
    _ax.set_title("Precedence network and CPM times (dependencies run left to right)")
    _ax.margins(x=0.15, y=0.25)
    _ax.axis("off")
    _fig.tight_layout()
    _fig
    return


@app.cell
def _(Patch, colors, cpm_duration, plt, schedule, scheduled_duration, start_order, tasks, times, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    _fig, _axes = plt.subplots(1, 2, figsize=(16, max(5, len(start_order) * 0.3)), sharey=True)
    for _row, _task in enumerate(start_order):
        _color = colors[1] if times[_task]["critical"] else colors[0]
        _axes[0].barh(_row, tasks[_task]["duration"], left=times[_task]["ES"], color=_color)
        _axes[0].barh(_row, times[_task]["slack"], left=times[_task]["EF"], color="white", edgecolor="0.5", hatch="//")
        _axes[1].barh(_row, tasks[_task]["duration"], left=schedule[_task][0], color=_color)
    _axes[0].set_yticks(range(len(start_order)), labels=start_order)
    _axes[0].invert_yaxis()
    _axes[0].set_title("CPM early schedule (dependencies only)")
    _axes[1].set_title("Verified resource-feasible schedule")
    for _ax, _finish in zip(_axes, [cpm_duration, scheduled_duration]):
        _ax.axvline(_finish, color="black", linestyle="--", label=f"Finish: {_finish:g}")
        _ax.set_xlabel("Working day")
        _ax.set_xlim(left=0)
        _ax.legend(loc="upper right")
    _fig.legend(handles=[Patch(color=colors[1], label="CPM critical"), Patch(color=colors[0], label="CPM noncritical"),
                         Patch(facecolor="white", edgecolor="0.5", hatch="//", label="CPM slack")],
                loc="upper center", ncol=3)
    _fig.tight_layout(rect=(0, 0, 1, 0.94))
    _fig
    return


@app.cell
def _(capacities, colors, plt, schedule, scheduled_duration, tasks, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    _events = sorted({0.0} | {_v for _interval in schedule.values() for _v in _interval})
    _fig, _axes = plt.subplots(len(capacities), 1, figsize=(12, 3 * len(capacities)), squeeze=False)
    for _index, (_resource, _capacity) in enumerate(capacities.items()):
        _ax = _axes[_index, 0]
        _usage = [sum(tasks[_t]["requirements"][_resource] for _t, (_s, _f) in schedule.items() if _s <= _time < _f) for _time in _events]
        _ax.step(_events, _usage, where="post", color=colors[_index % len(colors)], label="Scheduled usage")
        _ax.fill_between(_events, _usage, step="post", alpha=0.2, color=colors[_index % len(colors)])
        _ax.axhline(_capacity, color="black", linestyle="--", label=f"Capacity: {_capacity:g}")
        _ax.set(title=f"{_resource} utilization", xlabel="Working day", ylabel="Units", xlim=(0, max(1, scheduled_duration)), ylim=(0, _capacity * 1.2))
        _ax.legend()
    _fig.tight_layout()
    _fig
    return


@app.cell
def _(json, mo, results, schedule, start_order, notebook_ready):
    assert notebook_ready  # Reactive dependency on the interactive-mode gate.
    _decision = {"schedule": [{"activity_id": _t, "start": schedule[_t][0], "completion": schedule[_t][1]} for _t in start_order]}
    mo.hstack([
        mo.download(results.write_csv().encode("utf-8"), filename="cpm_results.csv", label="Download CPM table"),
        mo.download(json.dumps(_decision, indent=2).encode("utf-8"), filename="decision.json", label="Download feasible schedule"),
    ], justify="start")
    return


if __name__ == "__main__":
    app.run()
