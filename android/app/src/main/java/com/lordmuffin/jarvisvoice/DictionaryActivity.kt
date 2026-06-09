package com.lordmuffin.jarvisvoice

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView

class DictionaryActivity : AppCompatActivity() {

    private lateinit var dictManager: CustomDictionaryManager
    private lateinit var adapter: DictAdapter
    private val entries = mutableListOf<Pair<String, String>>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_dictionary)
        supportActionBar?.title = "Custom Dictionary"

        dictManager = CustomDictionaryManager(this)

        val rv = findViewById<RecyclerView>(R.id.rv_dict)
        adapter = DictAdapter()
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = adapter

        findViewById<Button>(R.id.btn_add_entry).setOnClickListener { showAddDialog() }

        refreshEntries()
    }

    private fun refreshEntries() {
        entries.clear()
        entries.addAll(
            dictManager.getAll().entries
                .map { it.key to it.value }
                .sortedBy { it.first }
        )
        adapter.notifyDataSetChanged()
    }

    private fun showAddDialog(existing: Pair<String, String>? = null) {
        val view  = LayoutInflater.from(this).inflate(R.layout.dialog_dict_entry, null)
        val etFrom = view.findViewById<EditText>(R.id.et_word_from)
        val etTo   = view.findViewById<EditText>(R.id.et_word_to)
        if (existing != null) {
            etFrom.setText(existing.first)
            etTo.setText(existing.second)
            etFrom.isEnabled = false
        }
        AlertDialog.Builder(this)
            .setTitle(if (existing == null) "Add substitution" else "Edit substitution")
            .setView(view)
            .setPositiveButton("Save") { _, _ ->
                val from = etFrom.text.toString().trim()
                val to   = etTo.text.toString().trim()
                if (from.isBlank() || to.isBlank()) {
                    Toast.makeText(this, "Both fields required", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                if (existing != null) dictManager.removeEntry(existing.first)
                dictManager.addEntry(from, to)
                refreshEntries()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private inner class DictAdapter : RecyclerView.Adapter<DictAdapter.VH>() {

        inner class VH(v: View) : RecyclerView.ViewHolder(v) {
            val tvFrom:    TextView = v.findViewById(R.id.tv_word_from)
            val tvTo:      TextView = v.findViewById(R.id.tv_word_to)
            val btnDelete: Button   = v.findViewById(R.id.btn_delete_entry)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            VH(LayoutInflater.from(parent.context)
                .inflate(R.layout.item_dict_entry, parent, false))

        override fun getItemCount() = entries.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val (from, to) = entries[position]
            holder.tvFrom.text = from
            holder.tvTo.text   = "→  $to"
            holder.itemView.setOnClickListener { showAddDialog(from to to) }
            holder.btnDelete.setOnClickListener {
                dictManager.removeEntry(from)
                refreshEntries()
            }
        }
    }
}
