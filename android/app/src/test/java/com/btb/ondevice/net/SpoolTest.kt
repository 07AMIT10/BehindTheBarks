package com.btb.ondevice.net

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import java.io.File
import java.nio.file.Files

class SpoolTest {

    private lateinit var tempDir: File
    private lateinit var spool: EventSpool

    @BeforeEach
    fun setUp() {
        tempDir = Files.createTempDirectory("spool_test").toFile()
        spool = EventSpool(tempDir, maxBytes = 2_000_000L, maxAgeS = 300.0)
    }

    @AfterEach
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    @Test
    fun `frame events are dropped and not spooled`() {
        val enqueued = spool.enqueue("frame", 10.0, """{"ts": 10.0, "source": "live"}""")
        assertThat(enqueued).isFalse()
        assertThat(spool.sizeBytes).isEqualTo(0L)
        assertThat(spool.drain()).isEmpty()
    }

    @Test
    fun `audio rules and treat events are spooled`() {
        assertThat(spool.enqueue("audio", 10.0, """{"ts": 10.0, "label": "bark"}""")).isTrue()
        assertThat(spool.enqueue("rules", 10.5, """{"ts": 10.5, "emotion": "happy"}""")).isTrue()
        assertThat(spool.enqueue("treat", 11.0, """{"ts": 11.0}""")).isTrue()

        assertThat(spool.sizeBytes).isGreaterThan(0L)

        val drained = spool.drain()
        assertThat(drained.size).isEqualTo(3)
        assertThat(drained[0].type).isEqualTo("audio")
        assertThat(drained[1].type).isEqualTo("rules")
        assertThat(drained[2].type).isEqualTo("treat")

        // Spool is empty after drain
        assertThat(spool.sizeBytes).isEqualTo(0L)
        assertThat(spool.drain()).isEmpty()
    }

    @Test
    fun `drain sorts events by timestamp ascending`() {
        // Enqueue out of order
        spool.enqueue("rules", 20.0, """{"ts": 20.0}""")
        spool.enqueue("audio", 10.0, """{"ts": 10.0}""")
        spool.enqueue("treat", 15.0, """{"ts": 15.0}""")

        val drained = spool.drain()
        assertThat(drained.map { it.ts }).containsExactly(10.0, 15.0, 20.0).inOrder()
    }

    @Test
    fun `events older than maxAgeS are pruned`() {
        val shortSpool = EventSpool(tempDir, maxAgeS = 10.0)

        shortSpool.enqueue("audio", 100.0, """{"ts": 100.0}""")
        shortSpool.enqueue("audio", 108.0, """{"ts": 108.0}""")
        shortSpool.enqueue("audio", 112.0, """{"ts": 112.0}""")

        // At current time 115.0, event at 100.0 is 15s old (> 10s maxAgeS)
        val drained = shortSpool.drain(currentTs = 115.0)
        assertThat(drained.size).isEqualTo(2)
        assertThat(drained.map { it.ts }).containsExactly(108.0, 112.0).inOrder()
    }

    @Test
    fun `spool caps total bytes and keeps newest events`() {
        // Limit spool to 200 bytes
        val smallSpool = EventSpool(tempDir, maxBytes = 250L, maxAgeS = 300.0)

        for (i in 1..10) {
            val ts = i * 1.0
            smallSpool.enqueue("audio", ts, """{"id": $i, "ts": $ts}""")
        }

        assertThat(smallSpool.sizeBytes).isAtMost(250L)
        val drained = smallSpool.drain()
        assertThat(drained).isNotEmpty()
        // Should keep newest events and have dropped earliest
        assertThat(drained.last().ts).isEqualTo(10.0)
        assertThat(drained.first().ts).isGreaterThan(1.0)
    }
}
