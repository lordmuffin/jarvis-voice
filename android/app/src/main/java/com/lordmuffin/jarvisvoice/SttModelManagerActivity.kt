package com.lordmuffin.jarvisvoice

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ProgressBar
import android.widget.RadioButton
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.lordmuffin.jarvisvoice.speech.ModelDownloader
import com.lordmuffin.jarvisvoice.speech.SttModelConfig
import com.lordmuffin.jarvisvoice.speech.SttModelManager
import com.lordmuffin.jarvisvoice.speech.SttModelRegistry

class SttModelManagerActivity : AppCompatActivity() {

    private lateinit var sttMgr: SttModelManager
    private lateinit var adapter: SttModelAdapter
    private var activeDownloader: ModelDownloader? = null
    private var downloadingId: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_stt_model_manager)
        supportActionBar?.title = "STT Model"

        sttMgr = SttModelManager(this)

        val rv = findViewById<RecyclerView>(R.id.rv_stt_models)
        rv.layoutManager = LinearLayoutManager(this)
        adapter = SttModelAdapter()
        rv.adapter = adapter
    }

    override fun onResume() {
        super.onResume()
        adapter.notifyDataSetChanged()
    }

    override fun onDestroy() {
        super.onDestroy()
        activeDownloader?.cancel()
    }

    private fun onSelectModel(config: SttModelConfig) {
        sttMgr.setActiveModel(config.id)
        VoiceOverlayService.instance?.reloadSpeechEngine()
        adapter.notifyDataSetChanged()
        Toast.makeText(this, "${config.displayName} selected", Toast.LENGTH_SHORT).show()
    }

    private fun onDownload(config: SttModelConfig) {
        if (downloadingId != null) return
        downloadingId = config.id
        adapter.notifyDataSetChanged()

        activeDownloader = ModelDownloader(this).also { dl ->
            dl.download(config, object : ModelDownloader.Listener {
                override fun onProgress(downloaded: Long, total: Long) {
                    adapter.updateProgress(config.id, downloaded, total)
                }
                override fun onExtracting() {
                    adapter.updateExtracting(config.id)
                }
                override fun onComplete() {
                    downloadingId = null
                    activeDownloader = null
                    sttMgr.setActiveModel(config.id)
                    VoiceOverlayService.instance?.reloadSpeechEngine()
                    adapter.notifyDataSetChanged()
                    Toast.makeText(
                        this@SttModelManagerActivity,
                        "${config.displayName} ready",
                        Toast.LENGTH_SHORT
                    ).show()
                }
                override fun onError(message: String) {
                    downloadingId = null
                    activeDownloader = null
                    adapter.notifyDataSetChanged()
                    Toast.makeText(
                        this@SttModelManagerActivity,
                        "Download failed: $message",
                        Toast.LENGTH_LONG
                    ).show()
                }
            })
        }
    }

    private fun onCancelDownload() {
        activeDownloader?.cancel()
        activeDownloader = null
        downloadingId = null
        adapter.notifyDataSetChanged()
    }

    private fun onDelete(config: SttModelConfig) {
        AlertDialog.Builder(this)
            .setTitle("Delete ${config.displayName}?")
            .setMessage("${config.fileSizeMb} MB will be freed.")
            .setPositiveButton("Delete") { _, _ ->
                sttMgr.deleteModel(config)
                VoiceOverlayService.instance?.reloadSpeechEngine()
                adapter.notifyDataSetChanged()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ── Adapter ──────────────────────────────────────────────────────────────

    inner class SttModelAdapter : RecyclerView.Adapter<SttModelAdapter.SttModelVH>() {

        private val progressMap = mutableMapOf<String, Pair<Long, Long>>()
        private val extractingSet = mutableSetOf<String>()

        fun updateProgress(id: String, downloaded: Long, total: Long) {
            progressMap[id] = downloaded to total
            val idx = SttModelRegistry.MODELS.indexOfFirst { it.id == id }
            if (idx >= 0) notifyItemChanged(idx)
        }

        fun updateExtracting(id: String) {
            extractingSet.add(id)
            progressMap.remove(id)
            val idx = SttModelRegistry.MODELS.indexOfFirst { it.id == id }
            if (idx >= 0) notifyItemChanged(idx)
        }

        override fun getItemCount() = SttModelRegistry.MODELS.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): SttModelVH =
            SttModelVH(
                LayoutInflater.from(parent.context)
                    .inflate(R.layout.item_llm_model, parent, false)
            )

        override fun onBindViewHolder(holder: SttModelVH, position: Int) {
            val config       = SttModelRegistry.MODELS[position]
            val isInstalled  = sttMgr.isInstalled(config)
            val isActive     = sttMgr.getActiveModelId() == config.id && isInstalled
            val isDl         = downloadingId == config.id
            val isExtracting = extractingSet.contains(config.id)
            val progress     = progressMap[config.id]
            holder.bind(config, isActive, isInstalled, isDl, isExtracting, progress)
        }

        inner class SttModelVH(view: View) : RecyclerView.ViewHolder(view) {
            val rbSelect:    RadioButton = view.findViewById(R.id.rb_select)
            val tvName:      TextView    = view.findViewById(R.id.tv_model_name)
            val tvDefault:   TextView    = view.findViewById(R.id.tv_default_badge)
            val tvDesc:      TextView    = view.findViewById(R.id.tv_model_desc)
            val tvStatus:    TextView    = view.findViewById(R.id.tv_model_status)
            val btnAction:   Button      = view.findViewById(R.id.btn_action)
            val progressBar: ProgressBar = view.findViewById(R.id.progress_download)
            val tvProgress:  TextView    = view.findViewById(R.id.tv_progress_label)

            fun bind(
                config: SttModelConfig,
                isActive: Boolean,
                isInstalled: Boolean,
                isDownloading: Boolean,
                isExtracting: Boolean,
                progress: Pair<Long, Long>?
            ) {
                tvName.text  = config.displayName
                tvDesc.text  = config.description
                tvDefault.visibility = if (config.isDefault) View.VISIBLE else View.GONE

                rbSelect.isChecked = isActive
                rbSelect.isEnabled = isInstalled && !isDownloading

                tvStatus.text = when {
                    isExtracting  -> "Extracting…"
                    isDownloading -> "Downloading…"
                    isInstalled   -> sttMgr.getStatusLabel(config)
                    else          -> "${config.fileSizeMb} MB"
                }

                val busy = isDownloading || isExtracting
                progressBar.visibility = if (busy) View.VISIBLE else View.GONE
                tvProgress.visibility  = if (busy) View.VISIBLE else View.GONE
                if (isExtracting) {
                    progressBar.isIndeterminate = true
                    tvProgress.text = "Extracting…"
                } else if (isDownloading && progress != null) {
                    val pct = if (progress.second > 0)
                        (progress.first * 100 / progress.second).toInt() else -1
                    progressBar.isIndeterminate = pct < 0
                    progressBar.max = 100
                    if (pct >= 0) progressBar.progress = pct
                    val dl  = formatBytes(progress.first)
                    val tot = if (progress.second > 0) " / ${formatBytes(progress.second)}" else ""
                    tvProgress.text = "$dl$tot"
                } else if (isDownloading) {
                    progressBar.isIndeterminate = true
                    tvProgress.text = "Connecting…"
                }

                when {
                    busy -> {
                        btnAction.text = "Cancel"
                        btnAction.backgroundTintList =
                            android.content.res.ColorStateList.valueOf(0xFF333333.toInt())
                        btnAction.isEnabled = true
                        btnAction.setOnClickListener { onCancelDownload() }
                    }
                    isInstalled -> {
                        btnAction.text = if (isActive) "Remove" else "Delete"
                        btnAction.backgroundTintList =
                            android.content.res.ColorStateList.valueOf(0xFF442222.toInt())
                        btnAction.isEnabled = downloadingId == null
                        btnAction.setOnClickListener { onDelete(config) }
                    }
                    else -> {
                        btnAction.text = "Download"
                        btnAction.backgroundTintList =
                            android.content.res.ColorStateList.valueOf(0xFF00B4D8.toInt())
                        btnAction.isEnabled = downloadingId == null
                        btnAction.setOnClickListener { onDownload(config) }
                    }
                }

                rbSelect.setOnClickListener {
                    if (isInstalled && !isDownloading) onSelectModel(config)
                }
            }
        }
    }

    private fun formatBytes(bytes: Long): String = when {
        bytes < 1_048_576 -> "${bytes / 1024} KB"
        else              -> "%.1f MB".format(bytes.toDouble() / 1_048_576)
    }
}
