# One standalone file, two interfaces:
# pixi run python simulated_annealing.py --case development_small
# pixi run marimo edit simulated_annealing.py
import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def scheduling_core():
    import marimo as mo

    import heapq

    def topological_order(tasks):
        indegree = {task: len(data['predecessors']) for task, data in tasks.items()}
        ready = [task for task in tasks if indegree[task] == 0]
        heapq.heapify(ready)
        order = []
        while ready:
            task = heapq.heappop(ready)
            order.append(task)
            for successor in sorted(tasks[task]['successors']):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    heapq.heappush(ready, successor)
        if len(order) != len(tasks):
            raise ValueError('Precedence contains a cycle; no feasible sequence exists.')
        return order

    def cpm(tasks):
        """Forward/backward passes; late times use the earliest project finish."""
        order = topological_order(tasks)
        times = {}
        for task in order:
            start = max((times[p]['EF'] for p in tasks[task]['predecessors']), default=0)
            times[task] = {'ES': start, 'EF': start + tasks[task]['duration']}
        finish = max((entry['EF'] for entry in times.values()), default=0)
        for task in reversed(order):
            late_finish = min((times[s]['LS'] for s in tasks[task]['successors']), default=finish)
            times[task]['LF'] = late_finish
            times[task]['LS'] = late_finish - tasks[task]['duration']
            times[task]['slack'] = times[task]['LS'] - times[task]['ES']
            times[task]['critical'] = math.isclose(times[task]['slack'], 0, abs_tol=1e-08)
        path = []
        if order:
            task = next((t for t in reversed(order) if math.isclose(times[t]['EF'], finish, abs_tol=1e-08)))
            while True:
                path.append(task)
                predecessors = [p for p in tasks[task]['predecessors'] if times[p]['critical'] and math.isclose(times[p]['EF'], times[task]['ES'], abs_tol=1e-08)]
                if not predecessors:
                    break
                task = min(predecessors)
        return (times, finish, list(reversed(path)))

    def validate_schedule(tasks, capacities, schedule):
        """Independently verify complete coverage, durations, precedence and usage."""
        if set(schedule) != set(tasks):
            raise ValueError('Schedule must contain every activity exactly once.')
        for task, (start, finish) in schedule.items():
            if not all((math.isfinite(t) for t in (start, finish))) or start < 0:
                raise ValueError(f'Invalid scheduled times for {task}')
            if not math.isclose(finish - start, tasks[task]['duration'], abs_tol=1e-08):
                raise ValueError(f'Incorrect duration for {task}')
            if any((schedule[p][1] > start + 1e-08 for p in tasks[task]['predecessors'])):
                raise ValueError(f'Precedence violation for {task}')
        for instant in sorted({t for interval in schedule.values() for t in interval}):
            active = [task for task, (s, f) in schedule.items() if s <= instant < f]
            for resource, capacity in capacities.items():
                if sum((tasks[t]['requirements'][resource] for t in active)) > capacity + 1e-08:
                    raise ValueError(f'Capacity violation for {resource} at {instant:g}')
    import argparse
    import csv
    import json
    import math
    import random
    import re
    import time
    from pathlib import Path
    ROOT = mo.notebook_dir() or Path.cwd()
    DATA_ROOT = ROOT / 'option_04_resource_constrained_project_scheduling'
    CASES = ('hand_checkable', 'development_small', 'development_medium', 'development_large', 'edge_case')
    TOL = 1e-08

    class InfeasibleError(ValueError):
        """Valid input with evidence that no feasible schedule exists."""

    def _rows(folder, filename, columns, optional=()):
        """Apply the published schema directly; schema.json is not required input."""
        with (folder / filename).open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames or []
            missing = set(columns) - set(headers)
            if missing or len(headers) != len(set(headers)):
                raise ValueError(f'{filename}: missing columns {sorted(missing)} or duplicate column headers')
            for line, row in enumerate(reader, 2):
                if None in row:
                    raise ValueError(f'{filename}, file line {line}: too many fields')
                converted = {}
                for column, kind in columns.items():
                    value = row.get(column)
                    label = f'{filename}, file line {line}, column {column}'
                    if value is None or value.strip() == '':
                        if column in optional:
                            converted[column] = None
                            continue
                        raise ValueError(f'{label}: required value is empty')
                    if kind is int:
                        if not re.fullmatch('[+-]?\\d+', value.strip()):
                            raise ValueError(f'{label}: expected an integer, got {value!r}')
                        converted[column] = int(value)
                    else:
                        converted[column] = value
                yield (line, converted)

    def load_instance(folder):
        """Validate all four CSVs without editing them or trusting row counts."""
        folder = Path(folder)
        tasks, capacities = ({}, {})
        for line, row in _rows(folder, 'activities.csv', {'activity_id': str, 'duration': int, 'milestone_due': int, 'priority_weight': int, 'locked_start': int}, optional=('milestone_due', 'locked_start')):
            task = row['activity_id']
            label = f'activities.csv, file line {line}'
            if task in tasks:
                raise ValueError(f'{label}, column activity_id: duplicate {task}')
            for column in ('duration', 'priority_weight'):
                if row[column] <= 0:
                    raise ValueError(f'{label}, column {column}: must be positive')
            if row['milestone_due'] is not None and row['milestone_due'] < 0:
                raise ValueError(f'{label}, column milestone_due: must be nonnegative')
            if row['locked_start'] is not None:
                raise ValueError(f'{label}, column locked_start: this model supports the published cases with empty locked starts')
            tasks[task] = dict(duration=row['duration'], due=row['milestone_due'], weight=row['priority_weight'], predecessors=set(), successors=set(), requirements={})
        for line, row in _rows(folder, 'resource_capacities.csv', {'resource_id': str, 'capacity': int}):
            resource = row['resource_id']
            if resource in capacities or row['capacity'] <= 0:
                raise ValueError(f'resource_capacities.csv, file line {line}, columns resource_id/capacity: duplicate ID or nonpositive capacity')
            capacities[resource] = row['capacity']
        for line, row in _rows(folder, 'precedence.csv', {'activity_id': str, 'predecessor_id': str}):
            task, predecessor = (row['activity_id'], row['predecessor_id'])
            if task not in tasks or predecessor not in tasks:
                raise ValueError(f'precedence.csv, file line {line}, columns activity_id/predecessor_id: unknown ID {task!r} or {predecessor!r}')
            tasks[task]['predecessors'].add(predecessor)
            tasks[predecessor]['successors'].add(task)
        for line, row in _rows(folder, 'resource_requirements.csv', {'activity_id': str, 'resource_id': str, 'requirement': int}):
            task, resource = (row['activity_id'], row['resource_id'])
            label = f'resource_requirements.csv, file line {line}'
            if task not in tasks or resource not in capacities:
                raise ValueError(f'{label}, columns activity_id/resource_id: unknown ID {task!r} or {resource!r}')
            if resource in tasks[task]['requirements']:
                raise ValueError(f'{label}, columns activity_id/resource_id: repeated pair {task}/{resource}')
            if row['requirement'] < 0:
                raise ValueError(f'{label}, column requirement: must be nonnegative')
            tasks[task]['requirements'][resource] = row['requirement']
        for task, data in tasks.items():
            missing = set(capacities) - set(data['requirements'])
            if missing:
                raise ValueError(f'resource_requirements.csv, columns activity_id/resource_id: missing pairs for {task}: {sorted(missing)}')
        topological_order(tasks)
        return (tasks, capacities)

    def ensure_possible(tasks, capacities):
        for task, data in tasks.items():
            for resource, demand in data['requirements'].items():
                if demand > capacities[resource]:
                    raise InfeasibleError(f'{task} requires {demand} units of {resource}; capacity is {capacities[resource]}')

    def earliest_slot(task, earliest, schedule, tasks, capacities):
        """Sweep resource-change events to find the first uninterrupted feasible gap.

        Event deltas release resources and acquire them at the same boundary;
        usage therefore represents the half-open interval starting at that event.
        This avoids scanning every integer day or retrying all candidate starts.
        """
        events = {earliest: {r: 0 for r in capacities}}
        usage = {r: 0 for r in capacities}
        for other, (start, finish) in schedule.items():
            for resource, demand in tasks[other]['requirements'].items():
                if start <= earliest < finish:
                    usage[resource] += demand
                for instant, delta in ((start, demand), (finish, -demand)):
                    if instant > earliest:
                        events.setdefault(instant, {r: 0 for r in capacities})[resource] += delta
        boundaries = sorted(events)
        candidate = earliest
        for index, instant in enumerate(boundaries):
            for resource, delta in events[instant].items():
                usage[resource] += delta
            next_event = boundaries[index + 1] if index + 1 < len(boundaries) else math.inf
            if any((usage[r] + tasks[task]['requirements'][r] > capacities[r] for r in capacities)):
                candidate = next_event
            elif candidate + tasks[task]['duration'] <= next_event:
                return int(candidate)
        raise InfeasibleError(f'No slot exists for {task}; check individual resource requirements')

    def build_schedule(tasks, capacities, priority, rng=None, selection='best'):
        """Construct a complete schedule; only precedence-eligible tasks are selected."""
        remaining, schedule, sequence = (set(tasks), {}, [])
        while remaining:
            ready = sorted((t for t in remaining if tasks[t]['predecessors'].issubset(schedule)), key=lambda t: (priority[t], t))
            if not ready:
                raise ValueError('precedence.csv: cycle prevents a complete sequence')
            if selection == 'uniform':
                task = rng.choice(ready)
            elif selection == 'biased':
                task = rng.choice(ready[:min(3, len(ready))])
            else:
                task = ready[0]
            earliest = max((schedule[p][1] for p in sorted(tasks[task]['predecessors'])), default=0)
            start = earliest_slot(task, earliest, schedule, tasks, capacities)
            schedule[task] = (start, start + tasks[task]['duration'])
            sequence.append(task)
            remaining.remove(task)
        return (schedule, sequence)

    def metrics(tasks, schedule, lower_bound):
        completion = max((finish for start, finish in schedule.values()), default=0)
        milestones = [(tasks[t], finish) for t, (start, finish) in schedule.items() if tasks[t]['due'] is not None]
        return dict(makespan=completion, activities_completed=len(schedule), weighted_milestone_delay=sum((data['weight'] * max(0, finish - data['due']) for data, finish in milestones)), late_milestones=sum((finish > data['due'] + TOL for data, finish in milestones)), cpm_duration=lower_bound, cpm_gap=completion - lower_bound)

    def score(measures):
        return (measures['makespan'], measures['weighted_milestone_delay'], measures['late_milestones'])

    def decision_from_schedule(schedule):
        return {'schedule': [dict(activity_id=t, start=schedule[t][0], completion=schedule[t][1]) for t in sorted(schedule, key=lambda t: (schedule[t][0], t))]}

    def check_decision(input_dir, decision_path):
        """Reload raw inputs and decision; do not use scheduler feasibility logic.

        The checker sums active requirements at every event directly, independently
        of the event-delta/gap algorithm used to construct the schedule.
        """
        tasks, capacities = load_instance(input_dir)
        with Path(decision_path).open(encoding='utf-8') as stream:
            decision = json.load(stream)
        if not isinstance(decision, dict) or not isinstance(decision.get('schedule'), list):
            raise ValueError('decision.json: expected a top-level schedule list')
        schedule = {}
        for index, entry in enumerate(decision['schedule'], 1):
            if not isinstance(entry, dict):
                raise ValueError(f'decision.json schedule entry {index}: expected an object')
            task = entry.get('activity_id')
            if not isinstance(task, str) or task not in tasks or task in schedule:
                raise ValueError(f'decision.json schedule entry {index}: unknown or duplicate activity_id {task!r}')
            start, finish = (entry.get('start'), entry.get('completion'))
            if any((isinstance(v, bool) or not isinstance(v, (int, float)) or (not math.isfinite(v)) for v in (start, finish))):
                raise ValueError(f'decision.json {task}: start/completion must be finite numbers')
            if start < 0 or abs(start - round(start)) > TOL:
                raise ValueError(f'decision.json {task}: start must be a nonnegative whole working day')
            if abs(finish - start - tasks[task]['duration']) > TOL:
                raise ValueError(f'decision.json {task}: completion does not match its input duration')
            schedule[task] = (start, finish)
        if set(schedule) != set(tasks):
            raise ValueError(f'decision.json: missing activities {sorted(set(tasks) - set(schedule))}')
        for task, (start, finish) in schedule.items():
            for predecessor in tasks[task]['predecessors']:
                if schedule[predecessor][1] > start + TOL:
                    raise ValueError(f'decision.json: precedence violation {predecessor} -> {task}')
        for instant in sorted({v for interval in schedule.values() for v in interval}):
            for resource, capacity in capacities.items():
                active = [t for t, (s, f) in schedule.items() if s <= instant < f]
                demand = sum((tasks[t]['requirements'][resource] for t in active))
                if demand > capacity + TOL:
                    raise ValueError(f'decision.json: {resource} demand {demand} > {capacity} at day {instant:g}; active: {active}')
        _, lower_bound, _ = cpm(tasks)
        return metrics(tasks, schedule, lower_bound)

    def write_json(path, data):
        path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')

    def write_csv(path, rows, columns):
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def is_precedence_feasible(sequence, tasks):
        if len(sequence) != len(tasks) or set(sequence) != set(tasks):
            return False
        position = {task: i for i, task in enumerate(sequence)}
        return all((position[p] < position[t] for t in tasks for p in tasks[t]['predecessors']))

    def neighbor(sequence, tasks, rng):
        """Mix precedence-preserving insertion and feasible pair swaps.

        An invalid swap is a counted no-op. An insertion is restricted to the
        interval after all direct predecessors and before all direct successors.
        Those restrictions preserve the complete transitive precedence relation.
        """
        if len(sequence) < 2:
            return (list(sequence), 'no_move')
        candidate = list(sequence)
        if rng.random() < 0.5:
            first, second = rng.sample(range(len(candidate)), 2)
            candidate[first], candidate[second] = (candidate[second], candidate[first])
            if is_precedence_feasible(candidate, tasks):
                return (candidate, 'swap')
            return (list(sequence), 'invalid_swap')
        original = rng.randrange(len(candidate))
        task = candidate.pop(original)
        positions = {t: i for i, t in enumerate(candidate)}
        lower = max((positions[p] + 1 for p in tasks[task]['predecessors']), default=0)
        upper = min((positions[s] for s in tasks[task]['successors']), default=len(candidate))
        choices = [index for index in range(lower, upper + 1) if index != original]
        if not choices:
            return (list(sequence), 'no_insertion')
        candidate.insert(rng.choice(choices), task)
        return (candidate, 'insertion')

    def decode(sequence, tasks, capacities):
        """Serially insert each task at its earliest resource-feasible whole day."""
        if not is_precedence_feasible(sequence, tasks):
            raise ValueError('Activity list must contain every task once and obey precedence')
        schedule = {}
        for task in sequence:
            earliest = max((schedule[p][1] for p in tasks[task]['predecessors']), default=0)
            start = earliest_slot(task, earliest, schedule, tasks, capacities)
            schedule[task] = (start, start + tasks[task]['duration'])
        return schedule

    def objective_scale(tasks):
        """Integer radix keeps milestone objectives below one makespan day.

        Any serially generated schedule finishes no later than the sum of durations.
        This bounds total weighted delay; its maximum plus one is the radix.
        """
        horizon = sum((data['duration'] for data in tasks.values()))
        milestones = [data for data in tasks.values() if data['due'] is not None]
        delay_bound = sum((data['weight'] * max(0, horizon - data['due']) for data in milestones))
        late_base = len(milestones) + 1
        return ((delay_bound + 1) * late_base, late_base)

    def energy_delta(candidate, current, scale, late_base):
        return ((candidate['makespan'] - current['makespan']) * scale + (candidate['weighted_milestone_delay'] - current['weighted_milestone_delay']) * late_base + candidate['late_milestones'] - current['late_milestones']) / scale

    def accept_move(delta, temperature, rng):
        return delta <= 0 or rng.random() < math.exp(-delta / temperature)

    def solve(tasks, capacities, iterations=3000, seed=522, time_limit=120.0, initial_temperature=None, final_temperature=0.05):
        if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1:
            raise ValueError('iterations must be a positive integer')
        if not math.isfinite(time_limit) or time_limit <= 0:
            raise ValueError('time_limit must be finite and positive')
        if not math.isfinite(final_temperature) or final_temperature <= 0:
            raise ValueError('final_temperature must be finite and positive')
        if initial_temperature is not None and (not math.isfinite(initial_temperature) or initial_temperature <= 0):
            raise ValueError('initial_temperature must be finite and positive')
        started = time.perf_counter()
        ensure_possible(tasks, capacities)
        times, lower_bound, critical_path = cpm(tasks)
        baseline_schedule, baseline_sequence = build_schedule(tasks, capacities, {t: times[t]['LS'] for t in sorted(tasks)})
        baseline = metrics(tasks, baseline_schedule, lower_bound)
        initial_temperature = max(1.0, baseline['makespan'] * 0.05) if initial_temperature is None else initial_temperature
        if final_temperature > initial_temperature:
            raise ValueError('final_temperature must not exceed initial_temperature')
        rng = random.Random(seed)
        scale, late_base = objective_scale(tasks)
        current_sequence, current_metrics = (list(baseline_sequence), baseline.copy())
        best_sequence, best_metrics = (list(baseline_sequence), baseline.copy())
        cache = {tuple(current_sequence): current_metrics.copy()}
        history = []
        accepted_count = worse_count = evaluations = 0
        stop_reason = 'iteration_count'
        for iteration in range(iterations):
            if time.perf_counter() - started >= time_limit:
                stop_reason = 'time_limit'
                break
            fraction = iteration / max(1, iterations - 1)
            temperature = math.exp((1 - fraction) * math.log(initial_temperature) + fraction * math.log(final_temperature))
            candidate_sequence, move = neighbor(current_sequence, tasks, rng)
            changed = candidate_sequence != current_sequence
            key = tuple(candidate_sequence)
            cached = key in cache
            if not cached:
                candidate_schedule = decode(candidate_sequence, tasks, capacities)
                cache[key] = metrics(tasks, candidate_schedule, lower_bound)
                evaluations += 1
            candidate_metrics = cache[key]
            delta = energy_delta(candidate_metrics, current_metrics, scale, late_base)
            accepted = changed and accept_move(delta, temperature, rng)
            worse = accepted and score(candidate_metrics) > score(current_metrics)
            if accepted:
                current_sequence, current_metrics = (candidate_sequence, candidate_metrics)
                accepted_count += 1
                worse_count += int(worse)
            improved = score(current_metrics) < score(best_metrics)
            if improved:
                best_sequence, best_metrics = (list(current_sequence), current_metrics.copy())
            history.append(dict(iteration=iteration + 1, move=move, temperature=temperature, changed=changed, cached=cached, accepted=accepted, accepted_worse=worse, candidate_makespan=candidate_metrics['makespan'], current_makespan=current_metrics['makespan'], best_makespan=best_metrics['makespan'], best_weighted_milestone_delay=best_metrics['weighted_milestone_delay'], improved=improved))
        schedule = decode(best_sequence, tasks, capacities)
        return dict(schedule=schedule, sequence=best_sequence, times=times, critical_path=critical_path, metrics=best_metrics, baseline_metrics=baseline, seed=seed, iterations_requested=iterations, iterations_completed=len(history), initial_temperature=initial_temperature, final_temperature=final_temperature, accepted_moves=accepted_count, accepted_worse_moves=worse_count, schedule_evaluations=evaluations + 1, history=history, stop_reason=stop_reason, search_seconds=time.perf_counter() - started)

    def main(argv=None):
        parser = argparse.ArgumentParser(description="Resource-constrained project scheduling: simulated_annealing.py; also opens with marimo edit")
        parser.add_argument('--case', choices=CASES, default='hand_checkable')
        parser.add_argument('--input-dir', type=Path)
        parser.add_argument('--output-dir', type=Path, default=ROOT / 'simulated_annealing_output')
        parser.add_argument('--iterations', type=int, default=3000)
        parser.add_argument('--seed', type=int, default=522)
        parser.add_argument('--time-limit', type=float, default=120.0)
        parser.add_argument('--initial-temperature', type=float, default=None, help='Default: max(1, 5%% of baseline makespan)')
        parser.add_argument('--final-temperature', type=float, default=0.05)
        parser.add_argument('--check-decision', type=Path)
        args = parser.parse_args(argv)
        input_dir = (args.input_dir or DATA_ROOT / args.case).resolve()
        output_dir = args.output_dir.resolve()
        started = time.perf_counter()
        if args.check_decision:
            try:
                checked = check_decision(input_dir, args.check_decision)
            except (ValueError, OSError) as error:
                parser.exit(1, f'Check failed: {error}\n')
            print(json.dumps(dict(status='feasible', **checked), indent=2))
            return
        if output_dir.is_relative_to(input_dir):
            parser.exit(1, 'Error: output directory must be separate from the input directory.\n')
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / 'decision.json').unlink(missing_ok=True)
            tasks, capacities = load_instance(input_dir)
            print(f'Simulated annealing: {len(tasks)} activities, {args.iterations} iterations, seed {args.seed}', flush=True)
            result = solve(tasks, capacities, args.iterations, args.seed, args.time_limit, args.initial_temperature, args.final_temperature)
            decision_path = output_dir / 'decision.json'
            write_json(decision_path, decision_from_schedule(result['schedule']))
            checked = check_decision(input_dir, decision_path)
            improvement = result['baseline_metrics']['makespan'] - checked['makespan']
            fields = ('seed', 'iterations_requested', 'iterations_completed', 'initial_temperature', 'final_temperature', 'accepted_moves', 'accepted_worse_moves', 'schedule_evaluations', 'stop_reason', 'sequence', 'search_seconds')
            summary = dict(status='feasible', method='simulated_annealing_activity_lists', input_dir=str(input_dir), **checked, rule_to_beat=result['baseline_metrics'], improvement_days=improvement, beats_rule_to_beat=improvement > 0.001, **{k: result[k] for k in fields}, search_time_limit_seconds=args.time_limit, optimality_proven=False, independent_check='passed')
            schedule = result['schedule']
            rows = [dict(activity_id=t, duration=tasks[t]['duration'], **result['times'][t], scheduled_start=schedule[t][0], scheduled_finish=schedule[t][1]) for t in sorted(tasks, key=lambda t: (schedule[t][0], t))]
            write_csv(output_dir / 'schedule.csv', rows, ['activity_id', 'duration', 'ES', 'EF', 'LS', 'LF', 'slack', 'critical', 'scheduled_start', 'scheduled_finish'])
            history = result['history']
            write_csv(output_dir / 'annealing_history.csv', history, ['iteration', 'move', 'temperature', 'changed', 'cached', 'accepted', 'accepted_worse', 'candidate_makespan', 'current_makespan', 'best_makespan', 'best_weighted_milestone_delay', 'improved'])
            summary['command_seconds_before_summary_write'] = time.perf_counter() - started
            write_json(output_dir / 'summary.json', summary)
            print('Task     Start   Finish     ES     EF     LS     LF')
            for row in rows:
                print(f"{row['activity_id']:<8}" + ''.join((f'{row[k]:7g}' for k in ('scheduled_start', 'scheduled_finish', 'ES', 'EF', 'LS', 'LF'))))
            print(f"Rule to beat: {result['baseline_metrics']['makespan']:g} days; annealing: {checked['makespan']:g} days; improvement: {improvement:g} days")
            print(f"Checked feasible. {result['iterations_completed']} iterations; {result['accepted_worse_moves']} worse moves accepted; stop: {result['stop_reason']}; search: {result['search_seconds']:.3f}s")
            print(f'Outputs: {output_dir}')
        except InfeasibleError as error:
            write_json(output_dir / 'summary.json', dict(status='infeasible', evidence=str(error)))
            print(f'Infeasible: {error}')
        except (ValueError, OSError) as error:
            (output_dir / 'decision.json').unlink(missing_ok=True)
            if output_dir.exists():
                write_json(output_dir / 'summary.json', dict(status='error', message=str(error)))
            parser.exit(1, f'Error: {error}\n')
    return (mo, heapq, topological_order, cpm, validate_schedule, argparse, csv, json, math, random, re, time, Path, ROOT, DATA_ROOT, CASES, TOL, InfeasibleError, load_instance, ensure_possible, earliest_slot, build_schedule, metrics, score, decision_from_schedule, check_decision, write_json, write_csv, is_precedence_feasible, neighbor, decode, objective_scale, energy_delta, accept_move, solve, main,)


