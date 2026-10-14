package com.lordmuffin.jarvisvoice

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ProgressBar
import android.widget.RadioButton
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Observer
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.lordmuffin.jarvisvoice.ui.InstButtonKind
import com.lordmuffin.jarvisvoice.ui.InstToast
import com.lordmuffin.jarvisvoice.ui.setInstKind
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
        supportActionBar?.title = getString(R.string.models_stt_model)

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
                            InstToast.show(this, getString(R.string.models_toast_ready, config.displayName))
                        }
                        WorkInfo.State.FAILED -> {
                            if (downloadingId == config.id) downloadingId = null
                            adapter.clearProgress(config.id)
                            adapter.notifyDataSetChanged()
                            val err = info.outputData.getString(SttDownloadWorker.KEY_ERROR) ?: getString(R.string.models_unknown_error)
                            InstToast.show(this, getString(R.string.models_toast_download_failed, err))
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
        InstToast.show(this, getString(R.string.models_toast_selected, config.displayName))
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
            .setTitle(getString(R.string.models_delete_title, config.displayName))
            .setMessage(getString(R.string.models_delete_message, config.fileSizeMb))
            .setPositiveButton(R.string.models_delete) { _, _ ->
                sttMgr.deleteModel(config)
                VoiceOverlayService.instance?.reloadSpeechEngine()
                adapter.notifyDataSetChanged()
            }
            .setNegativeButton(R.string.models_cancel, null)
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
                    isExtracting  -> getString(R.string.models_extracting)
                    isDownloading -> getString(R.string.models_downloading)
                    isInstalled   -> {
                        val base = sttMgr.getStatusLabel(config)
                        val sttBackend = (VoiceOverlayService.instance?.speechEngine as? SherpaOnnxSpeechEngine)
                            ?.activeProvider?.takeIf { isActive }
                        if (sttBackend != null) getString(R.string.models_status_installed_backend, base, sttBackend) else base
                    }
                    else          -> getString(R.string.models_status_not_installed_stt, config.fileSizeMb)
                }
                // Why the radio and the action are greyed out.
                val disabledHint = when {
                    !isInstalled && !isDownloading && !isExtracting && downloadingId != null -> R.string.models_hint_other_download
                    !isInstalled && !isDownloading && !isExtracting -> R.string.models_hint_download_first
                    isInstalled && downloadingId != null -> R.string.models_hint_other_download
                    else -> null
                }
                if (disabledHint != null) {
                    tvStatus.text = getString(R.string.models_hint_join, tvStatus.text, getString(disabledHint))
                }

                val busy = isDownloading || isExtracting
                progressBar.visibility = if (busy) View.VISIBLE else View.GONE
                tvProgress.visibility  = if (busy) View.VISIBLE else View.GONE
                when {
                    isExtracting -> {
                        progressBar.isIndeterminate = true
                        tvProgress.text = getString(R.string.models_extracting)
                    }
                    isDownloading -> {
                        progressBar.max = 100
                        if (pct != null && pct >= 0) {
                            progressBar.isIndeterminate = false
                            progressBar.progress = pct
                            tvProgress.text = buildProgressLabel(pct, prog.speedBps, prog.etaSec)
                        } else {
                            progressBar.isIndeterminate = true
                            tvProgress.text = getString(R.string.models_connecting)
                        }
                    }
                }

                when {
                    busy -> {
                        btnAction.text = getString(R.string.models_cancel)
                        btnAction.setInstKind(InstButtonKind.SECONDARY)
                        btnAction.isEnabled = true
                        btnAction.setOnClickListener { onCancelDownload(config) }
                    }
                    isInstalled -> {
                        btnAction.text = getString(if (isActive) R.string.models_remove else R.string.models_delete)
                        btnAction.setInstKind(InstButtonKind.DANGER)
                        btnAction.isEnabled = downloadingId == null
                        btnAction.setOnClickListener { onDelete(config) }
                    }
                    else -> {
                        btnAction.text = getString(R.string.models_download)
                        btnAction.setInstKind(InstButtonKind.PRIMARY)
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
        val sb = StringBuilder(getString(R.string.models_progress_pct, pct))
        if (speedBps > 0) sb.append(getString(R.string.models_progress_speed, formatSpeed(speedBps)))
        if (etaSec >= 0) sb.append(getString(R.string.models_progress_eta, formatEta(etaSec)))
        return sb.toString()
    }

    private fun formatSpeed(bps: Long): String = when {
        bps < 1_024         -> getString(R.string.models_speed_bps, bps)
        bps < 1_048_576     -> getString(R.string.models_speed_kbps, bps / 1_024)
        else                -> getString(R.string.models_speed_mbps, bps.toDouble() / 1_048_576)
    }

    private fun formatEta(sec: Long): String = when {
        sec < 60    -> getString(R.string.models_eta_s, sec)
        sec < 3_600 -> getString(R.string.models_eta_ms, sec / 60, sec % 60)
        else        -> getString(R.string.models_eta_hm, sec / 3_600, (sec % 3_600) / 60)
    }
}
