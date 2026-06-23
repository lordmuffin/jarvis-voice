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
    /** Stop the mic immediately without firing [onFinal]. Used to cancel a shadow barge-in
     *  listener when the conversation ends or transitions to a new state. */
    fun cancelListening() {}
    fun destroy()
}
