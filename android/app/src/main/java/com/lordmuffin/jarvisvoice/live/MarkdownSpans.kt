package com.lordmuffin.jarvisvoice.live

import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.LeadingMarginSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.text.style.TypefaceSpan

/**
 * A deliberately small Markdown subset for the session summary note: `#`/`##`/`###` headings,
 * `-`/`*` bullets, `- [ ]`/`- [x]` checkboxes, `**bold**`, `` `code` ``, `[[wikilinks]]` and
 * `[text](url)` shown as plain text. Parsing is pure Kotlin ([MarkdownModel]); [MarkdownSpans]
 * only turns the model into a [Spanned].
 */
object MarkdownModel {

    data class Inline(val text: String, val bold: Boolean = false, val code: Boolean = false)

    sealed class Block {
        data class Heading(val level: Int, val inlines: List<Inline>) : Block()
        data class Bullet(val depth: Int, val inlines: List<Inline>) : Block()
        data class Task(val depth: Int, val checked: Boolean, val inlines: List<Inline>) : Block()
        data class Paragraph(val inlines: List<Inline>) : Block()
        object Rule : Block()
    }

    /** Drops a leading YAML front-matter block (`---` … `---`), and the blank lines after it. */
    fun stripFrontmatter(markdown: String): String {
        val text = markdown.removePrefix("﻿").replace("\r\n", "\n")
        val lines = text.split('\n')
        if (lines.isEmpty() || lines[0].trimEnd() != "---") return text
        val end = (1 until lines.size).firstOrNull { lines[it].trimEnd() == "---" || lines[it].trimEnd() == "..." }
            ?: return text
        return lines.drop(end + 1).dropWhile { it.isBlank() }.joinToString("\n")
    }

    private val HEADING = Regex("^(#{1,6})\\s+(.*?)\\s*#*\\s*$")
    private val TASK = Regex("^(\\s*)[-*+]\\s+\\[([ xX])]\\s+(.*)$")
    private val BULLET = Regex("^(\\s*)[-*+]\\s+(.*)$")
    private val ORDERED = Regex("^(\\s*)(\\d+)[.)]\\s+(.*)$")
    private val RULE = Regex("^\\s*([-*_])(\\s*\\1){2,}\\s*$")

    /** Parses Markdown into blocks. Consecutive plain lines join into one paragraph. */
    fun parse(markdown: String): List<Block> {
        val blocks = mutableListOf<Block>()
        val paragraph = StringBuilder()
        fun flush() {
            if (paragraph.isNotEmpty()) blocks += Block.Paragraph(inlines(paragraph.toString()))
            paragraph.setLength(0)
        }
        var inFence = false
        for (raw in markdown.replace("\r\n", "\n").split('\n')) {
            val line = raw.trimEnd()
            if (line.trimStart().startsWith("```")) {
                flush()
                inFence = !inFence
                continue
            }
            if (inFence) {
                blocks += Block.Paragraph(listOf(Inline(line, code = true)))
                continue
            }
            if (line.isBlank()) { flush(); continue }
            val heading = HEADING.matchEntire(line)
            if (heading != null) {
                flush()
                blocks += Block.Heading(heading.groupValues[1].length, inlines(heading.groupValues[2]))
                continue
            }
            val task = TASK.matchEntire(line)
            if (task != null) {
                flush()
                blocks += Block.Task(depth(task.groupValues[1]), task.groupValues[2] != " ", inlines(task.groupValues[3]))
                continue
            }
            if (RULE.matches(line)) {
                flush()
                blocks += Block.Rule
                continue
            }
            val bullet = BULLET.matchEntire(line)
            if (bullet != null) {
                flush()
                blocks += Block.Bullet(depth(bullet.groupValues[1]), inlines(bullet.groupValues[2]))
                continue
            }
            val ordered = ORDERED.matchEntire(line)
            if (ordered != null) {
                flush()
                blocks += Block.Bullet(depth(ordered.groupValues[1]), inlines("${ordered.groupValues[2]}. ${ordered.groupValues[3]}"))
                continue
            }
            if (paragraph.isNotEmpty()) paragraph.append(' ')
            paragraph.append(line.trim())
        }
        flush()
        return blocks
    }

    private fun depth(indent: String): Int {
        val width = indent.sumOf { if (it == '\t') 4 else 1 }
        return width / 2
    }

