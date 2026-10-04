package com.btb.ondevice.pipeline

import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class InferenceExecutor(
    private val executor: ExecutorService = Executors.newSingleThreadExecutor { runnable ->
        Thread({
            try {
                // Set display thread priority when running on Android
                android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_DISPLAY)
            } catch (ignored: Throwable) {
                // Ignore in host JVM unit test environment
            }
            runnable.run()
        }, "btb-inference").apply {
            priority = Thread.NORM_PRIORITY + 1
        }
    }
) : AutoCloseable {

    private val busy = AtomicBoolean(false)

    fun isBusy(): Boolean = busy.get()

    /**
     * Submit an inference task. If the executor is currently busy, drops the task
     * under the "latest-wins" policy and returns false. Returns true if queued.
     */
    fun submitLatest(task: () -> Unit): Boolean {
        if (busy.compareAndSet(false, true)) {
            executor.submit {
                try {
                    task()
                } catch (t: Throwable) {
                    android.util.Log.e("InferenceExecutor", "Inference task threw exception: ${t.message}", t)
                } finally {
                    busy.set(false)
                }
            }
            return true
        }
        return false
    }

    /**
     * Submit a blocking task (e.g. for deterministic replay / testing).
     */
    fun submitBlocking(task: () -> Unit) {
        val future = executor.submit(task)
        future.get()
    }

    override fun close() {
        executor.shutdown()
    }
}
