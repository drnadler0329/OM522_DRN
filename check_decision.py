# One independent checker, usable in the terminal and Marimo.
# pixi run python check_decision.py --case development_small --decision cpm_output/decision.json
# pixi run marimo edit check_decision.py
import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


with app.setup:
    import marimo as mo

    import argparse
    import csv
    import heapq
    import json
    import math
    import os
    import re
    import tempfile
    import time
    from decimal import Decimal, localcontext
    from pathlib import Path

    TOLERANCE = Decimal("1e-8")
    IDENTITY = "option4_standalone_checker"
    CASES = ("hand_checkable", "development_small", "development_medium", "development_large", "edge_case")


    class InputError(ValueError):
        pass


    def read_table(folder, filename, columns, nullable=()):
        """Published CSV schema, independent of any solver's loader or schema file."""
        path = folder / filename
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                headers = reader.fieldnames or []
                if len(headers) != len(set(headers)):
                    raise InputError(f"{filename}: duplicate column headers")
                missing = set(columns) - set(headers)
                if missing:
                    raise InputError(f"{filename}: missing columns {sorted(missing)}")
                for row in reader:
                    label = f"{filename}, file line {reader.line_num}"
                    if None in row:
                        raise InputError(f"{label}: too many fields")
                    values = {}
                    for column, kind in columns.items():
                        value = row.get(column)
                        location = f"{label}, column {column}"
                        if value is None:
                            raise InputError(f"{location}: missing CSV field")
                        if not value.strip():
                            if column in nullable:
                                values[column] = None
                                continue
                            raise InputError(f"{location}: required value is empty")
                        if kind is int:
                            if not re.fullmatch(r"[+-]?\d+", value.strip()):
                                raise InputError(f"{location}: expected an integer, got {value!r}")
                            try:
                                values[column] = int(value)
                            except ValueError as error:
                                raise InputError(f"{location}: integer is outside the parser's supported size") from error
                        else:
                            values[column] = value  # Match identifiers by exact value.
                    yield label, values
        except (OSError, UnicodeError, csv.Error) as error:
            raise InputError(f"{filename}: {error}") from error


    def load_inputs(folder):
        """Return validated tasks, capacities and precedence-only project duration."""
        folder = Path(folder)
        tasks, capacities = {}, {}
        for label, row in read_table(folder, "activities.csv", {
            "activity_id": str, "duration": int, "milestone_due": int,
            "priority_weight": int, "locked_start": int,
        }, nullable=("milestone_due", "locked_start")):
            task = row["activity_id"]
            if task in tasks:
                raise InputError(f"{label}, column activity_id: duplicate ID {task!r}")
            for column in ("duration", "priority_weight"):
                if row[column] <= 0:
                    raise InputError(f"{label}, column {column}: must be positive")
            if row["milestone_due"] is not None and row["milestone_due"] < 0:
                raise InputError(f"{label}, column milestone_due: must be nonnegative")
            if row["locked_start"] is not None:
                raise InputError(f"{label}, column locked_start: outside the required model; expected an empty value")
            tasks[task] = dict(duration=row["duration"], due=row["milestone_due"],
                               weight=row["priority_weight"], requirements={}, predecessors=set(), successors=set())
        for label, row in read_table(folder, "resource_capacities.csv", {"resource_id": str, "capacity": int}):
            resource = row["resource_id"]
            if resource in capacities:
                raise InputError(f"{label}, column resource_id: duplicate ID {resource!r}")
            if row["capacity"] <= 0:
                raise InputError(f"{label}, column capacity: must be positive")
            capacities[resource] = row["capacity"]
        for label, row in read_table(folder, "precedence.csv", {"activity_id": str, "predecessor_id": str}):
            task, predecessor = row["activity_id"], row["predecessor_id"]
            for column in ("activity_id", "predecessor_id"):
                if row[column] not in tasks:
                    raise InputError(f"{label}, column {column}: unknown ID {row[column]!r}")
            tasks[task]["predecessors"].add(predecessor)
            tasks[predecessor]["successors"].add(task)
        for label, row in read_table(folder, "resource_requirements.csv", {
            "activity_id": str, "resource_id": str, "requirement": int,
        }):
            task, resource = row["activity_id"], row["resource_id"]
            if task not in tasks:
                raise InputError(f"{label}, column activity_id: unknown ID {task!r}")
            if resource not in capacities:
                raise InputError(f"{label}, column resource_id: unknown ID {resource!r}")
            if resource in tasks[task]["requirements"]:
                raise InputError(f"{label}, columns activity_id/resource_id: repeated pair {task}/{resource}")
            if row["requirement"] < 0:
                raise InputError(f"{label}, column requirement: must be nonnegative")
            tasks[task]["requirements"][resource] = row["requirement"]
        for task, data in tasks.items():
            missing = set(capacities) - set(data["requirements"])
            if missing:
                raise InputError(f"resource_requirements.csv, columns activity_id/resource_id: missing pairs for {task}: {sorted(missing)}")
        # Independent longest-path calculation for the CPM lower bound. No ordering
        # or schedule construction function from either method is used here.
        indegree = {t: len(d["predecessors"]) for t, d in tasks.items()}
        ready = [t for t in tasks if not indegree[t]]
        heapq.heapify(ready)
        earliest_finish = {}
        while ready:
            task = heapq.heappop(ready)
            earliest_finish[task] = tasks[task]["duration"] + max(
                (earliest_finish[p] for p in tasks[task]["predecessors"]), default=0)
            for successor in sorted(tasks[task]["successors"]):
                indegree[successor] -= 1
                if not indegree[successor]:
                    heapq.heappush(ready, successor)
        if len(earliest_finish) != len(tasks):
            raise InputError("precedence.csv, columns activity_id/predecessor_id: cyclic precedence")
        return tasks, capacities, max(earliest_finish.values(), default=0)


    def _reject_constant(value):
        raise ValueError(f"nonstandard nonfinite JSON number {value}")


    def _unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key {key!r}")
            result[key] = value
        return result


    def read_decision(path):
        with Path(path).open(encoding="utf-8-sig") as stream:
            return json.load(stream, parse_float=Decimal, parse_constant=_reject_constant,
                             object_pairs_hook=_unique_keys)


    def _numeric(value):
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            return None
        result = Decimal(value)
        return result if result.is_finite() else None


    def _precision(decision, tasks):
        # Preserve differences even for very large integer days or fine decimals.
        span = max((len(str(d["duration"])) for d in tasks.values()), default=1)
        if isinstance(decision, dict) and isinstance(decision.get("schedule"), list):
            for entry in decision["schedule"]:
                if isinstance(entry, dict):
                    for key in ("start", "completion"):
                        value = _numeric(entry.get(key))
                        if value is not None and value:
                            span = max(span, abs(value.adjusted()) + len(value.as_tuple().digits))
        return max(50, 2 * span + 40)


    def validate_schedule(tasks, capacities, lower_bound, decision):
        """Verify supplied times, without repairing, rescheduling or rounding them."""
        errors, schedule, seen = [], {}, set()

        def fail(code, message, **details):
            errors.append(dict(code=code, message=message, **details))

        if not isinstance(decision, dict) or not isinstance(decision.get("schedule"), list):
            fail("decision_schema", "decision.json must contain a top-level schedule list")
            return errors, None, None
        for index, entry in enumerate(decision["schedule"], 1):
            if not isinstance(entry, dict):
                fail("entry_schema", f"Schedule entry {index} must be an object", entry=index)
                continue
            task = entry.get("activity_id")
            if not isinstance(task, str):
                fail("activity_id_type", f"Entry {index}: activity_id must be a string", entry=index)
                continue
            if task not in tasks:
                fail("unknown_activity", f"Entry {index}: unknown activity {task!r}", entry=index, activity_id=task)
                continue
            if task in seen:
                fail("duplicate_activity", f"Activity {task} appears more than once", entry=index, activity_id=task)
                continue
            seen.add(task)
            start, finish = _numeric(entry.get("start")), _numeric(entry.get("completion"))
            if start is None or finish is None:
                fail("time_type", f"Activity {task}: start and completion must be finite JSON numbers", entry=index, activity_id=task)
                continue
            schedule[task] = (start, finish)
            if start < 0:
                fail("negative_start", f"Activity {task}: negative start {start}", activity_id=task, start=start)
            if abs(start - start.to_integral_value()) > TOLERANCE:
                fail("fractional_start", f"Activity {task}: start {start} is not a whole working day", activity_id=task, start=start)
            if finish <= start:
                fail("nonpositive_interval", f"Activity {task}: completion must be after start", activity_id=task)
            if abs((finish - start) - tasks[task]["duration"]) > TOLERANCE:
                fail("duration", f"Activity {task}: interval length {finish-start} differs from duration {tasks[task]['duration']}",
                     activity_id=task, start=start, completion=finish, expected_duration=tasks[task]["duration"])
        missing = sorted(set(tasks) - seen)
        if missing:
            fail("missing_activities", f"Missing activities: {', '.join(missing)}", activity_ids=missing)
        for task, (start, finish) in sorted(schedule.items()):
            for predecessor in sorted(tasks[task]["predecessors"]):
                if predecessor in schedule and schedule[predecessor][1] > start + TOLERANCE:
                    fail("precedence", f"Activity {task} starts before predecessor {predecessor} completes",
                         activity_id=task, predecessor_id=predecessor, start=start,
                         predecessor_completion=schedule[predecessor][1])
        # Direct interval checks at every event, independent of a method's
        # resource-gap search. Finishing activities are excluded at the boundary.
        events = sorted({time for pair in schedule.values() for time in pair})
        peaks = {r: 0 for r in capacities}
        for index, instant in enumerate(events[:-1]):
            active = sorted(t for t, (start, finish) in schedule.items() if start <= instant < finish)
            for resource, capacity in sorted(capacities.items()):
                demand = sum(tasks[t]["requirements"][resource] for t in active)
                peaks[resource] = max(peaks[resource], demand)
                if demand > capacity:
                    fail("resource_capacity", f"{resource} usage {demand} exceeds capacity {capacity} on [{instant}, {events[index+1]})",
                         resource_id=resource, interval_start=instant, interval_end=events[index+1],
                         usage=demand, capacity=capacity, active_activities=active)
        if errors:
            return errors, None, peaks
        completion = max((finish for start, finish in schedule.values()), default=Decimal(0))
        milestones = [(tasks[t], finish) for t, (start, finish) in schedule.items() if tasks[t]["due"] is not None]
        measures = dict(makespan=completion, activities_completed=len(schedule),
                        weighted_milestone_delay=sum((d["weight"] * max(Decimal(0), finish-d["due"])
                                                     for d, finish in milestones), Decimal(0)),
                        late_milestones=sum(finish > d["due"] + TOLERANCE for d, finish in milestones),
                        milestones=len(milestones), cpm_duration=lower_bound, cpm_gap=completion-lower_bound)
        return errors, measures, peaks


    def check(input_dir, decision_path):
        """Return a serializable report and exit code; inputs and decisions are read-only."""
        started = time.perf_counter()
        input_dir, decision_path = Path(input_dir).resolve(), Path(decision_path).resolve()
        report = dict(checker=IDENTITY, input_dir=str(input_dir), decision_file=str(decision_path),
                      tolerance=str(TOLERANCE), status="", feasible=False, errors=[], metrics=None)
        try:
            tasks, capacities, lower_bound = load_inputs(input_dir)
        except (InputError, OSError) as error:
            report.update(status="invalid_input", errors=[dict(code="input", message=str(error))])
            code = 2
        else:
            evidence = [dict(code="impossible_requirement", activity_id=t, resource_id=r,
                             message=f"Activity {t} requires {demand} {r}; only {capacities[r]} available",
                             requirement=demand, capacity=capacities[r])
                        for t, d in sorted(tasks.items()) for r, demand in sorted(d["requirements"].items())
                        if demand > capacities[r]]
            if evidence:
                report.update(status="infeasible_input", errors=evidence)
                code = 3
            else:
                try:
                    decision = read_decision(decision_path)
                except (ValueError, OSError, UnicodeError) as error:
                    report.update(status="invalid_decision", errors=[dict(code="decision_file", message=f"{decision_path}: {error}")])
                    code = 1
                else:
                    with localcontext() as context:
                        context.prec = _precision(decision, tasks)
                        errors, measures, peaks = validate_schedule(tasks, capacities, lower_bound, decision)
                    report.update(status="invalid_decision" if errors else "valid", feasible=not errors,
                                  errors=errors, metrics=measures, resource_peaks=peaks)
                    code = 1 if errors else 0
        report["checker_seconds"] = time.perf_counter() - started
        return report, code


    def _json_number(value):
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            converted = float(value)
            return converted if math.isfinite(converted) else str(value)
        raise TypeError(f"Cannot serialize {type(value).__name__}")


    def report_json(report):
        return json.dumps(report, indent=2, default=_json_number, allow_nan=False) + "\n"


    def save_report(path, report, input_dir, decision_path):
        """Protect source inputs/decisions and avoid overwriting unrelated files."""
        path = Path(path).resolve()
        if path.suffix.lower() != ".json":
            raise ValueError("--report must name a .json file")
        if path.is_relative_to(Path(input_dir).resolve()) or path == Path(decision_path).resolve():
            raise ValueError("--report must be separate from the input directory and decision file")
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                previous = None
            if not isinstance(previous, dict) or previous.get("checker") != IDENTITY:
                raise ValueError(f"Will not overwrite an unrelated file: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp", dir=path.parent,
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(report_json(report))
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


    def main(argv=None):
        parser = argparse.ArgumentParser(description="Independent Option 4 decision checker; also opens with marimo edit check_decision.py")
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--case", choices=CASES, help="Case under the data directory beside this checker")
        source.add_argument("--input-dir", type=Path, help="Any instance directory; no schema.json needed")
        parser.add_argument("--decision", "--decision-file", dest="decision", type=Path, required=True)
        parser.add_argument("--report", type=Path, help="Optional JSON check report; leaves the decision untouched")
        parser.add_argument("--json", action="store_true", help="Print the full report as JSON instead of readable text")
        args = parser.parse_args(argv)
        input_dir = args.input_dir or Path(__file__).resolve().parent / "option_04_resource_constrained_project_scheduling" / args.case
        report, code = check(input_dir, args.decision)
        if args.report:
            try:
                save_report(args.report, report, input_dir, args.decision)
            except (ValueError, OSError) as error:
                parser.exit(2, f"Report error: {error}\n")
        if args.json:
            print(report_json(report), end="")
        else:
            print(f"Check status: {report['status']}")
            print(f"Inputs: {report['input_dir']}")
            print(f"Decision: {report['decision_file']}")
            if report["feasible"]:
                measures = report["metrics"]
                print("PASS: activity coverage, whole-day starts, durations, precedence and resources")
                for key in ("makespan", "weighted_milestone_delay", "late_milestones", "cpm_duration", "cpm_gap", "activities_completed"):
                    print(f"{key}: {measures[key]}")
            else:
                for error in report["errors"][:20]:
                    print(f"FAIL [{error['code']}]: {error['message']}")
                if len(report["errors"]) > 20:
                    print(f"{len(report['errors']) - 20} additional violations; use --json or --report for all details")
            print(f"Checker time: {report['checker_seconds']:.6f} seconds (not scheduling runtime)")
            if args.report:
                print(f"Report: {args.report.resolve()}")
        return code


    CHECKER_ROOT = Path(__file__).resolve().parent

    if __name__ == "__main__" and not mo.running_in_notebook():
        raise SystemExit(main())


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Independent project schedule checker

    Select the **same case used to create the decision**, enter its file path,
    and click **Check decision**. This program reads the original CSV inputs and
    the exported decision.json. It does not run or import either scheduling method.

    The baseline output is usually `cpm_output/decision.json`; annealing output is
    usually `simulated_annealing_output/decision.json`. Relative paths below are
    resolved from the folder containing this checker. An optional custom input
    folder overrides the selected case. Re-submit to check a regenerated file.
    """)
    return


@app.cell
def checker_form(CASES, mo):
    configuration = mo.ui.dictionary({
        "case": mo.ui.dropdown(options=list(CASES), value="hand_checkable", label="Input case"),
        "input_dir": mo.ui.text(value="", label="Custom input folder (optional)", full_width=True),
        "decision": mo.ui.text(value="option_04_resource_constrained_project_scheduling/worked-example/solution.json", label="Decision file path", full_width=True),
    }).form(submit_button_label="Check decision")
    configuration
    return (configuration,)


@app.cell
def run_check(CHECKER_ROOT, Path, check, configuration, mo):
    mo.stop(configuration.value is None, mo.md("Choose the inputs above and click **Check decision**."))
    _values = configuration.value
    _custom_input = _values["input_dir"].strip()
    _decision_text = _values["decision"].strip()
    mo.stop(not _decision_text, mo.md("Enter a decision file path before checking."))
    selected_input = (Path(_custom_input).expanduser() if _custom_input else
                      CHECKER_ROOT / "option_04_resource_constrained_project_scheduling" / _values["case"])
    if not selected_input.is_absolute():
        selected_input = CHECKER_ROOT / selected_input
    selected_decision = Path(_decision_text).expanduser()
    if not selected_decision.is_absolute():
        selected_decision = CHECKER_ROOT / selected_decision
    check_report, check_exit_code = check(selected_input, selected_decision)
    return check_exit_code, check_report, selected_decision, selected_input


@app.cell(hide_code=True)
def _(check_exit_code, check_report, mo):
    _passed = check_report["feasible"]
    _heading = "PASS" if _passed else "CHECK FAILED"
    _explanation = ("The decision satisfies activity coverage, whole-day starts, durations, precedence and resource capacities."
                    if _passed else "Review the problems listed below. No accepted objective measures are reported for an invalid decision.")
    mo.md(f"""
    ## {_heading}

    **Status:** {check_report['status']}  
    **Terminal exit code:** {check_exit_code}

    {_explanation}

    **Inputs:** `{check_report['input_dir']}`  
    **Decision:** `{check_report['decision_file']}`

    Checker time: {check_report['checker_seconds']:.6f} seconds. This is validation
    time, not the scheduling method's computation time.
    """)
    return


@app.cell
def _(check_report, mo):
    if check_report["metrics"] is not None:
        _labels = {
            "makespan": "Project completion in working days",
            "activities_completed": "Activities completed",
            "weighted_milestone_delay": "Weighted milestone delay",
            "late_milestones": "Late milestones",
            "milestones": "Milestones",
            "cpm_duration": "CPM duration in working days",
            "cpm_gap": "Gap above CPM duration in working days",
        }
        _display = mo.vstack([
            mo.md("## Independently recomputed measures"),
            mo.ui.table([{"Measure": _labels[_key], "Value": str(_value)}
                         for _key, _value in check_report["metrics"].items()], selection=None),
            mo.md("### Peak resource use"),
            mo.ui.table([{"Resource": _resource, "Peak usage": _peak}
                         for _resource, _peak in check_report["resource_peaks"].items()], selection=None),
            mo.md("Milestone dates are soft targets. Lateness contributes to the measures but does not by itself make a schedule infeasible."),
        ])
    else:
        _display = mo.vstack([
            mo.md(f"## Violations and errors ({len(check_report['errors'])})"),
            mo.ui.table([{"Check": _error["code"], "Details": _error["message"]}
                         for _error in check_report["errors"]], selection=None, page_size=20),
            mo.md("The downloadable report includes all diagnostic fields, such as activity IDs, resources and offending intervals."),
        ])
    _display
    return


@app.cell
def _(check_report, mo, report_json):
    _json = report_json(check_report)
    mo.vstack([
        mo.download(_json.encode("utf-8"), filename="check_report.json", label="Download checker report"),
        mo.accordion({"Full report": mo.md("```json\n" + _json + "\n```")}),
    ])
    return


if __name__ == "__main__":
    app.run()
