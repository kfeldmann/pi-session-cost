# pi-session-cost

A command-line script that reports LLM API costs per project from [Pi](https://pi.dev/) coding-agent session files.

## Overview

Pi stores session data as JSONL files under `~/.pi/agent/sessions/`. Each assistant message in those files contains a `usage.cost.total` field (in USD). `pi-session-cost.py` aggregates those costs across all sessions and presents a tidy, column-aligned report — either as a flat per-project summary or broken down by calendar day.

## Requirements

- Python 3.7+
- No third-party dependencies (standard library only)

## Installation

Copy `pi-session-cost.py` anywhere on your `$PATH` and make it executable:

```bash
chmod +x pi-session-cost.py
```

Or run it directly with Python:

```bash
python3 pi-session-cost.py
```

## Usage

```
pi-session-cost.py [--days N] [--group-by-day]
```

### Options

| Option | Description |
|---|---|
| `--days N` | Only include costs since `N` midnights ago (local time). Fractions are allowed; the cutoff is always rounded up to the nearest midnight. Omit to include all sessions. |
| `--group-by-day` | Break the report down by calendar day. Each day section lists per-project costs and a day subtotal. Default is a flat per-project summary. |

### Examples

**All-time costs, summarised per project:**
```
$ pi-session-cost.py

Pi Session Cost Report  (all time)
------------------------------------------------------
PROJECT                            SESSIONS       COST
------------------------------------------------------
/home/alice/code/my-web-app               8    $1.2340
/home/alice/code/api-service              3    $0.4510
/home/alice/code/data-pipeline            1    $0.0892
------------------------------------------------------
TOTAL                                    12    $1.7742
```

**Costs for today only (`--days 1`):**
```
$ pi-session-cost.py --days 1

Pi Session Cost Report  (last 1 day  |  since 2026-06-29 00:00 EDT)
------------------------------------------------------
...
```

**Costs for the last 7 days, grouped by day:**
```
$ pi-session-cost.py --days 7 --group-by-day

Pi Session Cost Report  (last 7 days  |  since 2026-06-23 00:00 EDT)
------------------------------------------------------
  2026-06-29
------------------------------------------------------
  PROJECT                          SESSIONS       COST
------------------------------------------------------
  /home/alice/code/my-web-app             2    $0.3100
------------------------------------------------------
  DAY TOTAL                               2    $0.3100
------------------------------------------------------
  ...
------------------------------------------------------
  GRAND TOTAL                            12    $1.7742
```

### `--days` cutoff behaviour

The cutoff is computed by subtracting `N` days from the current local time, then rounding **up** to the nearest midnight. This means:

| Flag | Includes |
|---|---|
| `--days 1` | Today only (since the most recent midnight) |
| `--days 2` | Today + yesterday |
| `--days 7` | The last 7 calendar days |

## How It Works

1. **Session discovery** – all `*.jsonl` files under `~/.pi/agent/sessions/**/` are scanned.
2. **Project identification** – the `cwd` field in each session's header entry is used as the project name.
3. **Cost extraction** – only `message` entries with `role: assistant` are read. The cost is taken from `entry.message.usage.cost.total` (USD).
4. **Timestamps** – entry timestamps are stored in UTC and converted to the local system time zone for date filtering and day bucketing.
5. **Session counting** – a session file contributes one session count per project, per day on which at least one qualifying assistant message appears.

## Output Formatting

- Costs are right-aligned and formatted with enough decimal places to show significant figures:
  - `≥ $0.01` → 4 decimal places (`$1.2340`)
  - `≥ $0.0001` → 6 decimal places (`$0.000123`)
  - `< $0.0001` → 8 decimal places (`$0.00000001`)
- Projects are sorted by total cost (descending), with ties broken alphabetically.
- Days are sorted in reverse-chronological order (newest first).
