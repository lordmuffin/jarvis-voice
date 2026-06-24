package com.lordmuffin.jarvisvoice

import android.content.res.ColorStateList
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
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

class AgentTaskActivity : AppCompatActivity() {

    private lateinit var viewModel:  AgentTaskViewModel
    private lateinit var rvTasks:    RecyclerView
    private lateinit var llEmpty:    LinearLayout
    private lateinit var etPrompt:   EditText
    private lateinit var etTaskName: EditText
    private lateinit var spModel:    Spinner
    private lateinit var btnRun:     Button

    private val taskAdapter = TaskAdapter()
    private val uiScope     = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private var modelList: List<String> = listOf("local-default")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_agent_tasks)
        supportActionBar?.title = "Agent Tasks"
        BottomNav.wire(this, BottomNav.Tab.AGENT)

        rvTasks    = findViewById(R.id.rv_tasks)
        llEmpty    = findViewById(R.id.ll_empty)
        etPrompt   = findViewById(R.id.et_prompt)
        etTaskName = findViewById(R.id.et_task_name)
        spModel    = findViewById(R.id.sp_model)
        btnRun     = findViewById(R.id.btn_run_task)

        rvTasks.layoutManager = LinearLayoutManager(this)
        rvTasks.adapter = taskAdapter

        viewModel = ViewModelProvider(this)[AgentTaskViewModel::class.java]

        observeViewModel()

        btnRun.setOnClickListener { submitTask() }

        // Pre-fill from chat Delegate flow
        val initialPrompt = intent.getStringExtra(EXTRA_PROMPT) ?: ""
        val initialModel  = intent.getStringExtra(EXTRA_MODEL)  ?: ""
        if (initialPrompt.isNotBlank()) etPrompt.setText(initialPrompt)
        if (initialModel.isNotBlank()) selectModelInSpinner(initialModel)
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

    // ── Observe ───────────────────────────────────────────────────────────────

    private fun observeViewModel() {
        uiScope.launch {
            viewModel.tasks.collect { tasks ->
                taskAdapter.update(tasks)
                llEmpty.visibility = if (tasks.isEmpty()) View.VISIBLE else View.GONE
                rvTasks.visibility = if (tasks.isEmpty()) View.GONE   else View.VISIBLE
            }
        }
        uiScope.launch {
            viewModel.availableModels.collect { models ->
                modelList = models
                val adapter = ArrayAdapter(
                    this@AgentTaskActivity,
                    android.R.layout.simple_spinner_item,
                    models,
                ).also { it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item) }
                spModel.adapter = adapter
            }
        }
    }

    // ── Submit ────────────────────────────────────────────────────────────────

    private fun submitTask() {
        val prompt = etPrompt.text.toString().trim()
        if (prompt.isEmpty()) {
            etPrompt.error = "Enter a prompt"
            return
        }
        val name  = etTaskName.text.toString().trim()
        val model = spModel.selectedItem?.toString() ?: "local-default"

        viewModel.createTask(name, prompt, model)

        etPrompt.setText("")
        etTaskName.setText("")
        dismissKeyboard()

        // Scroll to top so user sees the new task appear
        rvTasks.scrollToPosition(0)
    }

    private fun selectModelInSpinner(model: String) {
        val idx = modelList.indexOf(model)
        if (idx >= 0) spModel.setSelection(idx)
    }

    private fun dismissKeyboard() {
        val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(etPrompt.windowToken, 0)
        etPrompt.clearFocus()
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
            h.tvModel.text   = task.model.take(22)
            h.tvStatus.text  = task.status.replaceFirstChar { it.titlecase() }
            h.tvElapsed.text = task.elapsedLabel()

            val dotColor = statusColor(task.status)
            h.tvDot.backgroundTintList = ColorStateList.valueOf(getColor(dotColor))

            if (task.output.isNotEmpty()) {
                h.tvExpand.visibility = View.VISIBLE
                if (isOpen) {
                    h.tvOutput.maxLines   = Int.MAX_VALUE
                    h.tvOutput.visibility = View.VISIBLE
                    h.tvOutput.text       = task.output
                    h.tvExpand.text       = "Hide output ▲"
                } else {
                    h.tvOutput.visibility = View.GONE
                    h.tvExpand.text       = "Show output ▼"
                }
            } else {
                h.tvOutput.visibility = View.GONE
                h.tvExpand.visibility = View.GONE
            }

            val toggleExpand = View.OnClickListener {
                if (task.id in expanded) expanded.remove(task.id) else expanded.add(task.id)
                notifyItemChanged(pos)
            }
            h.tvExpand.setOnClickListener(toggleExpand)
            h.itemView.setOnClickListener(toggleExpand)

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

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun statusColor(status: String) = when (status) {
        "running"   -> R.color.jv_warning
        "done"      -> R.color.jv_accent
        "failed"    -> R.color.jv_error
        else        -> R.color.jv_text2
    }

    private fun AgentTask.elapsedLabel(): String {
        val ms = elapsedMs
        return when {
            ms == null && !isTerminal -> "running…"
            ms == null -> ""
            ms < 1_000  -> "${ms}ms"
            ms < 60_000 -> "${"%.1f".format(ms / 1000.0)}s"
            else        -> "${ms / 60_000}m ${(ms % 60_000) / 1000}s"
        }
    }

    companion object {
        const val EXTRA_PROMPT = "extra_prompt"
        const val EXTRA_MODEL  = "extra_model"
    }
}
