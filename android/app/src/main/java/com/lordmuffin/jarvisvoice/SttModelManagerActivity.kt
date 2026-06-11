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
import androidx.lifecycle.Observer
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.lordmuffin.jarvisvoice.speech.SherpaOnnxSpeechEngine
import com.lordmuffin.jarvisvoice.speech.SttDownloadWorker
import com.lordmuffin.jarvisvoice.speech.SttModelConfig
import com.lordmuffin.jarvisvoice.speech.SttModelManager
import com.lordmuffin.jarvisvoice.speech.SttModelRegistry

class SttModelManagerActivity : AppCompatActivity() {

    private lateinit var sttMgr: SttModelManager
    private lateinit var adapter: SttModelAdapter

    // Tracks which model is currently being downloaded (one at a time)
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

        observeDownloads()
    }

    /**
     * Observe WorkManager state for each known STT model. WorkManager LiveData
     * delivers the last known state immediately on observe(), so the UI restores
     * correctly after the activity is recreated mid-download.
     */
    private fun observeDownloads() {
        for (config in SttModelRegistry.MODELS) {
            WorkManager.getInstance(this)
                .getWorkInfosForUniqueWorkLiveData(SttDownloadWorker.workName(config.id))
                .observe(this, Observer { infos ->
                    val info = infos.firstOrNull() ?: return@Observer
                    when (info.state) {
                        WorkInfo.State.ENQUEUED,
                        WorkInfo.State.RUNNING -> {
                            downloadingId = config.id
                            val pct        = info.progress.getInt(SttDownloadWorker.KEY_PROGRESS, -1)
                            val extracting = info.progress.getBoolean(SttDownloadWorker.KEY_EXTRACTING, false)
                            val speedBps   = info.progress.getLong(SttDownloadWorker.KEY_SPEED_BPS, 0L)
                            val etaSec     = info.progress.getLong(SttDownloadWorker.KEY_ETA_SEC, -1L)
                            if (extracting) adapter.updateExtracting(config.id)
                            else adapter.updateProgress(config.id, pct, speedBps, etaSec)
                        }
                        WorkInfo.State.SUCCEEDED -> {
                            if (downloadingId == config.id) downloadingId = null
                            adapter.clearProgress(config.id)
                            if (!sttMgr.isInstalled(config)) return@Observer
                            sttMgr.setActiveModel(config.id)
                            VoiceOverlayService.instance?.reloadSpeechEngine()
                            adapter.notifyDataSetChanged()
                            Toast.makeText(this, "${config.displayName} ready", Toast.LENGTH_SHORT).show()
                        }
                        WorkInfo.State.FAILED -> {
                            if (downloadingId == config.id) downloadingId = null
                            adapter.clearProgress(config.id)
                            adapter.notifyDataSetChanged()
                            val err = info.outputData.getString(SttDownloadWorker.KEY_ERROR) ?: "Unknown error"
                            Toast.makeText(this, "Download failed: $err", Toast.LENGTH_LONG).show()
                        }
                        WorkInfo.State.CANCELLED -> {
                            if (downloadingId == config.id) downloadingId = null
                            adapter.clearProgress(config.id)
                            adapter.notifyDataSetChanged()
                        }
                        else -> { /* BLOCKED — no UI action needed */ }
                    }
                })
        }
    }

    override fun onResume() {
        super.onResume()
        adapter.notifyDataSetChanged()
    }

    // No cancel in onDestroy — WorkManager download runs until completion or explicit cancel

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
        SttDownloadWorker.enqueue(this, config.id)
    }

    private fun onCancelDownload(config: SttModelConfig) {
        SttDownloadWorker.cancel(this, config.id)
        // Observer will clear downloadingId and update adapter when CANCELLED state arrives
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

    internal data class Progress(val pct: Int, val speedBps: Long, val etaSec: Long)

    // ── Adapter ──────────────────────────────────────────────────────────────

    inner class SttModelAdapter : RecyclerView.Adapter<SttModelAdapter.SttModelVH>() {

        private val progressMap   = mutableMapOf<String, Progress>()
        private val extractingSet = mutableSetOf<String>()

        fun updateProgress(id: String, pct: Int, speedBps: Long = 0L, etaSec: Long = -1L) {
            extractingSet.remove(id)
            progressMap[id] = Progress(pct, speedBps, etaSec)
            notifyItem(id)
        }

        fun updateExtracting(id: String) {
            extractingSet.add(id)
            progressMap.remove(id)
            notifyItem(id)
        }

        fun clearProgress(id: String) {
            progressMap.remove(id)
            extractingSet.remove(id)
            notifyItem(id)
        }

        private fun notifyItem(id: String) {
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
            val isDownloading = downloadingId == config.id
            val isExtracting  = extractingSet.contains(config.id)
            holder.bind(config, isActive, isInstalled, isDownloading, isExtracting, progressMap[config.id])
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

            internal fun bind(
                config: SttModelConfig,
                isActive: Boolean,
                isInstalled: Boolean,
                isDownloading: Boolean,
                isExtracting: Boolean,
                prog: Progress?
            ) {
                val pct = prog?.pct
                tvName.text  = config.displayName
                tvDesc.text  = config.description
                tvDefault.visibility = if (config.isDefault) View.VISIBLE else View.GONE

                rbSelect.isChecked = isActive
                rbSelect.isEnabled = isInstalled && !isDownloading

                tvStatus.text = when {
                    isExtracting  -> "Extracting…"
                    isDownloading -> "Downloading…"
                    isInstalled   -> {
                        val base = sttMgr.getStatusLabel(config)
                        val sttBackend = (VoiceOverlayService.instance?.speechEngine as? SherpaOnnxSpeechEngine)
                            ?.activeProvider?.takeIf { isActive }
                        if (sttBackend != null) "$base  [$sttBackend]" else base
                    }
                    else          -> "${config.fileSizeMb} MB"
                }

                val busy = isDownloading || isExtracting
                progressBar.visibility = if (busy) View.VISIBLE else View.GONE
                tvProgress.visibility  = if (busy) View.VISIBLE else View.GONE
                when {
                    isExtracting -> {
                        progressBar.isIndeterminate = true
                        tvProgress.text = "Extracting…"
                    }
                    isDownloading -> {
                        progressBar.max = 100
                        if (pct != null && pct >= 0) {
                            progressBar.isIndeterminate = false
                            progressBar.progress = pct
                            tvProgress.text = buildProgressLabel(pct, prog.speedBps, prog.etaSec)
                        } else {
                            progressBar.isIndeterminate = true
                            tvProgress.text = "Connecting…"
                        }
                    }
                }

                when {
                    busy -> {
                        btnAction.text = "Cancel"
                        btnAction.backgroundTintList =
                            android.content.res.ColorStateList.valueOf(0xFF333333.toInt())
                        btnAction.isEnabled = true
                        btnAction.setOnClickListener { onCancelDownload(config) }
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
                            android.content.res.ColorStateList.valueOf(0xFF00F5D4.toInt())
                        btnAction.setTextColor(0xFF04130F.toInt())
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

    private fun buildProgressLabel(pct: Int, speedBps: Long, etaSec: Long): String {
        val sb = StringBuilder("$pct%")
        if (speedBps > 0) sb.append("  ·  ").append(formatSpeed(speedBps))
        if (etaSec >= 0) sb.append("  ·  ETA ").append(formatEta(etaSec))
        return sb.toString()
    }

    private fun formatSpeed(bps: Long): String = when {
        bps < 1_024         -> "$bps B/s"
        bps < 1_048_576     -> "${bps / 1_024} KB/s"
        else                -> "%.1f MB/s".format(bps.toDouble() / 1_048_576)
    }

    private fun formatEta(sec: Long): String = when {
        sec < 60    -> "${sec}s"
        sec < 3_600 -> "${sec / 60}m ${sec % 60}s"
        else        -> "${sec / 3_600}h ${(sec % 3_600) / 60}m"
    }
}
