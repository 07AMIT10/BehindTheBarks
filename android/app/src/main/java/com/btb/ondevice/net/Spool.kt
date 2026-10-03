package com.btb.ondevice.net

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.io.RandomAccessFile

@Serializable
data class SpooledEvent(
    val ts: Double,
    val type: String,
    val json: String,
)

/**
 * Disk-backed FIFO spool for non-frame events (audio, rules, treat) during network disconnection.
 *
 * Requirements:
 *  - Frame events are NOT spooled (live only, dropped when disconnected).
 *  - Non-frame events are persisted to disk.
 *  - Capped at [maxAgeS] (default 5 minutes = 300s) and [maxBytes] (default 2 MB = 2,000,000 bytes).
 *  - Flushed in strict timestamp order on reconnect.
 */
class EventSpool(
    val spoolDir: File,
    val maxBytes: Long = 2_000_000L,
    val maxAgeS: Double = 300.0,
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    private val spoolFile = File(spoolDir, "events_spool.jsonl")
    private val lock = Any()

    init {
        spoolDir.mkdirs()
    }

    val sizeBytes: Long
        get() = synchronized(lock) {
            if (spoolFile.exists()) spoolFile.length() else 0L
        }

    /**
     * Record an event if it is a spoolable type (audio, rules, treat).
     * Frame events are ignored.
     */
    fun enqueue(type: String, ts: Double, jsonPayload: String): Boolean {
        if (type == "frame") {
            return false // Frame events are dropped, not spooled
        }
        if (type !in SPOOLABLE_TYPES) {
            return false
        }

        synchronized(lock) {
            val spooled = SpooledEvent(ts = ts, type = type, json = jsonPayload)
            val line = json.encodeToString(SpooledEvent.serializer(), spooled) + "\n"
            val lineBytes = line.toByteArray(Charsets.UTF_8)

            // If single line exceeds maxBytes, drop it
            if (lineBytes.size > maxBytes) {
                return false
            }

            // Prune before adding if file would exceed maxBytes
            if (spoolFile.length() + lineBytes.size > maxBytes) {
                prune(targetRemainingBytes = maxBytes - lineBytes.size, currentTs = ts)
            }

            spoolFile.appendText(line, Charsets.UTF_8)
            return true
        }
    }

    /**
     * Read and remove all valid spooled events, sorted in time order.
     */
    fun drain(currentTs: Double? = null): List<SpooledEvent> {
        synchronized(lock) {
            if (!spoolFile.exists() || spoolFile.length() == 0L) {
                return emptyList()
            }

            val events = mutableListOf<SpooledEvent>()
            spoolFile.forEachLine(Charsets.UTF_8) { line ->
                if (line.isNotBlank()) {
                    try {
                        val ev = json.decodeFromString(SpooledEvent.serializer(), line)
                        if (currentTs == null || (currentTs - ev.ts) <= maxAgeS) {
                            events.add(ev)
                        }
                    } catch (_: Exception) {
                        // ignore malformed line
                    }
                }
            }

            spoolFile.delete()
            return events.sortedBy { it.ts }
        }
    }

    /**
     * Prune expired events (> maxAgeS) or oldest events to satisfy byte limits.
     */
    fun prune(targetRemainingBytes: Long = maxBytes, currentTs: Double) {
        synchronized(lock) {
            if (!spoolFile.exists() || spoolFile.length() == 0L) return

            val events = mutableListOf<SpooledEvent>()
            spoolFile.forEachLine(Charsets.UTF_8) { line ->
                if (line.isNotBlank()) {
                    try {
                        val ev = json.decodeFromString(SpooledEvent.serializer(), line)
                        // Drop if older than maxAgeS
                        if ((currentTs - ev.ts) <= maxAgeS) {
                            events.add(ev)
                        }
                    } catch (_: Exception) {
                    }
                }
            }

            // Sort by time order
            events.sortBy { it.ts }

            // Drop oldest from head until total size fits within targetRemainingBytes
            val keptLines = mutableListOf<String>()
            var totalBytes = 0L

            // Iterate backwards (newest to oldest) to keep the newest ones
            val keptRev = mutableListOf<String>()
            for (i in events.indices.reversed()) {
                val line = json.encodeToString(SpooledEvent.serializer(), events[i]) + "\n"
                val b = line.toByteArray(Charsets.UTF_8).size.toLong()
                if (totalBytes + b <= targetRemainingBytes) {
                    totalBytes += b
                    keptRev.add(line)
                } else {
                    break
                }
            }

            // Reverse back to chronological order
            val kept = keptRev.asReversed()

            val tmpFile = File(spoolDir, "events_spool.tmp")
            tmpFile.bufferedWriter(Charsets.UTF_8).use { writer ->
                for (line in kept) {
                    writer.write(line)
                }
            }
            tmpFile.renameTo(spoolFile)
        }
    }

    fun clear() {
        synchronized(lock) {
            if (spoolFile.exists()) {
                spoolFile.delete()
            }
        }
    }

    companion object {
        val SPOOLABLE_TYPES = setOf("audio", "rules", "treat")
    }
}
