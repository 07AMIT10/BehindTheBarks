package com.btb.ondevice.capture

import android.content.Context
import android.media.MediaMetadataRetriever
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean

class ClipSource(
    private val context: Context,
    private val clipFile: File,
    private val fps: Double = 15.0,
    private val frameCallback: FrameCallback,
) {
    private val isRunning = AtomicBoolean(false)
    private var playbackThread: Thread? = null

    fun start() {
        if (!isRunning.compareAndSet(false, true)) return

        playbackThread = Thread({
            val retriever = MediaMetadataRetriever()
            try {
                retriever.setDataSource(clipFile.absolutePath)
                val durationStr = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                val durationMs = durationStr?.toLongOrNull() ?: 0L

                val frameIntervalUs = (1_000_000.0 / fps).toLong()
                var currentUs = 0L
                val maxUs = durationMs * 1000L

                while (isRunning.get() && currentUs < maxUs) {
                    val bitmap = retriever.getFrameAtTime(currentUs, MediaMetadataRetriever.OPTION_CLOSEST)
                    if (bitmap != null) {
                        val width = bitmap.width
                        val height = bitmap.height
                        val byteBuffer = ByteBuffer.allocate(bitmap.byteCount)
                        bitmap.copyPixelsToBuffer(byteBuffer)
                        val rgbaBytes = byteBuffer.array()
                        val ts = currentUs / 1_000_000.0

                        frameCallback(rgbaBytes, width, height, 0, ts)
                        bitmap.recycle()
                    }
                    currentUs += frameIntervalUs
                }
            } catch (_: Exception) {
            } finally {
                try {
                    retriever.release()
                } catch (_: Exception) {}
            }
        }, "ClipSourceThread").apply { start() }
    }

    fun stop() {
        isRunning.set(false)
        playbackThread?.join(1000)
        playbackThread = null
    }
}
