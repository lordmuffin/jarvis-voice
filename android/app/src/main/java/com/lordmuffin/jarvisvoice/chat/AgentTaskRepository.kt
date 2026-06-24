package com.lordmuffin.jarvisvoice.chat

import com.lordmuffin.jarvisvoice.DebugLog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

class AgentTaskRepository {

    var vaultBase   = "http://192.168.1.155:8881"
    var vaultApiKey = "0WBpWVdLsieaJPpTI7JEjKBZZMd2G-9WWZM2Iiq_wMo"

    suspend fun listTasks(): List<AgentTask> = withContext(Dispatchers.IO) {
        runCatching {
            val conn = openGet("$vaultBase/api/v1/agent/tasks")
            val body = conn.inputStream.bufferedReader().readText()
            conn.disconnect()
            val arr  = JSONObject(body).getJSONArray("tasks")
            (0 until arr.length()).map { parseTask(arr.getJSONObject(it)) }
        }.getOrElse {
            DebugLog.e("AgentTaskRepo", "listTasks failed: ${it.message}")
            emptyList()
        }
    }

    suspend fun getTask(id: String): AgentTask? = withContext(Dispatchers.IO) {
        runCatching {
            val conn = openGet("$vaultBase/api/v1/agent/tasks/$id")
            val body = conn.inputStream.bufferedReader().readText()
            conn.disconnect()
            parseTask(JSONObject(body))
        }.getOrElse {
            DebugLog.e("AgentTaskRepo", "getTask($id) failed: ${it.message}")
            null
        }
    }

    suspend fun createTask(
        name: String,
        prompt: String,
        model: String,
        system: String = "",
    ): String? = withContext(Dispatchers.IO) {
        runCatching {
            val payload = JSONObject().apply {
                put("name",   name)
                put("prompt", prompt)
                put("model",  model)
                put("system", system)
            }.toString().toByteArray()

            val conn = openPost("$vaultBase/api/v1/agent/tasks", payload)
            val body = conn.inputStream.bufferedReader().readText()
            conn.disconnect()
            JSONObject(body).getString("id")
        }.getOrElse {
            DebugLog.e("AgentTaskRepo", "createTask failed: ${it.message}")
            null
        }
    }

    suspend fun deleteTask(id: String): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            val url  = URL("$vaultBase/api/v1/agent/tasks/$id")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "DELETE"
                setRequestProperty("X-Jarvis-Key", vaultApiKey)
                connectTimeout = 8_000
                readTimeout    = 8_000
            }
            conn.connect()
            val code = conn.responseCode
            conn.disconnect()
            code in 200..299
        }.getOrElse { false }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun openGet(urlStr: String): HttpURLConnection =
        (URL(urlStr).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            setRequestProperty("X-Jarvis-Key", vaultApiKey)
            connectTimeout = 10_000
            readTimeout    = 15_000
            connect()
        }

    private fun openPost(urlStr: String, body: ByteArray): HttpURLConnection =
        (URL(urlStr).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("X-Jarvis-Key", vaultApiKey)
            doOutput = true
            connectTimeout = 10_000
            readTimeout    = 30_000
            connect()
            outputStream.write(body)
        }

    private fun parseTask(o: JSONObject) = AgentTask(
        id          = o.getString("id"),
        name        = o.optString("name", "Unnamed"),
        prompt      = o.optString("prompt", ""),
        model       = o.optString("model", ""),
        status      = o.optString("status", "unknown"),
        output      = o.optString("output", ""),
        tokens      = o.optInt("tokens", 0),
        createdAt   = (o.optDouble("created_at", 0.0) * 1000).toLong(),
        startedAt   = o.optDouble("started_at", 0.0).takeIf { it > 0 }?.let { (it * 1000).toLong() },
        finishedAt  = o.optDouble("finished_at", 0.0).takeIf { it > 0 }?.let { (it * 1000).toLong() },
    )
}
