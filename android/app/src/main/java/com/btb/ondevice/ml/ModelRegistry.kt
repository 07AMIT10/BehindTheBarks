package com.btb.ondevice.ml

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.Closeable

@Serializable
data class ModelSpec(
    val file: String,
    val backend: String = "CPU_XNNPACK",
    val threads: Int = 2,
) {
    val backendEnum: Backend get() = when (backend.uppercase()) {
        "GPU" -> Backend.GPU
        "ORT_XNNPACK" -> Backend.ORT_XNNPACK
        else -> Backend.CPU_XNNPACK
    }
}

@Serializable
data class ModelManifest(
    val version: Int = 1,
    val threads: Int = 2,
    val models: Map<String, ModelSpec> = emptyMap(),
)

fun interface ModelLoader {
    fun load(name: String, path: String, backend: Backend, threads: Int): ModelRunner
}

class ModelRegistry(
    private val loader: ModelLoader,
    val manifest: ModelManifest,
    val warmupTolerance: Double = 0.05,
) : Closeable {

    private val runners = mutableMapOf<String, ModelRunner>()

    fun getRunner(name: String): ModelRunner {
        runners[name]?.let { return it }

        val spec = manifest.models[name] ?: error("No model spec found in manifest for '$name'")
        val requestedBackend = spec.backendEnum

        val runner = try {
            val r = loader.load(name, spec.file, requestedBackend, spec.threads)
            r.warmup()
            r
        } catch (e: Exception) {
            if (requestedBackend == Backend.GPU) {
                // Fallback from GPU to CPU on initialization error
                val fallback = loader.load(name, spec.file, Backend.CPU_XNNPACK, spec.threads)
                fallback.warmup()
                fallback
            } else {
                throw e
            }
        }

        runners[name] = runner
        return runner
    }

    fun hasRunner(name: String): Boolean = runners.containsKey(name)

    /**
     * Memory trim handling:
     * - Under normal conditions (or non-critical trim), keep all models loaded.
     * - Under explicit MemoryGuard request or critical trim level (level >= 15),
     *   unload non-essential models ("face" and "audio"), preserving "detector" and "pose".
     */
    fun trimMemory(explicitMemoryGuard: Boolean = false, trimLevel: Int = 0) {
        val shouldUnload = explicitMemoryGuard || trimLevel >= 15
        if (shouldUnload) {
            listOf("face", "audio").forEach { name ->
                runners.remove(name)?.close()
            }
        }
    }

    override fun close() {
        for (r in runners.values) {
            r.close()
        }
        runners.clear()
    }

    companion object {
        private val json = Json { ignoreUnknownKeys = true }

        fun parseManifest(jsonString: String): ModelManifest {
            return json.decodeFromString(ModelManifest.serializer(), jsonString)
        }
    }
}
