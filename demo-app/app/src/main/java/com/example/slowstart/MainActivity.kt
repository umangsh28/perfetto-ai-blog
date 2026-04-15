package com.example.slowstart

import android.os.Bundle
import android.os.Trace
import android.util.Log
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.File

/**
 * SlowStart — A deliberately slow Android app for Perfetto SQL + AI blog demo.
 *
 * This app introduces 5 common startup bottlenecks that are clearly
 * visible in a Perfetto trace and queryable via SQL:
 *
 *   1. Heavy JSON parsing on main thread     → shows in slice table
 *   2. Synchronous SharedPreferences reads   → shows as disk I/O
 *   3. Binder IPC call to PackageManager     → shows in binder txns
 *   4. Expensive layout inflation             → shows in UI thread slices
 *   5. Unnecessary class verification         → shows in dex/classload
 *
 * Capture a trace with:
 *   adb shell perfetto -c - --txt -o /data/misc/perfetto-traces/trace \
 *     <<EOF
 *     buffers: { size_kb: 65536 }
 *     duration_ms: 15000
 *     data_sources: { config { name: "linux.ftrace" ftrace_config {
 *       ftrace_events: "sched/sched_switch"
 *       ftrace_events: "power/suspend_resume"
 *       atrace_categories: "am" atrace_categories: "wm"
 *       atrace_categories: "view" atrace_categories: "dalvik"
 *       atrace_categories: "sched" atrace_categories: "binder_driver"
 *       atrace_apps: "com.example.slowstart"
 *     }}}
 *     data_sources: { config { name: "linux.process_stats" }}
 *     EOF
 *
 * Then cold-start the app:
 *   adb shell am force-stop com.example.slowstart
 *   adb shell am start -n com.example.slowstart/.MainActivity
 *
 * Pull the trace:
 *   adb pull /data/misc/perfetto-traces/trace slowstart.perfetto-trace
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "SlowStart"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // ── Bottleneck 1: Heavy JSON parsing on main thread ──────────
        // Simulates loading a large remote config / feature flags file.
        // In production, this should be async or cached.
        Trace.beginSection("SlowStart::HeavyJsonParsing")
        try {
            val jsonString = buildLargeJson(500) // 500 key-value pairs
            val parsed = JSONObject(jsonString)
            Log.d(TAG, "Parsed ${parsed.length()} config keys")
        } finally {
            Trace.endSection()
        }

        // ── Bottleneck 2: Synchronous SharedPreferences reads ────────
        // Reading many keys sequentially on the main thread.
        // In production, use DataStore or read async.
        Trace.beginSection("SlowStart::SharedPrefBulkRead")
        try {
            val prefs = getSharedPreferences("user_config", MODE_PRIVATE)
            // First, seed some data so reads aren't trivially empty
            prefs.edit().apply {
                repeat(200) { putString("pref_key_$it", "value_$it") }
                commit() // intentionally commit (sync) not apply
            }
            // Now read them all back on main thread
            repeat(200) {
                prefs.getString("pref_key_$it", "")
            }
            Log.d(TAG, "Read 200 SharedPreference keys")
        } finally {
            Trace.endSection()
        }

        // ── Bottleneck 3: Binder IPC to PackageManager ──────────────
        // Querying installed packages is a heavy binder call.
        // In production, cache this or move off main thread.
        Trace.beginSection("SlowStart::PackageManagerQuery")
        try {
            val packages = packageManager.getInstalledPackages(0)
            Log.d(TAG, "Found ${packages.size} installed packages")
        } finally {
            Trace.endSection()
        }

        // ── Bottleneck 4: Heavy layout inflation ─────────────────────
        // Inflating a complex layout with nested views.
        setContentView(R.layout.activity_main)

        Trace.beginSection("SlowStart::ExtraViewInflation")
        try {
            // Inflate the heavy layout into a detached parent
            val heavyView = layoutInflater.inflate(R.layout.heavy_layout, null)
            Log.d(TAG, "Inflated heavy layout")
        } finally {
            Trace.endSection()
        }

        // ── Bottleneck 5: Simulated class loading / init work ────────
        // Represents expensive static initializers or verify-class overhead.
        Trace.beginSection("SlowStart::ExpensiveInit")
        try {
            simulateExpensiveInit()
        } finally {
            Trace.endSection()
        }

        Log.d(TAG, "onCreate complete")
    }

    /**
     * Builds a large JSON string to simulate parsing a config payload.
     */
    private fun buildLargeJson(keys: Int): String {
        val sb = StringBuilder("{")
        repeat(keys) { i ->
            if (i > 0) sb.append(",")
            sb.append("\"feature_flag_$i\":{")
            sb.append("\"enabled\":${i % 2 == 0},")
            sb.append("\"rollout\":${i * 0.1},")
            sb.append("\"description\":\"This is feature flag number $i with a reasonably long description to bulk up parsing time\"")
            sb.append("}")
        }
        sb.append("}")
        return sb.toString()
    }

    /**
     * Simulates expensive initialization (sorting, hashing, reflection).
     */
    private fun simulateExpensiveInit() {
        // Sort a large list
        val list = (0..50_000).shuffled().toMutableList()
        list.sort()

        // Simulated hash computation
        val sb = StringBuilder()
        repeat(10_000) { sb.append("hash_payload_$it") }
        sb.toString().hashCode()

        Log.d(TAG, "Expensive init done, sorted ${list.size} items")
    }
}
