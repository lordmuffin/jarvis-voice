package com.lordmuffin.jarvisvoice

import android.content.Context
import org.json.JSONObject

class CustomDictionaryManager(context: Context) {

    private val prefs = context.getSharedPreferences("jarvis_dictionary", Context.MODE_PRIVATE)
    private val KEY   = "entries"

    fun getAll(): Map<String, String> {
        val json = prefs.getString(KEY, null) ?: return emptyMap()
        return try {
            val obj = JSONObject(json)
            obj.keys().asSequence().associateWith { obj.getString(it) }
        } catch (_: Exception) { emptyMap() }
    }

    fun addEntry(from: String, to: String) {
        val map = getAll().toMutableMap()
        map[from.trim().lowercase()] = to.trim()
        save(map)
    }

    fun removeEntry(from: String) {
        val map = getAll().toMutableMap()
        map.remove(from.trim().lowercase())
        save(map)
    }

    private fun save(map: Map<String, String>) {
        val obj = JSONObject()
        map.forEach { (k, v) -> obj.put(k, v) }
        prefs.edit().putString(KEY, obj.toString()).apply()
    }

    fun applyTo(transcript: String): String {
        val dict = getAll()
        if (dict.isEmpty()) return transcript
        return transcript.split(" ").joinToString(" ") { word ->
            val stripped = word.trimEnd('.', ',', '!', '?', ';', ':')
            val punct    = word.drop(stripped.length)
            val hit      = dict[stripped.lowercase()]
            if (hit != null) hit + punct else word
        }
    }
}
