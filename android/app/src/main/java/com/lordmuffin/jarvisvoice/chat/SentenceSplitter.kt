package com.lordmuffin.jarvisvoice.chat

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

fun splitIntoSentences(tokens: Flow<String>): Flow<String> = flow {
    val buf = StringBuilder()
    tokens.collect { token ->
        buf.append(token)
        var consumed = 0
        var i = 0
        while (i < buf.length) {
            val c = buf[i]
            if (c == '.' || c == '?' || c == '!') {
                val next = i + 1
                val atBoundary = next >= buf.length || buf[next] == ' ' || buf[next] == '\n'
                if (atBoundary) {
                    val sentence = buf.substring(consumed, i + 1).trim()
                    if (sentence.isNotEmpty()) emit(sentence)
                    consumed = if (next < buf.length && (buf[next] == ' ' || buf[next] == '\n')) {
                        next + 1
                    } else {
                        next
                    }
                    i = consumed
                    continue
                }
            }
            i++
        }
        if (consumed > 0) buf.delete(0, consumed)
    }
    val remaining = buf.toString().trim()
    if (remaining.isNotEmpty()) emit(remaining)
}
