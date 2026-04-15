#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# capture_trace.sh — Record a Perfetto trace of SlowStart app
#
# Usage:
#   ./capture_trace.sh [output_filename]
#
# Prerequisites:
#   - adb installed and device connected
#   - SlowStart app installed on device
#   - Device running Android 10+ (API 29+)
# ──────────────────────────────────────────────────────────────

set -euo pipefail

APP_PACKAGE="com.example.slowstart"
OUTPUT="${1:-slowstart.perfetto-trace}"
DEVICE_TRACE_PATH="/data/misc/perfetto-traces/trace"

echo "╔══════════════════════════════════════════════╗"
echo "║  Perfetto Trace Capture — SlowStart Demo     ║"
echo "╚══════════════════════════════════════════════╝"

# Step 1: Force-stop the app for a clean cold start
echo ""
echo "→ Force-stopping $APP_PACKAGE..."
adb shell am force-stop "$APP_PACKAGE"
sleep 1

# Step 2: Start Perfetto recording in background
echo "→ Starting Perfetto trace (15 seconds)..."
adb shell perfetto -c - --txt -o "$DEVICE_TRACE_PATH" --background <<'PERFETTO_CONFIG'
buffers: {
  size_kb: 65536
  fill_policy: RING_BUFFER
}
duration_ms: 15000

# ── Ftrace: scheduling, binder, atrace ──
data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_wakeup"
      ftrace_events: "sched/sched_wakeup_new"
      ftrace_events: "sched/sched_blocked_reason"
      ftrace_events: "power/cpu_frequency"
      ftrace_events: "power/suspend_resume"
      ftrace_events: "binder/binder_transaction"
      ftrace_events: "binder/binder_transaction_received"
      ftrace_events: "binder/binder_lock"
      ftrace_events: "binder/binder_locked"
      ftrace_events: "binder/binder_unlock"

      # atrace categories for Android framework events
      atrace_categories: "am"      # ActivityManager
      atrace_categories: "wm"      # WindowManager
      atrace_categories: "view"    # View system
      atrace_categories: "dalvik"  # ART / Dalvik
      atrace_categories: "sched"   # CPU scheduling
      atrace_categories: "res"     # Resources
      atrace_categories: "input"   # Input events
      atrace_categories: "disk_io" # Disk I/O

      # Capture custom trace sections from our app
      atrace_apps: "com.example.slowstart"
    }
  }
}

# ── Process stats (memory, threads) ──
data_sources: {
  config {
    name: "linux.process_stats"
    process_stats_config {
      scan_all_processes_on_start: true
      proc_stats_poll_ms: 100
    }
  }
}
PERFETTO_CONFIG

# Step 3: Wait a moment for tracing to stabilize
sleep 2

# Step 4: Cold-start the app
echo "→ Launching $APP_PACKAGE (cold start)..."
adb shell am start -n "$APP_PACKAGE/.MainActivity" -W

# Step 5: Wait for trace to complete
echo "→ Waiting for trace to finish..."
sleep 12

# Step 6: Pull the trace
echo "→ Pulling trace to ./$OUTPUT..."
adb pull "$DEVICE_TRACE_PATH" "$OUTPUT"

echo ""
echo "✅ Trace saved to: $OUTPUT"
echo ""
echo "Next steps:"
echo "  1. Open in Perfetto UI:  https://ui.perfetto.dev"
echo "  2. Run SQL queries:      trace_processor_shell $OUTPUT"
echo "  3. Extract for AI:       python3 extract_for_ai.py $OUTPUT"
echo ""
