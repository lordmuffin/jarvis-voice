package com.lordmuffin.jarvisvoice.chat

import android.content.Context
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.notify.NotifyWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

private const val LLM_BASE   = "http://192.168.1.93:4000"
private const val VAULT_BASE = "http://192.168.1.155:8881"   // capture API on DGX Spark (.155)

// /no_think disables Qwen3's chain-of-thought so the model replies directly.
private const val SYSTEM_PROMPT =
    "/no_think\n\nYou are Kai, Andrew's AI chief of staff. " +
    "Reply in 1-3 sentences or a short list — nothing more. " +
    "No preamble, no thinking steps, no analysis. Answer directly. " +
    "When Andrew asks about his tasks, sprint board, or captured notes, " +
    "use the vault tools before answering."

private val VAULT_TOOLS = JSONArray().apply {
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "read_note")
            put("description",
                "Read a specific note from Andrew's vault by relative path. " +
                "Use for: daily notes, sprint board, project notes, TASKS.md.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("path", JSONObject().apply {
                        put("type", "string")
                        put("description", "Relative path to the note, e.g. '20 Areas/Personal/Sprints.md'")
                    })
                })
                put("required", JSONArray().put("path"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "search_vault")
            put("description", "Search vault notes for a keyword. Returns matching file paths.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("query", JSONObject().apply {
                        put("type", "string")
                        put("description", "Search term or phrase")
                    })
                    put("directory", JSONObject().apply {
                        put("type", "string")
                        put("description", "Subdirectory to search within (optional)")
                        put("default", "")
                    })
                })
                put("required", JSONArray().put("query"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "get_sprint_state")
            put("description",
                "Get the current sprint board, daily highlight, and open tasks. " +
                "Call when Andrew asks about priorities or what to work on.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject())
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "append_to_note")
            put("description",
                "Append text to an existing vault note. " +
                "Use for capturing voice thoughts into Brain Dump or TASKS.md.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("path", JSONObject().apply {
                        put("type", "string")
                        put("description", "Relative path to the note within the vault")
                    })
                    put("text", JSONObject().apply {
                        put("type", "string")
                        put("description", "Text to append")
                    })
                })
                put("required", JSONArray().put("path").put("text"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "write_note")
            put("description",
                "Create or overwrite a vault note with full content. " +
                "Use for saving new skills to 90 Skills/, creating project notes, or any new document.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("path", JSONObject().apply {
                        put("type", "string")
                        put("description", "Relative path for the note, e.g. '90 Skills/my-skill.md'")
                    })
                    put("content", JSONObject().apply {
                        put("type", "string")
                        put("description", "Full content to write (overwrites if exists)")
                    })
                })
                put("required", JSONArray().put("path").put("content"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "web_fetch")
            put("description",
                "Fetch a URL and return its text content for research. " +
                "Use to look up documentation, articles, GitHub issues, or any web resource.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("url", JSONObject().apply {
                        put("type", "string")
                        put("description", "Full URL to fetch (https://...)")
                    })
                })
                put("required", JSONArray().put("url"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "run_command")
            put("description",
                "Run a shell command on Andrew's homelab server. " +
                "Use for checking service status, reading logs, running scripts, or system tasks.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("command", JSONObject().apply {
                        put("type", "string")
                        put("description", "Shell command to run via /bin/bash")
                    })
                    put("working_dir", JSONObject().apply {
                        put("type", "string")
                        put("description", "Working directory (default: /home/lordmuffin)")
                        put("default", "/home/lordmuffin")
                    })
                })
                put("required", JSONArray().put("command"))
            })
        })
    })
    // ── Git tools ─────────────────────────────────────────────────────────────
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "git_clone")
            put("description",
                "Clone a GitHub repository to the homelab server workspace so you can read and edit it. " +
                "Call this first before any git_write_file or git_commit_and_push.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("repo", JSONObject().apply {
                        put("type", "string")
                        put("description", "owner/repo shorthand (e.g. 'lordmuffin/jarvis-voice') or full HTTPS URL")
                    })
                    put("branch", JSONObject().apply {
                        put("type", "string")
                        put("description", "Branch to checkout after clone (default: repo default branch)")
                        put("default", "")
                    })
                })
                put("required", JSONArray().put("repo"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "git_write_file")
            put("description",
                "Write or overwrite a file in a cloned repository workspace. " +
                "Use after git_clone to make code changes before committing.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("repo", JSONObject().apply {
                        put("type", "string")
                        put("description", "owner/repo identifier used when the repo was cloned")
                    })
                    put("path", JSONObject().apply {
                        put("type", "string")
                        put("description", "Relative file path within the repo (e.g. 'src/main.py')")
                    })
                    put("content", JSONObject().apply {
                        put("type", "string")
                        put("description", "Full file content to write")
                    })
                })
                put("required", JSONArray().put("repo").put("path").put("content"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "git_status")
            put("description",
                "Get the current git status, branch, and diff for a cloned repo workspace. " +
                "Use to verify changes before committing.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("repo", JSONObject().apply {
                        put("type", "string")
                        put("description", "owner/repo identifier")
                    })
                })
                put("required", JSONArray().put("repo"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "git_commit_and_push")
            put("description",
                "Stage all changes, commit with a message, and push to the remote origin. " +
                "Call after git_write_file to persist code changes.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("repo", JSONObject().apply {
                        put("type", "string")
                        put("description", "owner/repo identifier")
                    })
                    put("message", JSONObject().apply {
                        put("type", "string")
                        put("description", "Git commit message")
                    })
                    put("push", JSONObject().apply {
                        put("type", "boolean")
                        put("description", "Whether to push after committing (default true)")
                        put("default", true)
                    })
                })
                put("required", JSONArray().put("repo").put("message"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "git_create_pr")
            put("description",
                "Create a pull request on GitHub after pushing a branch. " +
                "Requires gh CLI to be authenticated on the homelab server.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("repo", JSONObject().apply {
                        put("type", "string")
                        put("description", "owner/repo identifier")
                    })
                    put("title", JSONObject().apply {
                        put("type", "string")
                        put("description", "Pull request title")
                    })
                    put("body", JSONObject().apply {
                        put("type", "string")
                        put("description", "Pull request description (markdown)")
                        put("default", "")
                    })
                    put("base", JSONObject().apply {
                        put("type", "string")
                        put("description", "Base branch to merge into (default: main)")
                        put("default", "main")
                    })
                })
                put("required", JSONArray().put("repo").put("title"))
            })
        })
    })
    put(JSONObject().apply {
        put("type", "function")
        put("function", JSONObject().apply {
            put("name", "schedule_notification")
            put("description",
                "Schedule a push notification to appear on the user's phone after a delay. " +
                "Use whenever the user says 'remind me', 'notify me', or 'set a timer'. " +
                "The notification fires even when the app is in the background.")
            put("parameters", JSONObject().apply {
                put("type", "object")
                put("properties", JSONObject().apply {
                    put("title", JSONObject().apply {
                        put("type", "string")
                        put("description", "Short notification title (e.g. 'Time to take Vyvanse')")
                    })
                    put("body", JSONObject().apply {
                        put("type", "string")
                        put("description", "Optional longer notification body text")
                        put("default", "")
                    })
                    put("delay_minutes", JSONObject().apply {
                        put("type", "number")
                        put("description", "Minutes from now when the notification should fire")
                    })
                })
                put("required", JSONArray().put("title").put("delay_minutes"))
            })
        })
    })
}

