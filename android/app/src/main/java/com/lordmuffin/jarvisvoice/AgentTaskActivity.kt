package com.lordmuffin.jarvisvoice

import android.app.Dialog
import android.content.res.ColorStateList
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.Window
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

    private fun submitTask() {
        val prompt      = etPrompt.text.toString().trim()
        val taskName    = etTaskName.text.toString().trim()
        val selectedIdx = spModel.selectedItemPosition
        val model       = modelList.getOrElse(selectedIdx) { "local-default" }
        if (prompt.isEmpty()) return
        uiScope.launch {
            val ok = viewModel.createTask(taskName, prompt, model)
            if (ok) {
                etPrompt.text?.clear()
                etTaskName.text?.clear()
            } else {
                _error.value = "Failed to create task — check server connectivity"
            }
        }
    }

    fun selectModelInSpinner(modelName: String) {
        val idx = modelList.indexOf(modelName)
        if (idx >= 0) spModel.setSelection(idx)
    }

    // ── Task RecyclerView adapter ─────────────────────────────────────────────

    private var expanded: MutableSet<String> = mutableSetOf()

    private inner class TaskAdapter : RecyclerView.Adapter<TaskViewHolder>() {
        private var items: List<AgentTask> = emptyList()

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

            val hasOutput = task.output.isNotEmpty()
            h.llActions.visibility = if (hasOutput || task.isTerminal) View.VISIBLE else View.GONE

            if (hasOutput) {
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
                h.tvExpand.text       = ""
            }

            val toggleExpand = View.OnClickListener {
                if (task.id in expanded) expanded.remove(task.id) else expanded.add(task.id)
                notifyItemChanged(pos)
            }
            h.tvExpand.setOnClickListener(toggleExpand)
            h.itemView.setOnClickListener(toggleExpand)

            // Reply button — opens full-screen conversation window
            h.btnReply.setOnClickListener { showConversationSheet(task) }

            h.btnDelete.setOnClickListener {
                viewModel.deleteTask(task.id)
                expanded.remove(task.id)
            }
        }

        private fun statusColor(status: String): Int = when (status) {
            "queued"    -> R.color.jv_warning
            "running"   -> R.color.jv_accent
            "completed" -> R.color.jv_success
            "failed"    -> R.color.jv_error
            "cancelled" -> R.color.jv_text2
            else        -> R.color.jv_text2
        }
    }

    private inner class TaskViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val tvName:     TextView      = view.findViewById(R.id.tv_task_name)
        val tvModel:    TextView      = view.findViewById(R.id.tv_task_model)
        val tvStatus:   TextView      = view.findViewById(R.id.tv_task_status)
        val tvElapsed:  TextView      = view.findViewById(R.id.tv_task_elapsed)
        val tvOutput:   TextView      = view.findViewById(R.id.tv_task_output)
        val llActions:  LinearLayout  = view.findViewById(R.id.ll_task_actions)
        val tvExpand:   TextView      = view.findViewById(R.id.tv_expand)
        val btnReply:   TextView      = view.findViewById(R.id.btn_reply)
        val btnDelete:  Button        = view.findViewById(R.id.btn_task_delete)
    }

    // ── Full-screen conversation sheet ────────────────────────────────────────

    private fun showConversationSheet(task: AgentTask) {
        val dialog = Dialog(this)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        dialog.setContentView(R.layout.bottom_sheet_task_convo)
        dialog.window?.apply {
            // Make dialog full-screen
            setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            setBackgroundDrawableResource(android.R.color.transparent)
        }

        val tvTitle  = dialog.findViewById<TextView>(R.id.tv_convo_title)
        val tvModel  = dialog.findViewById<TextView>(R.id.tv_convo_model)
        val rvConvo  = dialog.findViewById<RecyclerView>(R.id.rv_convo)
        val etReply  = dialog.findViewById<EditText>(R.id.et_reply)
        val btnSend  = dialog.findViewById<Button>(R.id.btn_send_reply)

        tvTitle.text = task.name
        tvModel.text = task.model

        // Build conversation from messages list, falling back to prompt+output
        val messages: MutableList<Pair<String, String>> = if (task.messages.isNotEmpty()) {
            task.messages.toMutableList()
        } else {
            mutableListOf<Pair<String, String>>().apply {
                if (task.prompt.isNotBlank()) add("user" to task.prompt)
                if (task.output.isNotBlank()) add("assistant" to task.output)
            }
        }

        val convoAdapter = ConvoAdapter(messages)
        rvConvo.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        rvConvo.adapter = convoAdapter

        btnSend.setOnClickListener {
            val reply = etReply.text.toString().trim()
            if (reply.isEmpty()) return@setOnClickListener
            etReply.setText("")
            messages.add("user" to reply)
            convoAdapter.notifyItemInserted(messages.size - 1)
            rvConvo.scrollToPosition(messages.size - 1)
            viewModel.replyToTask(task.id, reply)
            // Dismiss after sending so the task list updates with running status
            dialog.dismiss()
        }

        // Observe for updates while sheet is open (e.g. task completes while typing)
        uiScope.launch {
            viewModel.tasks.collect { tasks ->
                val updated = tasks.find { it.id == task.id }
                if (updated != null && updated.output != task.output && updated.output.isNotBlank()) {
                    if (messages.lastOrNull()?.first != "assistant") {
                        messages.add("assistant" to updated.output)
                        convoAdapter.notifyItemInserted(messages.size - 1)
                        rvConvo.scrollToPosition(messages.size - 1)
                    }
                }
            }
        }

        dialog.show()
    }

    // ── Conversation RecyclerView adapter ─────────────────────────────────────

    private inner class ConvoAdapter(
        private val items: MutableList<Pair<String, String>>,
    ) : RecyclerView.Adapter<ConvoViewHolder>() {

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ConvoViewHolder {
            val ctx = parent.context
            val (role, _) = items.firstOrNull() ?: ("" to "")
            return ConvoViewHolder(LayoutInflater.from(ctx).inflate(
                if (role == "user") R.layout.chat_bubble_user else R.layout.chat_bubble_ai,
                parent, false
            ))
        }

        override fun onBindViewHolder(h: ConvoViewHolder, pos: Int) {
            val (role, text) = items[pos]
            h.tvMsg.text = text
            h.itemView.background = if (role == "user")
                ctx.getDrawable(R.drawable.chat_bubble_user)
            else
                ctx.getDrawable(R.drawable.chat_bubble_ai)
        }

        override fun getItemCount() = items.size
    }

    private inner class ConvoViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val tvMsg: TextView = view.findViewById(R.id.tv_message)
    }

    companion object {
        const val EXTRA_PROMPT = "extra_prompt"
        const val EXTRA_MODEL  = "extra_model"
    }
}
