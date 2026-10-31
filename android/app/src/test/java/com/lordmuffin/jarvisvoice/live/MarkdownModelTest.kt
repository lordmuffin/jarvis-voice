package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.MarkdownModel.Block
import com.lordmuffin.jarvisvoice.live.MarkdownModel.Inline
import org.junit.Assert.assertEquals
import org.junit.Test

class MarkdownModelTest {

    @Test fun stripsFrontmatter() {
        val md = "---\ntitle: Planning\ntags: [meeting]\n---\n\n# Planning\n\nBody\n"
        assertEquals("# Planning\n\nBody\n", MarkdownModel.stripFrontmatter(md))
    }

    @Test fun stripsFrontmatterWithCrlfAndBom() {
        assertEquals("# T", MarkdownModel.stripFrontmatter("﻿---\r\na: 1\r\n---\r\n# T"))
    }

    @Test fun leavesTextWithoutFrontmatterAlone() {
        assertEquals("# Title\n---\nmore", MarkdownModel.stripFrontmatter("# Title\n---\nmore"))
        // An unterminated block is not front matter.
        assertEquals("---\nnot closed", MarkdownModel.stripFrontmatter("---\nnot closed"))
    }

    @Test fun parsesHeadingsBulletsTasksAndParagraphs() {
        val blocks = MarkdownModel.parse(
            """
            # Weekly sync
            ## Summary
            We agreed to **ship Friday**
            and to write notes.

            ### Actions
            - [ ] Write release notes
            - [x] Tag build
            - Talk to [[People/Sam|Sam]]
              * nested with `code`
            1. First
            ---
            """.trimIndent()
        )
        assertEquals(
            listOf(
                Block.Heading(1, listOf(Inline("Weekly sync"))),
                Block.Heading(2, listOf(Inline("Summary"))),
                Block.Paragraph(listOf(Inline("We agreed to "), Inline("ship Friday", bold = true), Inline(" and to write notes."))),
                Block.Heading(3, listOf(Inline("Actions"))),
                Block.Task(0, false, listOf(Inline("Write release notes"))),
                Block.Task(0, true, listOf(Inline("Tag build"))),
                Block.Bullet(0, listOf(Inline("Talk to Sam"))),
                Block.Bullet(1, listOf(Inline("nested with "), Inline("code", code = true))),
                Block.Bullet(0, listOf(Inline("1. First"))),
                Block.Rule,
            ),
            blocks,
        )
    }

    @Test fun inlineWikilinksAndLinksBecomePlainText() {
        assertEquals(listOf(Inline("See Project Alpha and docs.")), MarkdownModel.inlines("See [[Project Alpha]] and [docs](https://x.y)."))
        assertEquals(listOf(Inline("Note")), MarkdownModel.inlines("[[Note#Heading]]"))
        assertEquals(listOf(Inline("C# tips")), MarkdownModel.inlines("[[Dev/CSharp|C# tips]]"))
    }

    @Test fun unmatchedMarkersStayLiteral() {
        assertEquals(listOf(Inline("2 ** 3 and [x and `y")), MarkdownModel.inlines("2 ** 3 and [x and `y"))
        assertEquals(listOf(Inline("a "), Inline("b", bold = true)), MarkdownModel.inlines("a **b**"))
    }

    @Test fun hashtagIsNotAHeading() {
        assertEquals(listOf(Block.Paragraph(listOf(Inline("#meeting #weekly")))), MarkdownModel.parse("#meeting #weekly"))
    }

    @Test fun plainTextRendering() {
        val blocks = MarkdownModel.parse("## Actions\n- [ ] One\n- [x] Two\n- Three")
        assertEquals("Actions\n☐ One\n☑ Two\n• Three", MarkdownModel.plainText(blocks))
    }
}
