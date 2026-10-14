package com.lordmuffin.jarvisvoice

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.widget.Button
import android.widget.ScrollView
import android.widget.TextView
import com.lordmuffin.jarvisvoice.ui.InstToast
import androidx.appcompat.app.AppCompatActivity

class DebugLogActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_debug_log)
        supportActionBar?.title = getString(R.string.chat_debug_title)

        val tvLog   = findViewById<TextView>(R.id.tv_log)
        val scroll  = findViewById<ScrollView>(R.id.scroll_log)
        val btnCopy = findViewById<Button>(R.id.btn_copy_log)
        val btnClear = findViewById<Button>(R.id.btn_clear_log)

        fun refresh() {
            val text = DebugLog.readAll().ifBlank { getString(R.string.chat_debug_empty) }
            tvLog.text = text
            scroll.post { scroll.fullScroll(ScrollView.FOCUS_DOWN) }
        }

        refresh()

        btnCopy.setOnClickListener {
            val cb = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cb.setPrimaryClip(ClipData.newPlainText("jarvis_log", tvLog.text))
            InstToast.show(this, R.string.chat_debug_copied)
        }

        btnClear.setOnClickListener {
            DebugLog.clear()
            tvLog.text = getString(R.string.chat_debug_cleared)
        }
    }
}