class LlmRepository(private val context: Context) {

    private val client = OkHttpClient.Builder()
        .readTimeout(60, TimeUnit.SECONDS)
        .connectTimeout(10, TimeUnit.SECONDS)
        .build()

    // ── Streaming chat (no tools — for normal turns) ──────────────────────────

    fun streamChat(
        history: List<ConversationMessage>,
        model: String,
    ): Flow<String> = callbackFlow {
        val messages = buildMessages(history)
        val body = JSONObject()
            .put("model", model)
            .put("messages", messages)
            .put("stream", true)
            .put("tools", VAULT_TOOLS)
            .put("tool_choice", "auto")
            .put("chat_template_kwargs", JSONObject().put("enable_thinking", false))
            .toString()
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("$LLM_BASE/v1/chat/completions")
            .post(body)
            .build()

        val listener = object : EventSourceListener() {
            override fun onEvent(source: EventSource, id: String?, type: String?, data: String) {
                if (data == "[DONE]") { close(); return }
                try {
                    val choice = JSONObject(data).getJSONArray("choices").getJSONObject(0)
                    val delta  = choice.optJSONObject("delta") ?: return
                    val token  = delta.optString("content", "")
                    if (token.isNotEmpty()) trySend(token)
                } catch (_: Exception) {}
            }
            override fun onFailure(source: EventSource, t: Throwable?, response: Response?) {
                close(t ?: IOException("SSE failed: ${response?.code}"))
            }
            override fun onClosed(source: EventSource) { close() }
        }

        val es = EventSources.createFactory(client).newEventSource(request, listener)
        awaitClose { es.cancel() }
    }

