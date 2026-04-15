#!/usr/bin/env python3
"""
extract_for_ai.py — Extract Perfetto trace data into an AI-friendly report.

This script reads a .perfetto-trace file, runs a battery of SQL queries
using Perfetto's trace_processor Python API, and outputs a structured
JSON report that can be uploaded to Claude (or any LLM) for analysis.

Usage:
    pip install perfetto pandas
    python3 extract_for_ai.py <trace_file> [--output report.json] [--csv]

Examples:
    python3 extract_for_ai.py slowstart.perfetto-trace
    python3 extract_for_ai.py slowstart.perfetto-trace --output analysis.json
    python3 extract_for_ai.py slowstart.perfetto-trace --csv  # also dump CSVs
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from perfetto.trace_processor import TraceProcessor
except ImportError:
    print("❌ Missing dependency. Install with:")
    print("   pip install perfetto")
    sys.exit(1)

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# ── SQL Queries ──────────────────────────────────────────────────────────

QUERIES = {

    # ── 1. Process overview ──
    "processes": """
        SELECT upid, pid, name
        FROM process
        WHERE name IS NOT NULL
        ORDER BY name
    """,

    # ── 2. App startup lifecycle slices ──
    "startup_slices": """
        SELECT
            s.name,
            s.ts,
            s.dur / 1e6 AS dur_ms,
            t.name AS track
        FROM slice s
        JOIN track t ON s.track_id = t.id
        WHERE s.name IN (
            'bindApplication',
            'activityStart',
            'activityResume',
            'activityPause',
            'Choreographer#doFrame',
            'traversal',
            'measure',
            'layout',
            'draw',
            'inflate',
            'ResourcesManager#getResources',
            'OpenDexFilesFromOat',
            'VerifyClass'
        )
        ORDER BY s.ts
        LIMIT 200
    """,

    # ── 3. Custom trace sections (our SlowStart:: markers) ──
    "custom_trace_sections": """
        SELECT
            s.name,
            s.ts,
            s.dur / 1e6 AS dur_ms,
            t.name AS track
        FROM slice s
        JOIN track t ON s.track_id = t.id
        WHERE s.name LIKE 'SlowStart::%'
        ORDER BY s.ts
    """,

    # ── 4. Slowest slices on main thread ──
    "slow_main_thread": """
        SELECT
            s.name,
            s.dur / 1e6 AS dur_ms,
            s.ts
        FROM slice s
        JOIN thread_track tt ON s.track_id = tt.id
        JOIN thread t USING(utid)
        JOIN process p USING(upid)
        WHERE t.is_main_thread = 1
          AND s.dur > 1000000
        ORDER BY s.dur DESC
        LIMIT 50
    """,

    # ── 5. Binder transactions (IPC) ──
    "binder_transactions": """
        SELECT
            s.name,
            s.dur / 1e6 AS dur_ms,
            s.ts
        FROM slice s
        WHERE s.name LIKE 'binder%'
           OR s.name LIKE 'Binder%'
        ORDER BY s.dur DESC
        LIMIT 30
    """,

    # ── 6. Thread workload summary ──
    "thread_workload": """
        SELECT
            t.name AS thread_name,
            p.name AS process_name,
            COUNT(*) AS slice_count,
            SUM(s.dur) / 1e6 AS total_dur_ms,
            MAX(s.dur) / 1e6 AS max_dur_ms,
            AVG(s.dur) / 1e6 AS avg_dur_ms
        FROM slice s
        JOIN thread_track tt ON s.track_id = tt.id
        JOIN thread t USING(utid)
        JOIN process p USING(upid)
        GROUP BY t.name, p.name
        ORDER BY total_dur_ms DESC
        LIMIT 30
    """,

    # ── 7. GC events ──
    "gc_events": """
        SELECT
            name,
            dur / 1e6 AS dur_ms,
            ts
        FROM slice
        WHERE name LIKE '%GC%'
           OR name LIKE '%Gc%'
           OR name LIKE '%concurrent%copying%'
        ORDER BY ts
    """,

    # ── 8. Disk I/O on main thread ──
    "io_main_thread": """
        SELECT
            s.name,
            s.dur / 1e6 AS dur_ms
        FROM slice s
        JOIN thread_track tt ON s.track_id = tt.id
        JOIN thread t USING(utid)
        WHERE t.is_main_thread = 1
          AND (
            s.name LIKE '%Read%'
            OR s.name LIKE '%Write%'
            OR s.name LIKE '%Open%'
            OR s.name LIKE '%Flush%'
            OR s.name LIKE '%SharedPreferences%'
          )
        ORDER BY s.dur DESC
        LIMIT 30
    """,

    # ── 9. Frame rendering durations ──
    "frame_durations": """
        SELECT
            s.name,
            s.dur / 1e6 AS dur_ms,
            CASE
                WHEN s.dur > 32000000 THEN 'SEVERE_JANK'
                WHEN s.dur > 16000000 THEN 'JANK'
                WHEN s.dur > 8000000  THEN 'SLOW'
                ELSE 'OK'
            END AS status
        FROM slice s
        JOIN thread_track tt ON s.track_id = tt.id
        JOIN thread t USING(utid)
        WHERE t.is_main_thread = 1
          AND s.name LIKE 'Choreographer#doFrame%'
        ORDER BY s.dur DESC
        LIMIT 50
    """,

    # ── 10. Class verification overhead ──
    "class_verification": """
        SELECT
            name,
            dur / 1e6 AS dur_ms,
            ts
        FROM slice
        WHERE name LIKE 'VerifyClass%'
           OR name LIKE 'OpenDexFilesFromOat%'
           OR name LIKE 'JIT compiling%'
        ORDER BY dur DESC
        LIMIT 30
    """,
}


# ── Optional: Standard Library queries (require INCLUDE PERFETTO MODULE) ──

STDLIB_QUERIES = {

    "android_startups": """
        INCLUDE PERFETTO MODULE android.startup.startups;
        SELECT * FROM android_startups;
    """,

    "android_binder": """
        INCLUDE PERFETTO MODULE android.binder;
        SELECT
            client_process,
            server_process,
            client_aidl_name AS interface,
            dur / 1e6 AS dur_ms
        FROM android_binder_txns
        ORDER BY dur DESC
        LIMIT 20;
    """,

    "monitor_contention": """
        INCLUDE PERFETTO MODULE android.monitor_contention;
        SELECT
            blocking_method,
            blocked_method,
            dur / 1e6 AS dur_ms
        FROM android_monitor_contention
        ORDER BY dur DESC
        LIMIT 20;
    """,
}


def query_to_records(tp, sql):
    """Run a SQL query and return results as list of dicts."""
    try:
        result = tp.query(sql)
        if HAS_PANDAS:
            df = result.as_pandas_dataframe()
            return df.to_dict(orient="records")
        else:
            return [dict(row._asdict()) for row in result]
    except Exception as e:
        return {"error": str(e)}


def run_extraction(trace_path, output_path, dump_csv):
    """Main extraction pipeline."""
    print(f"📂 Loading trace: {trace_path}")
    tp = TraceProcessor(trace=str(trace_path))

    report = {}
    total = len(QUERIES) + len(STDLIB_QUERIES)
    current = 0

    # Run core queries
    print(f"\n🔍 Running {len(QUERIES)} core queries...")
    for name, sql in QUERIES.items():
        current += 1
        print(f"  [{current}/{total}] {name}...", end=" ")
        records = query_to_records(tp, sql)
        report[name] = records
        count = len(records) if isinstance(records, list) else "ERROR"
        print(f"→ {count} rows")

    # Run stdlib queries (may fail on older traces)
    print(f"\n🔍 Running {len(STDLIB_QUERIES)} stdlib queries...")
    for name, sql in STDLIB_QUERIES.items():
        current += 1
        print(f"  [{current}/{total}] {name}...", end=" ")
        records = query_to_records(tp, sql)
        report[name] = records
        count = len(records) if isinstance(records, list) else "ERROR"
        print(f"→ {count}")

    # Add metadata
    meta_records = query_to_records(tp, """
        SELECT name, str_value, int_value
        FROM metadata
        WHERE name IN ('cr-os-arch', 'system_name', 'system_machine',
                       'android_build_fingerprint', 'trace_size_bytes')
    """)
    report["_metadata"] = meta_records

    # Summary stats for the AI
    report["_summary"] = {
        "total_processes": len(report.get("processes", [])),
        "custom_bottlenecks_found": len(report.get("custom_trace_sections", [])),
        "slow_main_thread_slices": len(report.get("slow_main_thread", [])),
        "gc_event_count": len(report.get("gc_events", [])),
        "jank_frames": len([
            f for f in report.get("frame_durations", [])
            if isinstance(f, dict) and f.get("status") in ("JANK", "SEVERE_JANK")
        ]),
    }

    # Save JSON report
    output = Path(output_path)
    with open(output, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n✅ Report saved to: {output}")
    print(f"   Size: {output.stat().st_size / 1024:.1f} KB")

    # Optionally dump CSVs
    if dump_csv and HAS_PANDAS:
        csv_dir = output.parent / "csv_exports"
        csv_dir.mkdir(exist_ok=True)
        for name, data in report.items():
            if isinstance(data, list) and len(data) > 0:
                df = pd.DataFrame(data)
                csv_path = csv_dir / f"{name}.csv"
                df.to_csv(csv_path, index=False)
        print(f"   CSVs saved to: {csv_dir}/")

    # Print quick summary
    print("\n" + "=" * 60)
    print("📊 QUICK SUMMARY")
    print("=" * 60)
    summary = report["_summary"]
    print(f"  Processes in trace:       {summary['total_processes']}")
    print(f"  Custom bottlenecks:       {summary['custom_bottlenecks_found']}")
    print(f"  Slow main-thread slices:  {summary['slow_main_thread_slices']}")
    print(f"  GC events:                {summary['gc_event_count']}")
    print(f"  Jank frames:              {summary['jank_frames']}")

    # Print the custom bottlenecks if found
    custom = report.get("custom_trace_sections", [])
    if isinstance(custom, list) and custom:
        print("\n🔴 BOTTLENECKS FOUND (custom trace sections):")
        for item in custom:
            name = item.get("name", "?")
            dur = item.get("dur_ms", 0)
            print(f"  {name:40s} → {dur:>8.1f} ms")

    print("\n" + "=" * 60)
    print("💡 Next: Upload the JSON to Claude and ask:")
    print('   "Analyze my Android app startup. Find the top bottlenecks')
    print('    and suggest fixes with code examples."')
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Extract Perfetto trace data for AI analysis"
    )
    parser.add_argument("trace", help="Path to .perfetto-trace file")
    parser.add_argument(
        "--output", "-o",
        default="trace_report.json",
        help="Output JSON path (default: trace_report.json)"
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also dump individual CSV files"
    )
    args = parser.parse_args()

    trace_path = Path(args.trace)
    if not trace_path.exists():
        print(f"❌ Trace file not found: {trace_path}")
        sys.exit(1)

    run_extraction(trace_path, args.output, args.csv)


if __name__ == "__main__":
    main()
