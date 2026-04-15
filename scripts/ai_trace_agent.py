#!/usr/bin/env python3
"""
ai_trace_agent.py — Let Claude autonomously analyze a Perfetto trace.

This script creates an agentic loop where Claude:
  1. Explores the trace schema
  2. Decides what SQL queries to run
  3. Runs them via trace_processor
  4. Iterates until it has a complete analysis
  5. Outputs a final report

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    pip install anthropic perfetto pandas
    python3 ai_trace_agent.py <trace_file> [--focus "cold startup"] [--output report.md]

Example:
    python3 ai_trace_agent.py slowstart.perfetto-trace --focus "app cold startup"
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("❌ pip install anthropic")
    sys.exit(1)

try:
    from perfetto.trace_processor import TraceProcessor
except ImportError:
    print("❌ pip install perfetto")
    sys.exit(1)


SYSTEM_PROMPT = """You are an expert Android performance engineer analyzing a Perfetto trace.

You have access to a Perfetto trace_processor SQL interface. To run a query,
write it inside <sql>...</sql> tags. You'll receive the results, then you can
run more queries or give your final analysis.

## Key Tables
| Table | Description |
|-------|-------------|
| slice | Time intervals: function calls, atrace sections |
| thread_track | Links slices → threads |
| thread | utid, tid, name, is_main_thread, upid |
| process | upid, pid, name |
| counter | Sampled values (CPU freq, memory, etc.) |
| counter_track | Links counters to tracks |
| sched_slice | CPU scheduling: which thread ran where |
| metadata | Trace metadata (device, OS, etc.) |

## Key Concepts
- Timestamps are in **nanoseconds**. Divide by 1e6 for ms, 1e9 for seconds.
- Use `utid` / `upid` (unique IDs), NOT `tid` / `pid` (can be reused).
- Join pattern: slice → thread_track → thread → process

## Standard Library Modules (use INCLUDE PERFETTO MODULE ...)
- android.startup.startups → android_startups table
- android.binder → android_binder_txns table
- android.monitor_contention → android_monitor_contention table
- android.frames.timeline → expected/actual_frame_timeline_slice

## Your Workflow
1. Start by exploring: what processes exist? what app are we looking at?
2. Check startup lifecycle slices (bindApplication, activityStart, etc.)
3. Find the slowest slices on the main thread
4. Look for specific bottlenecks: binder, GC, I/O, lock contention
5. Summarize findings with root causes and actionable fixes

## Rules
- Run ONE focused query at a time (max 2 per message)
- Keep LIMIT reasonable (50-100 rows max)
- When done, output your final analysis WITHOUT any <sql> tags
- In your final analysis, list bottlenecks ranked by impact (ms saved)
- Include specific code-level fix suggestions for each bottleneck
"""


def run_query(tp, sql):
    """Execute a SQL query against the trace and return formatted results."""
    try:
        df = tp.query(sql).as_pandas_dataframe()
        if df.empty:
            return "(no results)"
        # Truncate for context window management
        if len(df) > 100:
            return df.head(100).to_string() + f"\n... ({len(df)} total rows, showing first 100)"
        return df.to_string()
    except Exception as e:
        return f"SQL ERROR: {e}"


def run_agent(trace_path, focus, output_path, max_iterations=12, model="claude-sonnet-4-20250514"):
    """Run the agentic trace analysis loop."""

    print(f"📂 Loading trace: {trace_path}")
    tp = TraceProcessor(trace=str(trace_path))

    print(f"🤖 Starting AI analysis (model: {model})")
    print(f"   Focus: {focus}")
    print(f"   Max iterations: {max_iterations}")
    print("=" * 70)

    client = anthropic.Anthropic()

    user_msg = f"""Analyze this Android app's Perfetto trace.

Focus area: {focus}

Start by exploring the trace — find the app process, then systematically
investigate the startup performance. I want:
1. A ranked list of bottlenecks with exact durations
2. Root cause for each
3. Specific code-level fixes

Begin exploring."""

    messages = [{"role": "user", "content": user_msg}]
    final_text = ""

    for i in range(max_iterations):
        print(f"\n{'─' * 70}")
        print(f"  ITERATION {i + 1}/{max_iterations}")
        print(f"{'─' * 70}")

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        text = response.content[0].text
        final_text = text

        # Extract SQL queries from response
        sql_blocks = re.findall(r"<sql>(.*?)</sql>", text, re.DOTALL)

        # Print Claude's reasoning (abbreviated)
        display = text
        for sql in sql_blocks:
            display = display.replace(f"<sql>{sql}</sql>", f"\n  📊 [SQL query]\n")
        # Show first 500 chars of reasoning
        reasoning_lines = [l for l in display.strip().split("\n") if l.strip()]
        for line in reasoning_lines[:15]:
            print(f"  🤖 {line}")
        if len(reasoning_lines) > 15:
            print(f"  🤖 ... ({len(reasoning_lines) - 15} more lines)")

        if not sql_blocks:
            print("\n  ✅ Claude finished analysis (no more SQL queries)")
            break

        # Execute each SQL query
        results = []
        for idx, sql in enumerate(sql_blocks):
            clean_sql = sql.strip()
            print(f"\n  ⚡ Running query {idx + 1}:")
            # Show first line of SQL
            first_line = clean_sql.split("\n")[0][:80]
            print(f"     {first_line}...")

            result = run_query(tp, clean_sql)

            # Show abbreviated result
            result_lines = result.split("\n")
            for line in result_lines[:5]:
                print(f"     → {line}")
            if len(result_lines) > 5:
                print(f"     → ... ({len(result_lines) - 5} more rows)")

            results.append(f"Query:\n{clean_sql}\n\nResult:\n{result}")

        # Feed results back to Claude
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "\n\n---\n\n".join(results)})

    else:
        print(f"\n  ⚠️  Reached max iterations ({max_iterations})")

    # Save final report
    output = Path(output_path)
    with open(output, "w") as f:
        f.write(f"# Perfetto Trace Analysis Report\n\n")
        f.write(f"**Trace:** {trace_path}\n")
        f.write(f"**Focus:** {focus}\n")
        f.write(f"**Model:** {model}\n")
        f.write(f"**Iterations:** {i + 1}\n\n")
        f.write("---\n\n")
        f.write(final_text)

    print(f"\n{'=' * 70}")
    print(f"📄 Report saved to: {output}")
    print(f"   Size: {output.stat().st_size / 1024:.1f} KB")
    print(f"{'=' * 70}")

    return final_text


def main():
    parser = argparse.ArgumentParser(
        description="AI-powered Perfetto trace analysis"
    )
    parser.add_argument("trace", help="Path to .perfetto-trace file")
    parser.add_argument(
        "--focus", "-f",
        default="app cold startup performance",
        help="Analysis focus area (default: app cold startup)"
    )
    parser.add_argument(
        "--output", "-o",
        default="ai_trace_report.md",
        help="Output report path (default: ai_trace_report.md)"
    )
    parser.add_argument(
        "--model", "-m",
        default="claude-sonnet-4-20250514",
        help="Claude model to use"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=12,
        help="Max agentic iterations (default: 12)"
    )
    args = parser.parse_args()

    trace_path = Path(args.trace)
    if not trace_path.exists():
        print(f"❌ Trace file not found: {trace_path}")
        sys.exit(1)

    run_agent(trace_path, args.focus, args.output, args.max_iterations, args.model)


if __name__ == "__main__":
    main()