    // ── Non-streaming chat with tool call loop ────────────────────────────────

    // Returns the final assistant text after resolving any tool calls.
    // onToolCall is invoked with the tool name so the caller can show status.
    suspend fun chatWithTools(
        history: List<ConversationMessage>,
        model: String,
        onToolCall: (String) -> Unit = {},
        onUsage: (Int) -> Unit = {},
    ): String = withContext(Dispatchers.IO) {
        val messages = buildMutableMessages(history)
        var iterations = 0

        while (iterations++ < 5) {
            val responseJson = callLlmBlocking(messages, model) ?: return@withContext ""

            // Report token usage after each round so the UI stays current.
            responseJson.optJSONObject("usage")?.optInt("total_tokens", 0)
                ?.let { if (it > 0) onUsage(it) }

            val choice  = responseJson.getJSONArray("choices").getJSONObject(0)
            val message = choice.getJSONObject("message")
            val content = message.optString("content", "")
            val toolCalls = message.optJSONArray("tool_calls")

            if (toolCalls == null || toolCalls.length() == 0) {
                return@withContext content.trim()
            }

            // Append assistant message with tool_calls
            messages.put(message)

            // Execute each tool and append results
            for (i in 0 until toolCalls.length()) {
                val tc       = toolCalls.getJSONObject(i)
                val tcId     = tc.getString("id")
                val fn       = tc.getJSONObject("function")
                val toolName = fn.getString("name")
                val args     = runCatching { JSONObject(fn.getString("arguments")) }.getOrDefault(JSONObject())

                DebugLog.i("VaultTool", "calling $toolName args=$args")
                onToolCall(toolName)

                val result = executeVaultTool(toolName, args)
                DebugLog.i("VaultTool", "$toolName → ${result.take(80)}")

                messages.put(JSONObject().apply {
                    put("role", "tool")
                    put("tool_call_id", tcId)
                    put("content", result)
                })
            }
        }
        return@withContext ""
    }

    // ── LLM blocking call (non-streaming, returns full JSON) ─────────────────

    private fun callLlmBlocking(messages: JSONArray, model: String): JSONObject? {
        val body = JSONObject()
            .put("model", model)
            .put("messages", messages)
            .put("stream", false)
            .put("tools", VAULT_TOOLS)
            .put("tool_choice", "auto")
            .put("chat_template_kwargs", JSONObject().put("enable_thinking", false))
            .toString()
            .toRequestBody("application/json".toMediaType())

        return try {
            val response = client.newCall(
                Request.Builder().url("$LLM_BASE/v1/chat/completions").post(body).build()
            ).execute()
            val text = response.body?.string() ?: return null
            JSONObject(text)
        } catch (e: Exception) {
            DebugLog.e("LlmRepository", "blocking call failed: ${e.message}")
            null
        }
    }

    // ── Vault tool execution (HTTP calls to capture API on GamingPC) ──────────

