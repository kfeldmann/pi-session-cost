#!/usr/bin/env python3
"""
pi-session-cost.py - Report LLM API cost per project from Pi session files.

Usage:
    pi-session-cost.py [--days N] [--group-by-day]

Options:
    --days N          Only include cost data since N midnights ago (local time).
                      For example, --days 1 includes only today's data (since
                      the most recent midnight), --days 2 adds yesterday, etc.
                      Fractions are allowed; the cutoff is always rounded up
                      to the nearest midnight.  Omit to include all sessions.
    --group-by-day    Break the report down by calendar day (local time).  Each day
                      section lists per-project costs and a day subtotal.
                      Omit the flag (default) for the flat per-project summary.

Timestamps in Pi session files are stored in UTC and are converted to the
local system time zone before day bucketing and display.

Session files live in ~/.pi/agent/sessions/<dir-slug>/*.jsonl.
Cost is read from assistant message entries:
    entry.message.usage.cost.total  (USD)

The project name is taken from the `cwd` field in the session header.
Each assistant message entry carries an ISO 8601 timestamp which is used for
date filtering and day grouping.
"""

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set, Tuple


SESSIONS_DIR = os.path.expanduser("~/.pi/agent/sessions")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Report Pi session LLM costs per project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--days",
        metavar="N",
        type=float,
        default=None,
        help="Only include costs since N midnights ago (local time). Fractions round up to midnight.",
    )
    parser.add_argument(
        "--group-by-day",
        action="store_true",
        default=False,
        help="Group output by calendar day (local time) instead of showing a flat per-project summary.",
    )
    return parser.parse_args()


def cutoff_from_days(days: float) -> datetime:
    """Return the cutoff as a UTC datetime.

    Subtracts *days* from the current local time, then rounds *up* to the next
    local midnight so the cutoff always falls on a day boundary.

    Examples (assuming local time is 14:30 on 2026-06-28):
        days=1  →  2026-06-28 00:00 local  (most recent midnight / today only)
        days=2  →  2026-06-27 00:00 local  (today + yesterday)
        days=5  →  2026-06-24 00:00 local  (5 midnights ago)
    """
    now_local = datetime.now().astimezone()
    shifted = now_local - timedelta(days=days)
    # Ceiling to midnight: if not already exactly at 00:00:00.000000,
    # advance to the start of the following day.
    at_midnight = shifted.replace(hour=0, minute=0, second=0, microsecond=0)
    if shifted != at_midnight:
        d = shifted.date() + timedelta(days=1)
    else:
        d = shifted.date()
    # Reconstruct as a naive local datetime then re-attach the local timezone;
    # using datetime().astimezone() correctly handles DST transitions.
    local_midnight = datetime(d.year, d.month, d.day).astimezone()
    return local_midnight.astimezone(timezone.utc)


def iter_session_files():
    """Yield all *.jsonl session file paths."""
    pattern = os.path.join(SESSIONS_DIR, "*", "*.jsonl")
    yield from sorted(glob.glob(pattern))


def read_session_costs(
    path: str, cutoff: Optional[datetime]
) -> Tuple[Optional[str], Dict[str, float], Set[str]]:
    """
    Parse a single session JSONL file.

    Returns (project_cwd, day_costs, active_days) where:
    - project_cwd  – from the session header's ``cwd`` field
    - day_costs    – dict mapping date string "YYYY-MM-DD" → sum of costs for
                     assistant messages on that day
    - active_days  – set of date strings on which at least one assistant
                     message fell within the time window

    Entries before ``cutoff`` are excluded when cutoff is set.
    Entries whose timestamp cannot be parsed are bucketed under "unknown".
    """
    project_cwd = None
    day_costs: Dict[str, float] = defaultdict(float)
    active_days: Set[str] = set()

    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type")

                # First line is always the session header
                if entry_type == "session":
                    project_cwd = entry.get("cwd") or project_cwd
                    continue

                if entry_type != "message":
                    continue

                message = entry.get("message", {})
                if message.get("role") != "assistant":
                    continue

                # Parse timestamp for both cutoff filtering and day bucketing
                date_str = "unknown"
                raw_ts = entry.get("timestamp")
                if raw_ts:
                    try:
                        # Python 3.11+ handles 'Z'; earlier versions need replacement
                        ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                        if cutoff is not None and ts < cutoff:
                            continue
                        local_ts = ts.astimezone()
                        date_str = local_ts.strftime("%Y-%m-%d")
                    except ValueError:
                        pass  # unparseable timestamp → include, bucket as "unknown"
                elif cutoff is not None:
                    # No timestamp and a cutoff is active → skip to be safe
                    continue

                active_days.add(date_str)
                cost = (
                    message.get("usage", {})
                    .get("cost", {})
                    .get("total", 0.0)
                )
                day_costs[date_str] += cost or 0.0

    except OSError as exc:
        print(f"Warning: could not read {path}: {exc}", file=sys.stderr)

    return project_cwd, dict(day_costs), active_days


def format_cost(amount: float) -> str:
    """Format a dollar amount. Show more decimals for small values."""
    if amount == 0:
        return "$0.00"
    if amount < 0.0001:
        return f"${amount:.8f}"
    if amount < 0.01:
        return f"${amount:.6f}"
    return f"${amount:.4f}"


def build_header(args, cutoff: Optional[datetime]) -> str:
    if cutoff:
        return (
            f"Pi Session Cost Report  "
            f"(last {args.days:g} day{'s' if args.days != 1 else ''}  |  "
            f"since {cutoff.astimezone().strftime('%Y-%m-%d %H:%M %Z')})"
        )
    return "Pi Session Cost Report  (all time)"


