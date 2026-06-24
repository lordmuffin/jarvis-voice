package com.lordmuffin.jarvisvoice.chat

data class SessionMeta(
    val id: String,
    val name: String,
    val createdAt: Long,
    val updatedAt: Long,
    val messageCount: Int,
    val preview: String,
)