    private fun executeVaultTool(name: String, args: JSONObject): String {
        return try {
            when (name) {
                "read_note" -> {
                    val path = args.getString("path")
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/vault/note?path=${encode(path)}")
                        .header("x-jarvis-key", vaultKey())
                        .get().build()
                    val resp = client.newCall(req).execute()
                    if (!resp.isSuccessful) return "Error reading note: ${resp.code}"
                    JSONObject(resp.body?.string() ?: "").optString("content", "[empty]")
                }
                "search_vault" -> {
                    val query = args.getString("query")
                    val dir   = args.optString("directory", "")
                    val url   = "$VAULT_BASE/api/v1/vault/search?query=${encode(query)}" +
                                if (dir.isNotEmpty()) "&directory=${encode(dir)}" else ""
                    val req = Request.Builder().url(url).header("x-jarvis-key", vaultKey()).get().build()
                    val resp = client.newCall(req).execute()
                    if (!resp.isSuccessful) return "Search failed: ${resp.code}"
                    val json = JSONObject(resp.body?.string() ?: "{}")
                    val count = json.optInt("count", 0)
                    val matches = json.optJSONArray("matches") ?: JSONArray()
                    if (count == 0) return "No notes found containing '${query}'"
                    "Found $count notes:\n" + (0 until matches.length()).joinToString("\n") { matches.getString(it) }
                }
                "get_sprint_state" -> {
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/vault/sprint-state")
                        .header("x-jarvis-key", vaultKey())
                        .get().build()
                    val resp = client.newCall(req).execute()
                    if (!resp.isSuccessful) return "Sprint state unavailable: ${resp.code}"
                    val json = JSONObject(resp.body?.string() ?: "{}")
                    val sections = json.optJSONArray("sections") ?: return "[no data]"
                    (0 until sections.length()).joinToString("\n\n") { i ->
                        val s = sections.getJSONObject(i)
                        "=== ${s.optString("path")} ===\n${s.optString("content")}"
                    }
                }
                "append_to_note" -> {
                    val payload = JSONObject()
                        .put("path", args.getString("path"))
                        .put("text", args.getString("text"))
                        .toString()
                        .toRequestBody("application/json".toMediaType())
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/vault/note/append")
                        .header("x-jarvis-key", vaultKey())
                        .post(payload).build()
                    val resp = client.newCall(req).execute()
                    if (!resp.isSuccessful) return "Append failed: ${resp.code}"
                    "Appended to ${args.optString("path")}"
                }
                "write_note" -> {
                    val payload = JSONObject()
                        .put("path", args.getString("path"))
                        .put("content", args.getString("content"))
                        .toString()
                        .toRequestBody("application/json".toMediaType())
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/vault/note/write")
                        .header("x-jarvis-key", vaultKey())
                        .post(payload).build()
                    val resp = client.newCall(req).execute()
                    if (!resp.isSuccessful) return "Write failed: ${resp.code}"
                    "Written: ${args.optString("path")}"
                }
                "web_fetch" -> {
                    val url = args.getString("url")
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/web/fetch?url=${encode(url)}")
                        .header("x-jarvis-key", vaultKey())
                        .get().build()
                    val resp = client.newCall(req).execute()
                    if (!resp.isSuccessful) return "Fetch failed: ${resp.code}"
                    JSONObject(resp.body?.string() ?: "{}").optString("content", "[empty]")
                }
                "run_command" -> {
                    val payload = JSONObject()
                        .put("command", args.getString("command"))
                        .put("working_dir", args.optString("working_dir", "/home/lordmuffin"))
                        .toString()
                        .toRequestBody("application/json".toMediaType())
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/system/exec")
                        .header("x-jarvis-key", vaultKey())
                        .post(payload).build()
                    val resp = client.newCall(req).execute()
                    if (!resp.isSuccessful) return "Command failed: ${resp.code}"
                    val json = JSONObject(resp.body?.string() ?: "{}")
                    buildString {
                        append("exit_code: ${json.optInt("exit_code", -1)}\n")
                        val out = json.optString("stdout", "")
                        val err = json.optString("stderr", "")
                        if (out.isNotBlank()) append("stdout:\n$out\n")
                        if (err.isNotBlank()) append("stderr:\n$err")
                    }.trim()
                }
                "git_clone" -> {
                    val payload = JSONObject()
                        .put("repo", args.getString("repo"))
                        .put("branch", args.optString("branch", ""))
                        .toString().toRequestBody("application/json".toMediaType())
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/git/clone")
                        .header("x-jarvis-key", vaultKey()).post(payload).build()
                    val resp = client.newCall(req).execute()
                    resp.body?.string() ?: "Clone failed: ${resp.code}"
                }
                "git_write_file" -> {
                    val payload = JSONObject()
                        .put("repo", args.getString("repo"))
                        .put("path", args.getString("path"))
                        .put("content", args.getString("content"))
                        .toString().toRequestBody("application/json".toMediaType())
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/git/write")
                        .header("x-jarvis-key", vaultKey()).post(payload).build()
                    val resp = client.newCall(req).execute()
                    resp.body?.string() ?: "Write failed: ${resp.code}"
                }
                "git_status" -> {
                    val repo = args.getString("repo")
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/git/status?repo=${encode(repo)}")
                        .header("x-jarvis-key", vaultKey()).get().build()
                    val resp = client.newCall(req).execute()
                    resp.body?.string() ?: "Status failed: ${resp.code}"
                }
                "git_commit_and_push" -> {
                    val payload = JSONObject()
                        .put("repo", args.getString("repo"))
                        .put("message", args.getString("message"))
                        .put("push", args.optBoolean("push", true))
                        .toString().toRequestBody("application/json".toMediaType())
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/git/commit")
                        .header("x-jarvis-key", vaultKey()).post(payload).build()
                    val resp = client.newCall(req).execute()
                    resp.body?.string() ?: "Commit failed: ${resp.code}"
                }
                "git_create_pr" -> {
                    val payload = JSONObject()
                        .put("repo", args.getString("repo"))
                        .put("title", args.getString("title"))
                        .put("body", args.optString("body", ""))
                        .put("base", args.optString("base", "main"))
                        .toString().toRequestBody("application/json".toMediaType())
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/git/pr")
                        .header("x-jarvis-key", vaultKey()).post(payload).build()
                    val resp = client.newCall(req).execute()
                    resp.body?.string() ?: "PR failed: ${resp.code}"
                }
                "schedule_notification" -> {
                    val title        = args.getString("title")
                    val body         = args.optString("body", "")
                    val delayMinutes = args.getDouble("delay_minutes")
                    val payload = JSONObject()
                        .put("title", title)
                        .put("body", body)
                        .put("delay_minutes", delayMinutes)
                        .toString().toRequestBody("application/json".toMediaType())
                    val req = Request.Builder()
                        .url("$VAULT_BASE/api/v1/notify/schedule")
                        .header("x-jarvis-key", vaultKey()).post(payload).build()
                    val resp = client.newCall(req).execute()
                    val respBody = resp.body?.string() ?: ""
                    // Schedule a one-shot WorkManager job for exact delivery — the
                    // periodic poller has a 15-min minimum and would fire too late.
                    val notifId = runCatching {
                        JSONObject(respBody).optString("id", "")
                    }.getOrDefault("")
                    val delayMs = (delayMinutes * 60 * 1000).toLong().coerceAtLeast(0L)
                    NotifyWorker.scheduleOnce(context, notifId, title, body, delayMs)
                    respBody.ifEmpty { "schedule failed: ${resp.code}" }
                }
                else -> "Unknown tool: $name"
            }
        } catch (e: Exception) {
            "Tool error ($name): ${e.message}"
        }
    }

    // ── Models ────────────────────────────────────────────────────────────────

    suspend fun fetchModels(): List<String> = withContext(Dispatchers.IO) {
        try {
            val response = client.newCall(
                Request.Builder().url("$LLM_BASE/v1/models").get().build()
            ).execute()
            if (!response.isSuccessful) return@withContext emptyList()
            val body = response.body?.string() ?: return@withContext emptyList()
            val data = JSONObject(body).getJSONArray("data")
            (0 until data.length()).map { data.getJSONObject(it).getString("id") }.sorted()
        } catch (_: Exception) {
            emptyList()
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun buildMessages(history: List<ConversationMessage>): JSONArray =
        JSONArray().apply {
            put(JSONObject().put("role", "system").put("content", SYSTEM_PROMPT))
            history.forEach { put(JSONObject().put("role", it.role).put("content", it.content)) }
        }

    private fun buildMutableMessages(history: List<ConversationMessage>): JSONArray =
        buildMessages(history)

    private fun encode(s: String): String =
        java.net.URLEncoder.encode(s, "UTF-8")

    // Set by VoiceChatViewModel on init from SharedPreferences (pref key: vault_api_key).
    var vaultApiKey: String = ""

    private fun vaultKey(): String = vaultApiKey
}
