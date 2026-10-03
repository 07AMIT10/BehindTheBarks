package com.btb.ondevice.pipeline

import com.btb.ondevice.ml.Backend
import com.btb.ondevice.ml.ModelLoader
import com.btb.ondevice.ml.ModelManifest
import com.btb.ondevice.ml.ModelRegistry
import com.btb.ondevice.ml.ModelRunner
import com.btb.ondevice.ml.ModelSpec
import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.nio.ByteBuffer

class MemoryGuardTest {

    private class FakeModelRunner(
        override val name: String,
        override val backend: Backend = Backend.CPU_XNNPACK,
    ) : ModelRunner {
        var isClosed = false
        override fun run(inputs: Array<ByteBuffer>, outputs: Array<ByteBuffer>) {}
        override fun warmup(n: Int) {}
        override fun close() {
            isClosed = true
        }
    }

    private class FakeMemoryProvider(
        var isLow: Boolean = false,
        var pss: Long = 100 * 1024L,
    ) : MemoryProvider {
        override fun isLowMemory(): Boolean = isLow
        override fun getPssKb(): Long = pss
    }

    private fun createRegistry(runners: MutableMap<String, FakeModelRunner>): ModelRegistry {
        val manifest = ModelManifest(
            models = mapOf(
                "detector" to ModelSpec("det.tflite"),
                "pose" to ModelSpec("pose.tflite"),
                "face" to ModelSpec("face.tflite"),
                "audio" to ModelSpec("audio.tflite"),
            )
        )
        val loader = ModelLoader { name, _, backend, _ ->
            val r = FakeModelRunner(name, backend)
            runners[name] = r
            r
        }
        val registry = ModelRegistry(loader, manifest)
        registry.getRunner("detector")
        registry.getRunner("pose")
        registry.getRunner("face")
        registry.getRunner("audio")
        return registry
    }

    @Test
    fun `checkMemory triggers trim when system reports low memory`() {
        var mockTime = 10.0
        val memProvider = FakeMemoryProvider(isLow = false, pss = 120 * 1024L)
        val runners = mutableMapOf<String, FakeModelRunner>()
        val registry = createRegistry(runners)
        val guard = MemoryGuard(memProvider, registry, maxPssKb = 250 * 1024L, pollIntervalS = 30.0, clock = { mockTime })

        // Initial check: fine
        val trimmed = guard.checkMemory()
        assertThat(trimmed).isFalse()
        assertThat(registry.hasRunner("face")).isTrue()

        // Next check: system low memory reported
        mockTime += 35.0
        memProvider.isLow = true
        val trimmedLow = guard.checkMemory()
        assertThat(trimmedLow).isTrue()
        assertThat(guard.lastTrimReason).isEqualTo("system_low_memory")
        assertThat(registry.hasRunner("detector")).isTrue()
        assertThat(registry.hasRunner("pose")).isTrue()
        assertThat(registry.hasRunner("face")).isFalse()
        assertThat(registry.hasRunner("audio")).isFalse()
        assertThat(runners["face"]!!.isClosed).isTrue()
    }

    @Test
    fun `checkMemory triggers trim when PSS exceeds budget`() {
        var mockTime = 10.0
        val memProvider = FakeMemoryProvider(isLow = false, pss = 260 * 1024L) // 260 MB > 250 MB
        val runners = mutableMapOf<String, FakeModelRunner>()
        val registry = createRegistry(runners)
        val guard = MemoryGuard(memProvider, registry, maxPssKb = 250 * 1024L, pollIntervalS = 30.0, clock = { mockTime })

        val trimmed = guard.checkMemory()
        assertThat(trimmed).isTrue()
        assertThat(guard.lastTrimReason).contains("pss_budget_exceeded")
        assertThat(registry.hasRunner("face")).isFalse()
        assertThat(registry.hasRunner("audio")).isFalse()
    }

    @Test
    fun `checkMemory throttles unless force is true`() {
        var mockTime = 10.0
        val memProvider = FakeMemoryProvider(isLow = false, pss = 100 * 1024L)
        val runners = mutableMapOf<String, FakeModelRunner>()
        val registry = createRegistry(runners)
        val guard = MemoryGuard(memProvider, registry, maxPssKb = 250 * 1024L, pollIntervalS = 30.0, clock = { mockTime })

        guard.checkMemory() // poll at t=10

        // At t=15, memory spikes
        mockTime = 15.0
        memProvider.pss = 300 * 1024L
        // Without force, skipped due to interval
        assertThat(guard.checkMemory(force = false)).isFalse()
        assertThat(registry.hasRunner("face")).isTrue()

        // With force=true, checked immediately
        assertThat(guard.checkMemory(force = true)).isTrue()
        assertThat(registry.hasRunner("face")).isFalse()
    }

    @Test
    fun `onTrimMemory respects severity level`() {
        val memProvider = FakeMemoryProvider()
        val runners = mutableMapOf<String, FakeModelRunner>()
        val registry = createRegistry(runners)
        val guard = MemoryGuard(memProvider, registry)

        // Trim level 5 (TRIM_MEMORY_RUNNING_MODERATE) -> no unload
        guard.onTrimMemory(5)
        assertThat(registry.hasRunner("face")).isTrue()

        // Trim level 15 (TRIM_MEMORY_RUNNING_CRITICAL) -> unloads
        guard.onTrimMemory(15)
        assertThat(registry.hasRunner("face")).isFalse()
        assertThat(guard.lastTrimReason).isEqualTo("trim_memory_signal (15)")
    }
}
