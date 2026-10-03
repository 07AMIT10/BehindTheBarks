package com.btb.ondevice.ml

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.nio.ByteBuffer

class ModelRegistryTest {

    private class FakeModelRunner(
        override val name: String,
        override val backend: Backend,
    ) : ModelRunner {
        var runCount = 0
        var warmupCount = 0
        var isClosed = false

        override fun run(inputs: Array<ByteBuffer>, outputs: Array<ByteBuffer>) {
            runCount++
        }

        override fun warmup(n: Int) {
            warmupCount += n
        }

        override fun close() {
            isClosed = true
        }
    }

    @Test
    fun `manifest parsing loads asset manifest correctly`() {
        val stream = javaClass.classLoader?.getResourceAsStream("models/manifest.json")
            ?: error("Could not load models/manifest.json")
        val manifestJson = stream.bufferedReader().readText()

        val manifest = ModelRegistry.parseManifest(manifestJson)
        assertThat(manifest.version).isEqualTo(1)
        assertThat(manifest.threads).isEqualTo(2)
        assertThat(manifest.models.keys).containsExactly("detector", "pose", "face", "audio")

        val det = manifest.models["detector"]!!
        assertThat(det.file).isEqualTo("models/det_yolo26n_320_int8.tflite")
        assertThat(det.backendEnum).isEqualTo(Backend.CPU_XNNPACK)
        assertThat(det.threads).isEqualTo(2)
    }

    @Test
    fun `getRunner lazily loads and warms up runner`() {
        val manifest = ModelManifest(
            models = mapOf(
                "detector" to ModelSpec("models/detector.tflite", "CPU_XNNPACK", 2)
            )
        )
        val created = mutableListOf<FakeModelRunner>()
        val loader = ModelLoader { name, _, backend, _ ->
            val runner = FakeModelRunner(name, backend)
            created.add(runner)
            runner
        }

        val registry = ModelRegistry(loader, manifest)
        assertThat(registry.hasRunner("detector")).isFalse()

        val runner1 = registry.getRunner("detector")
        assertThat(runner1.backend).isEqualTo(Backend.CPU_XNNPACK)
        assertThat((runner1 as FakeModelRunner).warmupCount).isEqualTo(3)
        assertThat(created).hasSize(1)

        val runner2 = registry.getRunner("detector")
        assertThat(runner2).isSameInstanceAs(runner1)
        assertThat(created).hasSize(1)
    }

    @Test
    fun `getRunner falls back to CPU when GPU initialization throws`() {
        val manifest = ModelManifest(
            models = mapOf(
                "pose" to ModelSpec("models/pose.tflite", "GPU", 2)
            )
        )
        val loader = ModelLoader { name, _, backend, _ ->
            if (backend == Backend.GPU) {
                throw IllegalStateException("GPU accelerator not available on device")
            }
            FakeModelRunner(name, backend)
        }

        val registry = ModelRegistry(loader, manifest)
        val runner = registry.getRunner("pose")
        assertThat(runner.backend).isEqualTo(Backend.CPU_XNNPACK)
    }

    @Test
    fun `trimMemory unloads face and audio on explicit memory guard`() {
        val manifest = ModelManifest(
            models = mapOf(
                "detector" to ModelSpec("det.tflite", "CPU_XNNPACK", 2),
                "pose" to ModelSpec("pose.tflite", "CPU_XNNPACK", 2),
                "face" to ModelSpec("face.tflite", "CPU_XNNPACK", 2),
                "audio" to ModelSpec("audio.tflite", "CPU_XNNPACK", 2),
            )
        )
        val instances = mutableMapOf<String, FakeModelRunner>()
        val loader = ModelLoader { name, _, backend, _ ->
            val r = FakeModelRunner(name, backend)
            instances[name] = r
            r
        }

        val registry = ModelRegistry(loader, manifest)
        registry.getRunner("detector")
        registry.getRunner("pose")
        registry.getRunner("face")
        registry.getRunner("audio")

        assertThat(registry.hasRunner("detector")).isTrue()
        assertThat(registry.hasRunner("pose")).isTrue()
        assertThat(registry.hasRunner("face")).isTrue()
        assertThat(registry.hasRunner("audio")).isTrue()

        // Normal trim does not unload
        registry.trimMemory(explicitMemoryGuard = false, trimLevel = 5)
        assertThat(registry.hasRunner("face")).isTrue()
        assertThat(instances["face"]!!.isClosed).isFalse()

        // Explicit memory guard unloads face and audio
        registry.trimMemory(explicitMemoryGuard = true)
        assertThat(registry.hasRunner("detector")).isTrue()
        assertThat(registry.hasRunner("pose")).isTrue()
        assertThat(registry.hasRunner("face")).isFalse()
        assertThat(registry.hasRunner("audio")).isFalse()
        assertThat(instances["face"]!!.isClosed).isTrue()
        assertThat(instances["audio"]!!.isClosed).isTrue()

        // Lazily reload face
        val reloadedFace = registry.getRunner("face")
        assertThat(reloadedFace).isNotNull()
        assertThat(registry.hasRunner("face")).isTrue()
    }
}
