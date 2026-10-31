package com.lordmuffin.jarvisvoice.wearcommon

/** What the server said about one recording upload. Only [Acked] lets the sender delete audio. */
sealed interface UploadResult {
    /** 2xx: the server stored the audio and made session [sessionId]. */
    data class Acked(val sessionId: String) : UploadResult

    /** Network trouble or a 5xx/429: try again later. */
    data class Retry(val reason: String) : UploadResult

    /** 401/403: the token is missing or revoked; retrying cannot help until it is replaced. */
    data class AuthRejected(val reason: String) : UploadResult

    /** The server refused this file (415 no audio, 413 too big, 422 empty). Keep it, don't retry. */
    data class Rejected(val reason: String) : UploadResult
}
