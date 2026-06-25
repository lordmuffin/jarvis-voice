package com.lordmuffin.jarvisvoice.wear.db

enum class SyncStatus { PENDING, SYNCED, FAILED }

data class RecordingEntity(
    val id: Long,
    val filePath: String,
    val timestampMs: Long,
    val status: SyncStatus = SyncStatus.PENDING,
    val response: String? = null,
    val errorMessage: String? = null,
)
