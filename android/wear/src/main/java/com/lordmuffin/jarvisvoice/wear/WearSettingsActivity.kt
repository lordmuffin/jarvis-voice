package com.lordmuffin.jarvisvoice.wear

import android.content.Context
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.lordmuffin.jarvisvoice.wear.sync.UploadWorker

class WearSettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_wear_settings)

        val prefs = getSharedPreferences(UploadWorker.PREF_FILE, Context.MODE_PRIVATE)
        val etKey = findViewById<EditText>(R.id.et_api_key)
        val etUrl = findViewById<EditText>(R.id.et_server_url)

        etKey.setText(prefs.getString(UploadWorker.PREF_API_KEY, ""))
        etUrl.setText(prefs.getString(UploadWorker.PREF_SERVER_URL, UploadWorker.DEFAULT_SERVER_URL))

        findViewById<Button>(R.id.btn_save).setOnClickListener {
            prefs.edit()
                .putString(UploadWorker.PREF_API_KEY, etKey.text.toString().trim())
                .putString(UploadWorker.PREF_SERVER_URL, etUrl.text.toString().trim().ifBlank { UploadWorker.DEFAULT_SERVER_URL })
                .apply()
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
            finish()
        }
    }
}
