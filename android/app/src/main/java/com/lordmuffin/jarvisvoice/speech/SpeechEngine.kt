package com.lordmuffin.jarvisvoice.speech

interface SpeechEngine {
    /** @param mode how the session ends: at the first pause, after a long silence, or only via [stopListening]. */
    fun startListening(
        onPartial: (String) -> Unit,
        onFinal:   (String) -> Unit,
        onError:   (Int)    -> Unit,
        mode:      ListenMode = ListenMode.CONVERSATION
    )
    fun stopListening()
    /** Stop the mic immediately without firing [onFinal]. Used to cancel a shadow barge-in
     *  listener when the conversation ends or transitions to a new state. */
    fun cancelListening() {}
    fun destroy()
}
