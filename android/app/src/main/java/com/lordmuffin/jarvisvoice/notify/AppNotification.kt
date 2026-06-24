package com.lordmuffin.jarvisvoice.notify

data class AppNotification(
    val id: String,
    val title: String,
    val body: String,
    val firesAt: Long,
)
