package com.lordmuffin.jarvisvoice.speech

interface SpeechEngine {
    /**
     * @param holdMode true = silence detection disabled, caller stops via [stopListening].
     *                 false = auto-stop after 30 s of silence.
     */
    fun startListening(
        onPartial: (String) -> Unit,
        onFinal:   (String) -> Unit,
        onError:   (Int)    -> Unit,
        holdMode:  Boolean = false
    )
    fun stopListening()
    fun destroy()
}
