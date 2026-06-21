package com.lordmuffin.jarvisvoice.chat

data class ConversationMessage(
    val role: String,    // "user" or "assistant"
    val content: String
)