# ---------------------------------------------------------------------------
# Flat per-project output
# ---------------------------------------------------------------------------

def print_flat(
    project_costs: Dict[str, float],
    project_sessions: Dict[str, int],
    cutoff: Optional[datetime],
    args,
) -> None:
    active_projects = (
        [p for p in project_costs if project_sessions.get(p, 0) > 0]
        if cutoff is not None
        else list(project_costs.keys())
    )
    sorted_projects = sorted(active_projects, key=lambda p: (-project_costs[p], p))

    grand_total = sum(project_costs[p] for p in sorted_projects)

    cost_col_width = (
        max(len(format_cost(project_costs[p])) for p in sorted_projects)
        if sorted_projects else 6
    )
    cost_col_width = max(cost_col_width, len("COST"))
    project_col_width = max((len(p) for p in sorted_projects), default=10)
    project_col_width = max(project_col_width, len("PROJECT"))

    sep = "-" * (project_col_width + cost_col_width + 12)

    print()
    print(build_header(args, cutoff))
    print(sep)
    print(f"{'PROJECT':<{project_col_width}}  {'SESSIONS':>8}  {'COST':>{cost_col_width}}")
    print(sep)

    for proj in sorted_projects:
        cost_str = format_cost(project_costs[proj])
        sessions = project_sessions[proj]
        print(f"{proj:<{project_col_width}}  {sessions:>8}  {cost_str:>{cost_col_width}}")

    print(sep)
    grand_str = format_cost(grand_total)
    total_sessions = sum(project_sessions[p] for p in sorted_projects)
    print(f"{'TOTAL':<{project_col_width}}  {total_sessions:>8}  {grand_str:>{cost_col_width}}")
    print()


# ---------------------------------------------------------------------------
# Day-grouped output
# ---------------------------------------------------------------------------

def print_grouped_by_day(
    day_project_costs: Dict[str, Dict[str, float]],
    day_project_sessions: Dict[str, Dict[str, int]],
    cutoff: Optional[datetime],
    args,
) -> None:
    """Print costs broken down by calendar day, then by project within each day."""

    all_projects = {
        proj
        for day_data in day_project_costs.values()
        for proj in day_data
    }

    # Column widths across the entire report for alignment consistency
    all_costs = [
        cost
        for day_data in day_project_costs.values()
        for cost in day_data.values()
    ]
    day_totals = [sum(d.values()) for d in day_project_costs.values()]
    grand_total = sum(day_totals)

    cost_col_width = (
        max(len(format_cost(c)) for c in all_costs + day_totals + [grand_total])
        if all_costs else 6
    )
    cost_col_width = max(cost_col_width, len("COST"))
    project_col_width = max((len(p) for p in all_projects), default=10)
    project_col_width = max(project_col_width, len("PROJECT"), len("DAY TOTAL"), len("GRAND TOTAL"))

    sep = "-" * (project_col_width + cost_col_width + 12)

    print()
    print(build_header(args, cutoff))

    total_sessions_all = 0

    for day in sorted(day_project_costs.keys(), reverse=True):
        day_data = day_project_costs[day]
        day_sess = day_project_sessions[day]

        sorted_projects = sorted(day_data.keys(), key=lambda p: (-day_data[p], p))
        day_total = sum(day_data.values())
        day_session_total = sum(day_sess.values())
        total_sessions_all += day_session_total

        print(sep)
        print(f"  {day}")
        print(sep)
        print(f"  {'PROJECT':<{project_col_width}}  {'SESSIONS':>8}  {'COST':>{cost_col_width}}")
        print(sep)

        for proj in sorted_projects:
            cost_str = format_cost(day_data[proj])
            sessions = day_sess.get(proj, 0)
            print(f"  {proj:<{project_col_width}}  {sessions:>8}  {cost_str:>{cost_col_width}}")

        print(sep)
        day_total_str = format_cost(day_total)
        print(f"  {'DAY TOTAL':<{project_col_width}}  {day_session_total:>8}  {day_total_str:>{cost_col_width}}")

    print(sep)
    grand_str = format_cost(grand_total)
    print(f"  {'GRAND TOTAL':<{project_col_width}}  {total_sessions_all:>8}  {grand_str:>{cost_col_width}}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    cutoff = cutoff_from_days(args.days) if args.days is not None else None

    files = list(iter_session_files())
    if not files:
        print(f"No session files found under {SESSIONS_DIR}", file=sys.stderr)
        sys.exit(1)

    if args.group_by_day:
        # day -> project -> cost / session-count
        day_project_costs: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        day_project_sessions: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for path in files:
            cwd, day_costs, active_days = read_session_costs(path, cutoff)
            if cwd is None:
                cwd = os.path.basename(os.path.dirname(path))
            for day, cost in day_costs.items():
                day_project_costs[day][cwd] += cost
            for day in active_days:
                day_project_sessions[day][cwd] += 1

        if not day_project_costs:
            print("No cost data found for the specified period.", file=sys.stderr)
            sys.exit(1)

        print_grouped_by_day(
            {k: dict(v) for k, v in day_project_costs.items()},
            {k: dict(v) for k, v in day_project_sessions.items()},
            cutoff,
            args,
        )

    else:
        # project -> total cost / session-count
        project_costs: Dict[str, float] = defaultdict(float)
        project_sessions: Dict[str, int] = defaultdict(int)

        for path in files:
            cwd, day_costs, active_days = read_session_costs(path, cutoff)
            if cwd is None:
                cwd = os.path.basename(os.path.dirname(path))
            project_costs[cwd] += sum(day_costs.values())
            if active_days:
                project_sessions[cwd] += 1

        print_flat(dict(project_costs), dict(project_sessions), cutoff, args)


if __name__ == "__main__":
    main()
