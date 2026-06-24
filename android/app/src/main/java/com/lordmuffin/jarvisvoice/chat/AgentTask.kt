package com.lordmuffin.jarvisvoice.chat

data class AgentTask(
    val id: String,
    val name: String,
    val prompt: String,
    val model: String,
    val status: String,       // queued | running | done | failed | cancelled
    val output: String,
    val tokens: Int,
    val createdAt: Long,      // epoch seconds (from server)
    val startedAt: Long?,
    val finishedAt: Long?,
) {
    val elapsedMs: Long? get() {
        val start = startedAt ?: return null
        val end   = finishedAt ?: return null
        return ((end - start) * 1000).toLong()
    }

    val isTerminal get() = status == "done" || status == "failed" || status == "cancelled"
}
