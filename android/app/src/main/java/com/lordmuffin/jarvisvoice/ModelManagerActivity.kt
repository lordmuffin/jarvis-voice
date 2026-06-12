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
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Observer
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.work.WorkInfo
import androidx.work.WorkManager

class ModelManagerActivity : AppCompatActivity() {

    private lateinit var mgr: LlmModelManager
    private lateinit var adapter: ModelAdapter
    private var pendingDownloadConfig: ModelConfig? = null

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
        supportActionBar?.title = "LLM Model"
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

    private fun observeAllDownloads() {
        ModelRegistry.MODELS.forEach { config ->
            WorkManager.getInstance(this)
                .getWorkInfosForUniqueWorkLiveData(ModelDownloadWorker.workName(config.id))
                .observe(this, Observer { infos ->
                    val info = infos?.firstOrNull() ?: return@Observer
                    if (info.state == WorkInfo.State.FAILED) {
                        val error = info.outputData.getString(ModelDownloadWorker.KEY_ERROR)
                            ?: "Download failed"
                        Toast.makeText(this, "${config.displayName}: $error", Toast.LENGTH_LONG).show()
                    }
                    adapter.updateDownloadState(config.id, info)
                })
        }
    }

    private fun onSelectModel(config: ModelConfig) {
        mgr.setActiveModel(config.id)
        VoiceOverlayService.instance?.reloadLlmModel()
        adapter.notifyDataSetChanged()
        Toast.makeText(this, "${config.displayName} selected", Toast.LENGTH_SHORT).show()
    }

    private fun onClearModel() {
        mgr.setActiveModel(ModelRegistry.NO_LLM)
        VoiceOverlayService.instance?.reloadLlmModel()
        adapter.notifyDataSetChanged()
        Toast.makeText(this, "LLM enhancement disabled", Toast.LENGTH_SHORT).show()
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
            .setTitle("Delete ${config.displayName}?")
            .setMessage("${config.fileSizeMb} MB will be freed.")
            .setPositiveButton("Delete") { _, _ ->
                mgr.deleteModel(config)
                VoiceOverlayService.instance?.reloadLlmModel()
                adapter.notifyDataSetChanged()
            }
            .setNegativeButton("Cancel", null)
            .show()
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

    // ── Adapter ──────────────────────────────────────────────────────────────

    inner class ModelAdapter : RecyclerView.Adapter<ModelAdapter.ModelVH>() {

        private val downloadStates = mutableMapOf<String, WorkInfo?>()

        fun updateDownloadState(modelId: String, info: WorkInfo) {
            downloadStates[modelId] = info
            val idx = ModelRegistry.MODELS.indexOfFirst { it.id == modelId }
            if (idx >= 0) notifyItemChanged(idx)
        }

        override fun getItemCount() = ModelRegistry.MODELS.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ModelVH =
            ModelVH(LayoutInflater.from(parent.context).inflate(R.layout.item_llm_model, parent, false))

        override fun onBindViewHolder(holder: ModelVH, position: Int) {
            val config     = ModelRegistry.MODELS[position]
            val isActive   = mgr.getActiveModelId() == config.id
            val isInstalled = mgr.isInstalled(config)
            val workInfo   = downloadStates[config.id]
            val isRunning  = workInfo?.state == WorkInfo.State.RUNNING
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
                tvName.text    = config.displayName
                tvDesc.text    = config.description
                tvDefault.visibility = if (config.isDefault) View.VISIBLE else View.GONE

                rbSelect.isChecked = isActive
                rbSelect.isEnabled = isInstalled

                tvStatus.text = when {
                    isDownloading -> "Downloading…"
                    isInstalled   -> {
                        val base = mgr.getStatusLabel(config)
                        val backend = LlmEnhancer.activeBackend.takeIf { it != "none" && isActive }
                        if (backend != null) "$base  [$backend]" else base
                    }
                    else          -> "${config.fileSizeMb} MB · min ${config.minRamGb} GB RAM"
                }

                // Download progress bar
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
                                      else "Connecting…"
                }

                // Primary action button
                when {
                    config.isAiCore -> {
                        btnAction.visibility = View.GONE
                        progressBar.visibility = View.GONE
                        tvProgress.visibility = View.GONE
                    }
                    isDownloading -> {
                        btnAction.visibility = View.VISIBLE
                        btnAction.text = "Cancel"
                        btnAction.backgroundTintList =
                            android.content.res.ColorStateList.valueOf(0xFF333333.toInt())
                        btnAction.setOnClickListener { onCancelDownload(config) }
                    }
                    isInstalled -> {
                        btnAction.visibility = View.VISIBLE
                        btnAction.text = if (isActive) "Remove" else "Delete"
                        btnAction.backgroundTintList =
                            android.content.res.ColorStateList.valueOf(0xFF442222.toInt())
                        btnAction.setOnClickListener { onDelete(config) }
                    }
                    else -> {
                        btnAction.visibility = View.VISIBLE
                        btnAction.text = "Download"
                        btnAction.backgroundTintList =
                            android.content.res.ColorStateList.valueOf(0xFF00F5D4.toInt())
                        btnAction.setTextColor(0xFF04130F.toInt())
                        btnAction.setOnClickListener { onDownload(config) }
                    }
                }

                rbSelect.setOnClickListener {
                    if (isInstalled) {
                        if (isActive) onClearModel() else onSelectModel(config)
                    }
                }
            }
        }
    }
}
