package com.lordmuffin.jarvisvoice

import android.content.res.ColorStateList
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.ViewModelProvider
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.lordmuffin.jarvisvoice.chat.AgentTask
import com.lordmuffin.jarvisvoice.chat.AgentTaskViewModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class AgentTaskActivity : AppCompatActivity() {

    private lateinit var viewModel: AgentTaskViewModel
    private lateinit var rvTasks:   RecyclerView
    private lateinit var llEmpty:   LinearLayout
    private lateinit var fabNew:    Button
    private val adapter = TaskAdapter()
    private val uiScope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_agent_tasks)
        supportActionBar?.title = "Agent Tasks"
        BottomNav.wire(this, BottomNav.Tab.AGENT)

        rvTasks = findViewById(R.id.rv_tasks)
        llEmpty = findViewById(R.id.ll_empty)
        fabNew  = findViewById(R.id.fab_new_task)

        rvTasks.layoutManager = LinearLayoutManager(this)
        rvTasks.adapter = adapter

        viewModel = ViewModelProvider(this)[AgentTaskViewModel::class.java]

        // Pre-fill prompt and model if launched from chat (Delegate flow)
        val initialPrompt = intent.getStringExtra(EXTRA_PROMPT) ?: ""
        val initialModel  = intent.getStringExtra(EXTRA_MODEL)  ?: ""

        uiScope.launch {
            viewModel.tasks.collect { tasks ->
                adapter.update(tasks)
                llEmpty.visibility = if (tasks.isEmpty()) View.VISIBLE else View.GONE
                rvTasks.visibility = if (tasks.isEmpty()) View.GONE   else View.VISIBLE
            }
        }

        fabNew.setOnClickListener { showCreateDialog("", "", "") }

        // If launched with a pre-filled prompt, open the dialog immediately
        if (initialPrompt.isNotBlank()) {
            showCreateDialog(
                prompt        = initialPrompt,
                selectedModel = initialModel,
            )
        }
    }

    override fun onResume() {
        super.onResume()
        viewModel.startPolling()
    }

    override fun onPause() {
        super.onPause()
        viewModel.stopPolling()
    }

    override fun onDestroy() {
        super.onDestroy()
        uiScope.cancel()
    }

    // ── Create task dialog ────────────────────────────────────────────────────

    private fun showCreateDialog(
        prompt: String = "",
        name: String = "",
        selectedModel: String = "",
    ) {
        val view = layoutInflater.inflate(R.layout.dialog_create_task, null)
        val etName    = view.findViewById<EditText>(R.id.et_task_name)
        val etPrompt  = view.findViewById<EditText>(R.id.et_task_prompt)
        val spModel   = view.findViewById<Spinner>(R.id.sp_task_model)

        etPrompt.setText(prompt)
        etName.setText(name)

        // Populate model spinner
        val models = viewModel.tasks.value
            .map { it.model }
            .distinct()
            .toMutableList()

        // Also pull from a companion list if available
        val knownModels = listOf(
            "local-default",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
            "gpt-4o",
            "gpt-4o-mini",
        ) + models
        val uniqueModels = knownModels.distinct()

        val spinnerAdapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_item,
            uniqueModels,
        ).also { it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item) }
        spModel.adapter = spinnerAdapter

        val defaultIndex = uniqueModels.indexOf(selectedModel.ifBlank { "local-default" })
        if (defaultIndex >= 0) spModel.setSelection(defaultIndex)

        AlertDialog.Builder(this)
            .setTitle("New Agent Task")
            .setView(view)
            .setPositiveButton("Delegate") { _, _ ->
                val taskPrompt = etPrompt.text.toString().trim()
                val taskName   = etName.text.toString().trim()
                val taskModel  = spModel.selectedItem?.toString() ?: "local-default"
                if (taskPrompt.isNotEmpty()) {
                    viewModel.createTask(taskName, taskPrompt, taskModel)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ── Adapter ───────────────────────────────────────────────────────────────

    private inner class TaskAdapter : RecyclerView.Adapter<TaskViewHolder>() {
        private var items: List<AgentTask> = emptyList()
        private val expanded = mutableSetOf<String>()

        fun update(newItems: List<AgentTask>) {
            items = newItems
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            TaskViewHolder(LayoutInflater.from(parent.context)
                .inflate(R.layout.item_agent_task, parent, false))

        override fun getItemCount() = items.size

        override fun onBindViewHolder(h: TaskViewHolder, pos: Int) {
            val task   = items[pos]
            val isOpen = task.id in expanded

            h.tvName.text    = task.name
            h.tvModel.text   = task.model.take(20)
            h.tvStatus.text  = task.status.replaceFirstChar { it.titlecase() }
            h.tvElapsed.text = task.elapsedLabel()

            // Status dot color
            val dotColor = statusColor(task.status)
            h.tvDot.backgroundTintList = ColorStateList.valueOf(getColor(dotColor))

            // Output
            if (task.output.isNotEmpty()) {
                h.tvExpand.visibility = View.VISIBLE
                if (isOpen) {
                    h.tvOutput.maxLines  = Int.MAX_VALUE
                    h.tvOutput.visibility = View.VISIBLE
                    h.tvOutput.text      = task.output
                    h.tvExpand.text      = "Hide output ▲"
                } else {
                    h.tvOutput.visibility = View.GONE
                    h.tvExpand.text      = "Show output ▼"
                }
            } else {
                h.tvOutput.visibility  = View.GONE
                h.tvExpand.visibility  = View.GONE
            }

            h.tvExpand.setOnClickListener {
                if (task.id in expanded) expanded.remove(task.id) else expanded.add(task.id)
                notifyItemChanged(pos)
            }
            h.itemView.setOnClickListener {
                if (task.id in expanded) expanded.remove(task.id) else expanded.add(task.id)
                notifyItemChanged(pos)
            }
            h.btnDelete.setOnClickListener {
                AlertDialog.Builder(this@AgentTaskActivity)
                    .setTitle("Remove task?")
                    .setMessage("\"${task.name}\" will be cancelled and removed.")
                    .setPositiveButton("Remove") { _, _ -> viewModel.deleteTask(task.id) }
                    .setNegativeButton("Cancel", null)
                    .show()
            }
        }
    }

    private inner class TaskViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val tvDot:     View     = view.findViewById(R.id.tv_status_dot)
        val tvName:    TextView = view.findViewById(R.id.tv_task_name)
        val tvModel:   TextView = view.findViewById(R.id.tv_task_model)
        val tvStatus:  TextView = view.findViewById(R.id.tv_task_status)
        val tvElapsed: TextView = view.findViewById(R.id.tv_task_elapsed)
        val tvOutput:  TextView = view.findViewById(R.id.tv_task_output)
        val tvExpand:  TextView = view.findViewById(R.id.tv_expand)
        val btnDelete: Button   = view.findViewById(R.id.btn_task_delete)
    }

    private fun statusColor(status: String) = when (status) {
        "queued"    -> R.color.jv_text2
        "running"   -> R.color.jv_warning
        "done"      -> R.color.jv_accent
        "failed"    -> R.color.jv_error
        "cancelled" -> R.color.jv_text2
        else        -> R.color.jv_text2
    }

    private fun AgentTask.elapsedLabel(): String {
        val ms = elapsedMs
        return when {
            ms == null && !isTerminal -> "running…"
            ms == null -> ""
            ms < 1_000 -> "${ms}ms"
            ms < 60_000 -> "${"%.1f".format(ms / 1000.0)}s"
            else -> "${ms / 60_000}m ${(ms % 60_000) / 1000}s"
        }
    }

    companion object {
        const val EXTRA_PROMPT = "extra_prompt"
        const val EXTRA_MODEL  = "extra_model"
    }
}
