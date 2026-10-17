package com.btb.ondevice.net

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

/**
 * I2: the snippet upload gate. At most one POST in flight, one newest-wins waiting slot, drops
 * counted. Pure logic, no OkHttp: the caller injects what "post" means (handing work to its own
 * executor -- never a synchronous blocking completion).
 */
class SnippetGateTest {

    /** Fake executor-facing "post": records the upload, the test completes it manually. */
    private class ManualPost {
        val started = mutableListOf<SnippetUpload>()
        val gate: SnippetGate
            get() = _gate!!

        private var _gate: SnippetGate? = null

        private var created = false

        fun build(): SnippetGate {
            check(!created)
            created = true
            val g = SnippetGate { up -> started.add(up) }
            _gate = g
            return g
        }
    }

    private fun snippet(id: String) = SnippetUpload(id, ByteArray(8) { id.last().code.toByte() })

    @Test
    fun `the first snippet starts immediately`() {
        val post = ManualPost()
        val gate = post.build()
        gate.accept(snippet("a"))
        assertThat(post.started.map { it.snippetId }).containsExactly("a")
        assertThat(gate.busy).isTrue()
        assertThat(gate.dropped).isEqualTo(0)
    }

    @Test
    fun `while one post is in flight the next snippet waits in one slot`() {
        val post = ManualPost()
        val gate = post.build()
        gate.accept(snippet("a"))
        gate.accept(snippet("b"))
        gate.accept(snippet("c"))

        // "a" in flight, "b" parked in the slot, "c" replaced it (dropped, counted once).
        assertThat(post.started.map { it.snippetId }).containsExactly("a").inOrder()
        assertThat(gate.busy).isTrue()
        assertThat(gate.dropped).isEqualTo(1)

        // Completing "a" starts the newest waiting one ("c"), never the dropped "b".
        post.gate.completePost()
        assertThat(post.started.map { it.snippetId }).containsExactly("a", "c").inOrder()
        assertThat(gate.busy).isTrue()

        // Draining the empty slot releases the gate.
        post.gate.completePost()
        assertThat(post.started.map { it.snippetId }).containsExactly("a", "c").inOrder()
        assertThat(gate.busy).isFalse()
    }

    @Test
    fun `completing an idle gate releases it and a later snippet starts fresh`() {
        val post = ManualPost()
        val gate = post.build()
        gate.accept(snippet("a"))
        gate.completePost()
        gate.completePost() // idempotent release when nothing waits
        assertThat(gate.busy).isFalse()

        gate.accept(snippet("b"))
        assertThat(post.started.map { it.snippetId }).containsExactly("a", "b").inOrder()
        assertThat(gate.dropped).isEqualTo(0)
    }

    @Test
    fun `a long burst drops everything but the newest behind the slot`() {
        val post = ManualPost()
        val gate = post.build()
        gate.accept(snippet("a"))          // starts
        (1..8).forEach { gate.accept(snippet("w$it")) }  // only w8 survives in the slot

        assertThat(post.started.map { it.snippetId }).containsExactly("a")
        assertThat(gate.dropped).isEqualTo(7)

        post.gate.completePost()
        assertThat(post.started.map { it.snippetId }).containsExactly("a", "w8").inOrder()
        post.gate.completePost()
        assertThat(gate.busy).isFalse()
    }
}