@app.cell
def execution_mode(main, mo):
    # Keeping dispatch in a cell preserves it when Marimo saves the notebook.
    if mo.app_meta().mode == "script":
        main()
        mo.stop(True)
    notebook_ready = True
    return (notebook_ready,)


@app.cell
def visualization_imports(notebook_ready):
    assert notebook_ready
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
    assert notebook_ready  # Real reactive dependency on interactive mode.
    mo.md("""
    # Simulated annealing project scheduling

    Select the CSV instance below. The **forward pass** calculates early start
    (ES) and early finish (EF). The **backward pass** calculates late start
    (LS) and late finish (LF) against the earliest project finish. Slack is LS − ES.

    CPM considers dependencies. The annealing schedule additionally respects crew
    and equipment capacities. It improves a complete activity list using swaps
    and insertions, sometimes accepts worse moves, and retains the best visited list. It produces a feasible schedule;
    it does not guarantee the shortest possible schedule. Milestone due dates
    are soft targets, and locked starts are outside the supplied instance scope.
    """)
    return


@app.cell
def _(mo, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
    instance = mo.ui.dropdown(
        options=["hand_checkable", "development_small", "development_medium", "development_large"],
        value="hand_checkable",
        label="Project instance",
    )
    instance
    return (instance,)


@app.cell
def load_notebook_data(instance, load_instance, pl, ROOT, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
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
    # Validate published input types and relationships before searching.
    tasks, capacities = load_instance(data_directory)
    return activities, capacities, precedence, resource_capacities, resource_requirements, tasks


@app.cell
def _(activities, mo, precedence, resource_capacities, resource_requirements, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
    mo.accordion({
        "Input activities": mo.ui.table(activities, selection=None),
        "Precedence requirements": mo.ui.table(precedence, selection=None),
        "Resource requirements": mo.ui.table(resource_requirements, selection=None),
        "Resource capacities": mo.ui.table(resource_capacities, selection=None),
    })
    return


@app.cell
def _(activities, nx, precedence, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
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
    assert notebook_ready  # Real reactive dependency on interactive mode.
    times, cpm_duration, critical_path = cpm(tasks)
    critical_edges = [
        (_p, _s) for _p, _s in graph.edges
        if times[_p]["critical"] and times[_s]["critical"]
        and math.isclose(times[_p]["EF"], times[_s]["ES"], abs_tol=1e-8)
    ]
    return cpm_duration, critical_edges, critical_path, times


@app.cell
def _(mo, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
    iterations_control = mo.ui.number(start=1, stop=10000, step=1, value=3000, label="Annealing iterations")
    seed_control = mo.ui.number(start=0, stop=1000000, step=1, value=522, label="Random seed")
    mo.hstack([iterations_control, seed_control], justify="start")
    return seed_control, iterations_control


@app.cell
def notebook_schedule(capacities, iterations_control, seed_control, solve, tasks, validate_schedule, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
    search_result = solve(tasks, capacities, iterations=int(iterations_control.value), seed=int(seed_control.value))
    schedule = search_result["schedule"]
    validate_schedule(tasks, capacities, schedule)
    priority_sequence = search_result["sequence"]
    scheduled_duration = max((_f for _s, _f in schedule.values()), default=0)
    start_order = sorted(schedule, key=lambda t: (schedule[t][0], priority_sequence.index(t)))
    weighted_tardiness = sum(
        tasks[_t]["weight"] * max(0, schedule[_t][1] - tasks[_t]["due"])
        for _t in tasks if tasks[_t]["due"] is not None
    )
    return priority_sequence, schedule, scheduled_duration, search_result, start_order, weighted_tardiness


@app.cell(hide_code=True)
def _(mo, search_result, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
    _baseline = search_result["baseline_metrics"]["makespan"]
    _best = search_result["metrics"]["makespan"]
    mo.md(f"**Exact rule to beat:** {_baseline:g} days. **Simulated annealing:** {_best:g} days. "
          f"**Improvement:** {_baseline - _best:g} days. "
          f"Attempted {search_result['iterations_completed']} moves; stop: {search_result['stop_reason']}. "
          "Use the terminal program to export and independently check decision.json.")
    return


@app.cell(hide_code=True)
def _(cpm_duration, critical_path, mo, priority_sequence, scheduled_duration, start_order, weighted_tardiness, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
    mo.md(f"""
    ## Project results

    - **CPM duration:** {cpm_duration:g} working days
    - **One critical path:** {' → '.join(critical_path)}
    - **Verified resource-feasible duration:** {scheduled_duration:g} working days
    - **Weighted milestone tardiness:** {weighted_tardiness:g}
    - **Precedence-feasible priority sequence:** {' → '.join(priority_sequence)}
    - **Actual start sequence:** {' → '.join(start_order)}

    Tasks with equal scheduled starts run together. The priority sequence is the
    winning order in which the algorithm places tasks; later placements can fill earlier
    available slots. CPM late times apply to the precedence-only finish, so a
    resource-constrained task can legitimately start after its CPM LS.
    """)
    return


@app.cell
def _(mo, pl, schedule, start_order, tasks, times, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
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
    assert notebook_ready  # Real reactive dependency on interactive mode.
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
    assert notebook_ready  # Real reactive dependency on interactive mode.
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
    assert notebook_ready  # Real reactive dependency on interactive mode.
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
def _(colors, mo, plt, search_result, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
    _history = search_result["history"]
    if _history:
        _fig, _axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
        _iterations = [_row["iteration"] for _row in _history]
        _axes[0].plot(_iterations, [_row["current_makespan"] for _row in _history],
                      color=colors[0], alpha=0.6, label="Current solution")
        _axes[0].plot(_iterations, [_row["best_makespan"] for _row in _history],
                      color=colors[1], linewidth=2, label="Best retained solution")
        _axes[0].axhline(search_result["baseline_metrics"]["makespan"], color="black",
                         linestyle="--", label="Exact rule to beat")
        _axes[0].set(ylabel="Project completion (days)", title="Simulated annealing progress")
        _axes[0].legend()
        _axes[1].plot(_iterations, [_row["temperature"] for _row in _history], color=colors[2])
        _axes[1].set(xlabel="Attempted move", ylabel="Temperature (days)", yscale="log")
        _fig.tight_layout()
        _output = _fig
    else:
        _output = mo.md("The safety cap stopped search before the first move; the baseline is retained.")
    _output
    return


@app.cell
def _(json, mo, results, schedule, start_order, notebook_ready):
    assert notebook_ready  # Real reactive dependency on interactive mode.
    _decision = {"schedule": [{"activity_id": _t, "start": schedule[_t][0], "completion": schedule[_t][1]} for _t in start_order]}
    mo.hstack([
        mo.download(results.write_csv().encode("utf-8"), filename="cpm_results.csv", label="Download CPM table"),
        mo.download(json.dumps(_decision, indent=2).encode("utf-8"), filename="decision.json", label="Download feasible schedule"),
    ], justify="start")
    return


if __name__ == "__main__":
    app.run()
