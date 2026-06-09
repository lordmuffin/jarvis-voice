package dev.apj.jarvisvoice.speech

interface SpeechEngine {
    fun startListening(
        onPartial: (String) -> Unit,
        onFinal: (String) -> Unit,
        onError: (Int) -> Unit
    )
    fun stopListening()
    fun destroy()
}
