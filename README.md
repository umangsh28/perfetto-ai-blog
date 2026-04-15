# Perfetto SQL + AI: Android Trace Analysis Toolkit

> Analyze Android Perfetto traces with SQL and let AI find the bottlenecks for you.

📝 **Blog post:** [Ditch the UI: Analyzing Android Perfetto Traces with SQL and AI](link-to-medium-post)

## What's in this repo?

```
├── demo-app/                  # "SlowStart" Android app with 5 intentional bottlenecks
│   └── app/src/main/
│       ├── java/.../MainActivity.kt
│       ├── res/layout/
│       └── AndroidManifest.xml
│
├── scripts/
│   ├── capture_trace.sh       # One-command trace capture
│   ├── extract_for_ai.py      # Extract trace → JSON/CSV for LLM analysis
│   └── ai_trace_agent.py      # Agentic loop: Claude autonomously queries the trace
│
└── blog/
    └── blog-post.md           # Full blog post (Medium-ready)
```

## Quick Start

### 1. Capture a trace

```bash
# Install the demo app, then:
./scripts/capture_trace.sh
```

### 2. Extract for AI analysis

```bash
pip install perfetto pandas
python3 scripts/extract_for_ai.py slowstart.perfetto-trace --csv
```

### 3. Upload to Claude

Upload the generated `trace_report.json` to [claude.ai](https://claude.ai) and ask:

> "Analyze my Android app startup. Find the top bottlenecks and suggest fixes."

### 4. Or go fully agentic

```bash
export ANTHROPIC_API_KEY=sk-ant-...
pip install anthropic perfetto pandas
python3 scripts/ai_trace_agent.py slowstart.perfetto-trace --focus "cold startup"
```

## The 3 Levels of Trace + AI Analysis

| Level | Effort | What happens |
|-------|--------|--------------|
| **Copy-paste** | 5 min | Run SQL manually, paste results into Claude |
| **Extraction script** | 2 min | `extract_for_ai.py` dumps everything, upload JSON |
| **Agentic loop** | 1 min | Claude decides what to query, runs 6-10 iterations |

## Prerequisites

- Python 3.8+
- `pip install perfetto pandas anthropic`
- Android device with USB debugging (for trace capture)
- `adb` installed
- Anthropic API key (for agentic mode)

## The Demo App: SlowStart

5 deliberate bottlenecks visible in Perfetto traces:

1. **HeavyJsonParsing** — 500-key JSON parsed on main thread
2. **SharedPrefBulkRead** — 200 synchronous SharedPreferences reads + commit()
3. **PackageManagerQuery** — Binder IPC to get installed packages
4. **ExtraViewInflation** — Deeply nested layout inflated unnecessarily
5. **ExpensiveInit** — Sorting 50K items + hashing on main thread

Each is wrapped in `Trace.beginSection()` / `Trace.endSection()` so they appear as named slices in Perfetto.

## License

MIT