    /** `**bold**`, `` `code` ``, `[[target|alias]]` → alias, `[text](url)` → text. */
    fun inlines(text: String): List<Inline> {
        val out = mutableListOf<Inline>()
        val plain = StringBuilder()
        var bold = false
        fun emit() {
            if (plain.isNotEmpty()) {
                val last = out.lastOrNull()
                if (last != null && last.bold == bold && !last.code) out[out.lastIndex] = last.copy(text = last.text + plain)
                else out += Inline(plain.toString(), bold = bold)
                plain.setLength(0)
            }
        }
        var i = 0
        while (i < text.length) {
            when {
                text.startsWith("**", i) -> {
                    // Open only when a closing marker follows, so a stray "**" stays literal.
                    if (bold || text.indexOf("**", i + 2) > i + 2) {
                        emit()
                        bold = !bold
                    } else {
                        plain.append("**")
                    }
                    i += 2
                }
                text[i] == '`' -> {
                    val end = text.indexOf('`', i + 1)
                    if (end > i) {
                        emit()
                        out += Inline(text.substring(i + 1, end), bold = bold, code = true)
                        i = end + 1
                    } else {
                        plain.append('`'); i++
                    }
                }
                text.startsWith("[[", i) -> {
                    val end = text.indexOf("]]", i + 2)
                    if (end > i) {
                        val inner = text.substring(i + 2, end)
                        plain.append(if ('|' in inner) inner.substringAfter('|') else inner.substringBefore('#').ifEmpty { inner })
                        i = end + 2
                    } else {
                        plain.append("[["); i += 2
                    }
                }
                text[i] == '[' -> {
                    val close = text.indexOf("](", i + 1)
                    val end = if (close > i) text.indexOf(')', close + 2) else -1
                    if (close > i && end > close) {
                        plain.append(text, i + 1, close)
                        i = end + 1
                    } else {
                        plain.append('['); i++
                    }
                }
                else -> { plain.append(text[i]); i++ }
            }
        }
        emit()
        return out
    }

    /** The text a [Block] renders to, without styling (used by tests and as an accessibility fallback). */
    fun plainText(blocks: List<Block>): String = blocks.joinToString("\n") { b ->
        when (b) {
            is Block.Heading -> b.inlines.joinToString("") { it.text }
            is Block.Bullet -> "  ".repeat(b.depth) + "• " + b.inlines.joinToString("") { it.text }
            is Block.Task -> "  ".repeat(b.depth) + (if (b.checked) "☑ " else "☐ ") + b.inlines.joinToString("") { it.text }
            is Block.Paragraph -> b.inlines.joinToString("") { it.text }
            Block.Rule -> "—"
        }
    }
}

/** Renders [MarkdownModel] blocks with framework spans. */
object MarkdownSpans {

    fun render(markdown: String, accentColor: Int, mutedColor: Int, indentPx: Int): Spanned =
        render(MarkdownModel.parse(markdown), accentColor, mutedColor, indentPx)

    fun render(blocks: List<MarkdownModel.Block>, accentColor: Int, mutedColor: Int, indentPx: Int): Spanned {
        val sb = SpannableStringBuilder()
        blocks.forEachIndexed { index, block ->
            if (index > 0) {
                sb.append('\n')
                // A blank line before headings and paragraphs; list items stay tight.
                if (block is MarkdownModel.Block.Heading || block is MarkdownModel.Block.Paragraph) sb.append('\n')
            }
            val start = sb.length
            when (block) {
                is MarkdownModel.Block.Heading -> {
                    appendInlines(sb, block.inlines)
                    val scale = when (block.level) { 1 -> 1.4f; 2 -> 1.2f; else -> 1.05f }
                    sb.setSpan(RelativeSizeSpan(scale), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    sb.setSpan(StyleSpan(Typeface.BOLD), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    if (block.level >= 3) {
                        sb.setSpan(ForegroundColorSpan(accentColor), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    }
                }
                is MarkdownModel.Block.Bullet -> {
                    sb.append("•  ")
                    sb.setSpan(ForegroundColorSpan(accentColor), start, start + 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    appendInlines(sb, block.inlines)
                    indent(sb, start, block.depth, indentPx)
                }
                is MarkdownModel.Block.Task -> {
                    sb.append(if (block.checked) "☑  " else "☐  ")
                    sb.setSpan(ForegroundColorSpan(accentColor), start, start + 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    val textStart = sb.length
                    appendInlines(sb, block.inlines)
                    if (block.checked) {
                        sb.setSpan(ForegroundColorSpan(mutedColor), textStart, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    }
                    indent(sb, start, block.depth, indentPx)
                }
                is MarkdownModel.Block.Paragraph -> appendInlines(sb, block.inlines)
                MarkdownModel.Block.Rule -> {
                    sb.append("———")
                    sb.setSpan(ForegroundColorSpan(mutedColor), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
            }
        }
        return sb
    }

    private fun indent(sb: SpannableStringBuilder, start: Int, depth: Int, indentPx: Int) {
        if (depth <= 0) return
        sb.setSpan(LeadingMarginSpan.Standard(depth * indentPx), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
    }

    private fun appendInlines(sb: SpannableStringBuilder, inlines: List<MarkdownModel.Inline>) {
        for (inline in inlines) {
            val s = sb.length
            sb.append(inline.text)
            if (inline.bold) sb.setSpan(StyleSpan(Typeface.BOLD), s, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            if (inline.code) sb.setSpan(TypefaceSpan("monospace"), s, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
    }
}
