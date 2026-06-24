package com.lordmuffin.jarvisvoice.chat

data class AgentTask(
    val id: String,
    val name: String,
    val prompt: String,
    val model: String,
    val status: String,       // queued | running | done | failed | cancelled
    val output: String,
    val tokens: Int,
    val createdAt: Long,      // epoch milliseconds
    val startedAt: Long?,     // epoch milliseconds
    val finishedAt: Long?,    // epoch milliseconds
    val messages: List<Pair<String, String>> = emptyList(), // role → content pairs
) {
    // startedAt/finishedAt are already in ms — no extra *1000
    val elapsedMs: Long? get() {
        val start = startedAt ?: return null
        val end   = finishedAt ?: return null
        return end - start
    }

    val isTerminal get() = status == "done" || status == "failed" || status == "cancelled"
}
