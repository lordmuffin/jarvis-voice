package com.lordmuffin.jarvisvoice

import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ProgressBar
import android.widget.RadioButton
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Observer
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.google.mlkit.genai.common.DownloadStatus
import com.google.mlkit.genai.common.FeatureStatus
import com.lordmuffin.jarvisvoice.ui.InstButtonKind
import com.lordmuffin.jarvisvoice.ui.InstToast
import com.lordmuffin.jarvisvoice.ui.setInstKind
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ModelManagerActivity : AppCompatActivity() {

    private lateinit var mgr: LlmModelManager
    private lateinit var adapter: ModelAdapter
    private var pendingDownloadConfig: ModelConfig? = null
    private var aiCoreDownloadJob: Job? = null

    private val requestNotificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            pendingDownloadConfig?.let { config ->
                pendingDownloadConfig = null
                ModelDownloadWorker.enqueue(this, config.id)
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_model_manager)
        supportActionBar?.title = getString(R.string.models_llm_model)
        BottomNav.wire(this, BottomNav.Tab.MODELS)

        mgr = LlmModelManager(this)

        val rv = findViewById<RecyclerView>(R.id.rv_models)
        rv.layoutManager = LinearLayoutManager(this)
        adapter = ModelAdapter()
        rv.adapter = adapter

        observeAllDownloads()
    }

    override fun onResume() {
        super.onResume()
        adapter.notifyDataSetChanged()
    }

    override fun onDestroy() {
        super.onDestroy()
        aiCoreDownloadJob?.cancel()
    }

    private fun observeAllDownloads() {
        ModelRegistry.MODELS.forEach { config ->
            WorkManager.getInstance(this)
                .getWorkInfosForUniqueWorkLiveData(ModelDownloadWorker.workName(config.id))
                .observe(this, Observer { infos ->
                    val info = infos?.firstOrNull() ?: return@Observer
                    if (info.state == WorkInfo.State.FAILED) {
                        val error = info.outputData.getString(ModelDownloadWorker.KEY_ERROR)
                            ?: getString(R.string.models_download_failed_default)
                        InstToast.show(this, getString(R.string.models_toast_model_error, config.displayName, error))
                    }
                    adapter.updateDownloadState(config.id, info)
                })
        }
    }

    private fun onSelectModel(config: ModelConfig) {
        mgr.setActiveModel(config.id)
        VoiceOverlayService.instance?.reloadLlmModel()
        adapter.notifyDataSetChanged()
        InstToast.show(this, getString(R.string.models_toast_selected, config.displayName))
    }

    private fun onClearModel() {
        mgr.setActiveModel(ModelRegistry.NO_LLM)
        VoiceOverlayService.instance?.reloadLlmModel()
        adapter.notifyDataSetChanged()
        InstToast.show(this, R.string.models_toast_llm_disabled)
    }

    private fun onDownload(config: ModelConfig) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
        ) {
            pendingDownloadConfig = config
            requestNotificationPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        } else {
            ModelDownloadWorker.enqueue(this, config.id)
        }
    }

    private fun onCancelDownload(config: ModelConfig) {
        ModelDownloadWorker.cancel(this, config.id)
        adapter.notifyDataSetChanged()
    }

    private fun onDelete(config: ModelConfig) {
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.models_delete_title, config.displayName))
            .setMessage(getString(R.string.models_delete_message, config.fileSizeMb))
            .setPositiveButton(R.string.models_delete) { _, _ ->
                mgr.deleteModel(config)
                VoiceOverlayService.instance?.reloadLlmModel()
                adapter.notifyDataSetChanged()
            }
            .setNegativeButton(R.string.models_cancel, null)
            .show()
    }

    private fun onDownloadAiCore() {
        aiCoreDownloadJob?.cancel()
        adapter.setAiCoreDownloading(true, 0L, 0L)
        var totalBytes = 0L
        aiCoreDownloadJob = lifecycleScope.launch {
            try {
                val flow: Flow<DownloadStatus>? = withContext(Dispatchers.IO) { AiCoreEnhancer.downloadFlow() }
                if (flow == null) {
                    adapter.setAiCoreDownloading(false)
                    InstToast.show(this@ModelManagerActivity, R.string.models_toast_download_start_failed)
                    return@launch
                }
                flow.collect { status ->
                    when (status) {
                        is DownloadStatus.DownloadStarted -> {
                            totalBytes = status.bytesToDownload
                            adapter.setAiCoreDownloading(true, 0L, totalBytes)
                        }
                        is DownloadStatus.DownloadProgress -> {
                            adapter.setAiCoreDownloading(true, status.totalBytesDownloaded, totalBytes)
                        }
                        is DownloadStatus.DownloadCompleted -> {
                            adapter.setAiCoreDownloading(false)
                            // Re-init on background thread — status should now be AVAILABLE
                            withContext(Dispatchers.IO) { AiCoreEnhancer.initialize() }
                            adapter.notifyDataSetChanged()
                            if (AiCoreEnhancer.isReady()) {
                                VoiceOverlayService.instance?.reloadLlmModel()
                                InstToast.show(this@ModelManagerActivity, R.string.models_toast_gemini_ready)
                            }
                        }
                        is DownloadStatus.DownloadFailed -> {
                            adapter.setAiCoreDownloading(false)
                            InstToast.show(this@ModelManagerActivity,
                                getString(R.string.models_toast_download_failed, status.e.message))
                        }
                    }
                }
            } catch (e: Exception) {
                adapter.setAiCoreDownloading(false)
                if (e !is kotlinx.coroutines.CancellationException) {
                    InstToast.show(this@ModelManagerActivity,
                        getString(R.string.models_toast_download_error, e.message))
                }
            }
        }
    }

    private fun onCancelAiCoreDownload() {
        aiCoreDownloadJob?.cancel()
        aiCoreDownloadJob = null
        adapter.setAiCoreDownloading(false)
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

    // ── Adapter ──────────────────────────────────────────────────────────────

    inner class ModelAdapter : RecyclerView.Adapter<ModelAdapter.ModelVH>() {

        private val downloadStates = mutableMapOf<String, WorkInfo?>()

        // AI Core download state (separate from WorkManager-based downloads)
        private var aiCoreDownloading = false
        private var aiCoreBytesDownloaded = 0L
        private var aiCoreBytesTotal = 0L

        fun setAiCoreDownloading(downloading: Boolean, downloaded: Long = 0L, total: Long = 0L) {
            aiCoreDownloading = downloading
            aiCoreBytesDownloaded = downloaded
            aiCoreBytesTotal = total
            val idx = ModelRegistry.MODELS.indexOfFirst { it.isAiCore }
            if (idx >= 0) notifyItemChanged(idx)
        }

        fun updateDownloadState(modelId: String, info: WorkInfo) {
            downloadStates[modelId] = info
            val idx = ModelRegistry.MODELS.indexOfFirst { it.id == modelId }
            if (idx >= 0) notifyItemChanged(idx)
        }

        override fun getItemCount() = ModelRegistry.MODELS.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ModelVH =
            ModelVH(LayoutInflater.from(parent.context).inflate(R.layout.item_llm_model, parent, false))

        override fun onBindViewHolder(holder: ModelVH, position: Int) {
            val config      = ModelRegistry.MODELS[position]
            val isActive    = mgr.getActiveModelId() == config.id
            val isInstalled = mgr.isInstalled(config)
            val workInfo    = downloadStates[config.id]
            val isRunning   = workInfo?.state == WorkInfo.State.RUNNING
                           || workInfo?.state == WorkInfo.State.ENQUEUED

            holder.bind(config, isActive, isInstalled, isRunning, workInfo)
        }

        inner class ModelVH(view: View) : RecyclerView.ViewHolder(view) {
            val rbSelect:      RadioButton = view.findViewById(R.id.rb_select)
            val tvName:        TextView    = view.findViewById(R.id.tv_model_name)
            val tvDefault:     TextView    = view.findViewById(R.id.tv_default_badge)
            val tvDesc:        TextView    = view.findViewById(R.id.tv_model_desc)
            val tvStatus:      TextView    = view.findViewById(R.id.tv_model_status)
            val btnAction:     Button      = view.findViewById(R.id.btn_action)
            val progressBar:   ProgressBar = view.findViewById(R.id.progress_download)
            val tvProgress:    TextView    = view.findViewById(R.id.tv_progress_label)

            fun bind(
                config: ModelConfig,
                isActive: Boolean,
                isInstalled: Boolean,
                isDownloading: Boolean,
                workInfo: WorkInfo?
            ) {
                tvName.text = config.displayName
                tvDesc.text = config.description
                tvDefault.visibility = if (config.isDefault) View.VISIBLE else View.GONE
                rbSelect.isChecked = isActive
                rbSelect.isEnabled = isInstalled

                if (config.isAiCore) {
                    bindAiCore(isActive)
                    return
                }

                tvStatus.text = when {
                    isDownloading -> getString(R.string.models_downloading)
                    isInstalled   -> {
                        val base = mgr.getStatusLabel(config)
                        val backend = LlmEnhancer.activeBackend.takeIf { it != "none" && isActive }
                        if (backend != null) getString(R.string.models_status_installed_backend, base, backend) else base
                    }
                    else          -> getString(R.string.models_status_not_installed_llm, config.fileSizeMb, config.minRamGb)
                }

                val progress = workInfo?.progress?.getInt(ModelDownloadWorker.KEY_PROGRESS, -1) ?: -1
                val speedBps = workInfo?.progress?.getLong(ModelDownloadWorker.KEY_SPEED_BPS, 0L) ?: 0L
                val etaSec   = workInfo?.progress?.getLong(ModelDownloadWorker.KEY_ETA_SEC, -1L) ?: -1L
                progressBar.visibility = if (isDownloading) View.VISIBLE else View.GONE
                tvProgress.visibility  = if (isDownloading) View.VISIBLE else View.GONE
                if (isDownloading) {
                    progressBar.isIndeterminate = progress < 0
                    progressBar.max = 100
                    progressBar.progress = progress.coerceIn(0, 100)
                    tvProgress.text = if (progress >= 0) buildProgressLabel(progress, speedBps, etaSec)
                                      else getString(R.string.models_connecting)
                }

                when {
                    isDownloading -> {
                        btnAction.visibility = View.VISIBLE
                        btnAction.text = getString(R.string.models_cancel)
                        btnAction.setInstKind(InstButtonKind.SECONDARY)
                        btnAction.setOnClickListener { onCancelDownload(config) }
                    }
                    isInstalled -> {
                        btnAction.visibility = View.VISIBLE
                        btnAction.text = getString(if (isActive) R.string.models_remove else R.string.models_delete)
                        btnAction.setInstKind(InstButtonKind.DANGER)
                        btnAction.setOnClickListener { onDelete(config) }
                    }
                    else -> {
                        btnAction.visibility = View.VISIBLE
                        btnAction.text = getString(R.string.models_download)
                        btnAction.setInstKind(InstButtonKind.PRIMARY)
                        btnAction.setOnClickListener { onDownload(config) }
                    }
                }

                rbSelect.setOnClickListener {
                    if (isInstalled) {
                        if (isActive) onClearModel() else onSelectModel(config)
                    }
                }
            }

            private fun bindAiCore(isActive: Boolean) {
                val status = AiCoreEnhancer.lastStatus
                val isDownloading = aiCoreDownloading

                tvStatus.text = when {
                    isDownloading -> {
                        if (aiCoreBytesTotal > 0) {
                            val pct = ((aiCoreBytesDownloaded.toFloat() / aiCoreBytesTotal) * 100).toInt()
                            val mb = aiCoreBytesDownloaded / (1024 * 1024)
                            val totalMb = aiCoreBytesTotal / (1024 * 1024)
                            getString(R.string.models_aicore_downloading_pct, pct, mb, totalMb)
                        } else getString(R.string.models_downloading)
                    }
                    AiCoreEnhancer.isReady() -> {
                        val backend = if (isActive) getString(R.string.models_aicore_backend_suffix) else ""
                        getString(R.string.models_aicore_ready, backend)
                    }
                    status == FeatureStatus.UNAVAILABLE -> getString(R.string.models_aicore_unavailable)
                    status == -1 -> getString(R.string.models_aicore_checking)
                    else -> getString(R.string.models_aicore_download_required)
                }

                progressBar.visibility = if (isDownloading) View.VISIBLE else View.GONE
                tvProgress.visibility  = View.GONE
                if (isDownloading) {
                    val pct = if (aiCoreBytesTotal > 0)
                        ((aiCoreBytesDownloaded.toFloat() / aiCoreBytesTotal) * 100).toInt()
                    else -1
                    progressBar.isIndeterminate = pct < 0
                    progressBar.max = 100
                    progressBar.progress = pct.coerceIn(0, 100)
                }

                when {
                    isDownloading -> {
                        btnAction.visibility = View.VISIBLE
                        btnAction.text = getString(R.string.models_cancel)
                        btnAction.setInstKind(InstButtonKind.SECONDARY)
                        btnAction.setOnClickListener { onCancelAiCoreDownload() }
                    }
                    AiCoreEnhancer.isReady() || status == FeatureStatus.UNAVAILABLE || status == -1 -> {
                        btnAction.visibility = View.GONE
                    }
                    else -> {
                        // DOWNLOADABLE state
                        btnAction.visibility = View.VISIBLE
                        btnAction.text = getString(R.string.models_download)
                        btnAction.setInstKind(InstButtonKind.PRIMARY)
                        btnAction.setOnClickListener { onDownloadAiCore() }
                    }
                }

                rbSelect.setOnClickListener {
                    if (isActive) onClearModel() else onSelectModel(ModelRegistry.MODELS.first { it.isAiCore })
                }
            }
        }
    }
}
