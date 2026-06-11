package com.lordmuffin.jarvisvoice

import android.content.Context
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

object DebugLog {

    private val executor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "jarvis-debug-log").also { it.isDaemon = true }
    }
    private val sdf = SimpleDateFormat("HH:mm:ss.SSS", Locale.getDefault())
    private const val MAX_BYTES = 200_000L   // 200 KB before rotation

    private var logFile: File? = null

    fun init(context: Context) {
        logFile = PersistentStorage.logFile(context)
    }

    fun i(tag: String, msg: String) = write("I", tag, msg)
    fun w(tag: String, msg: String) = write("W", tag, msg)
    fun e(tag: String, msg: String, t: Throwable? = null) =
        write("E", tag, if (t != null) "$msg — ${causeChain(t)}" else msg)

    private fun causeChain(t: Throwable): String = buildString {
        var cur: Throwable? = t
        while (cur != null) {
            if (isNotEmpty()) append(" → ")
            append("${cur.javaClass.name}: ${cur.message}")
            cur = cur.cause
        }
    }

    private fun write(level: String, tag: String, msg: String) {
        val line = "${sdf.format(Date())} $level/$tag: $msg\n"
        executor.execute {
            val f = logFile ?: return@execute
            if (f.exists() && f.length() > MAX_BYTES) {
                // Keep last half of the file
                val content = f.readText()
                f.writeText(content.drop(content.length / 2))
            }
            f.appendText(line)
        }
    }

    fun readAll(): String = try {
        logFile?.readText() ?: "(log not initialized)"
    } catch (e: Exception) { "(error reading log: ${e.message})" }

    fun clear() {
        executor.execute { logFile?.writeText("") }
    }
}
