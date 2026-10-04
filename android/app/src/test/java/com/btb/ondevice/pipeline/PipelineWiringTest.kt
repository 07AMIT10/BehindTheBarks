package com.btb.ondevice.pipeline

import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.ml.Backend
import com.btb.ondevice.ml.ModelRunner
import com.btb.ondevice.vision.DogDetector
import com.google.common.truth.Truth.assertThat
import java.nio.ByteBuffer
import org.junit.jupiter.api.Test

/**
 * Regression test for the MonitorService wiring gap (2026-10-04): the pipeline was
 * built with null model runners, so DogDetector.detect() returned null on every frame
 * and no rules envelopes ever reached the backend. A pipeline whose detector has a
 * runner serving one confident dog box must yield a non-null rules label.
 */
class PipelineWiringTest {

    /** Fake YOLO raw-head runner: every invoke writes one confident dog box. */
    private class CannedDogRunner : ModelRunner {
        override val name = "detector"
        override val backend = Backend.CPU_XNNPACK

        override fun run(inputs: Array<ByteBuffer>, outputs: Array<ByteBuffer>) {
            val out = FloatArray(84 * 2100)
            val anchor = 7
            out[0 * 2100 + anchor] = 0.5f // cx
            out[1 * 2100 + anchor] = 0.5f // cy
            out[2 * 2100 + anchor] = 0.4f // w
            out[3 * 2100 + anchor] = 0.5f // h
            out[(4 + 16) * 2100 + anchor] = 0.49f // dog score (int8 head tops out ~0.5)
            outputs[0].rewind()
            outputs[0].asFloatBuffer().put(out)
        }

        override fun warmup(n: Int) {}
        override fun close() {}
    }

    @Test
    fun `detector with a runner produces rules on the first frame`() {
        val pipe = OnDevicePipeline(
            config = DataConfig(),
            dogDetector = DogDetector(runner = CannedDogRunner()),
        )
        val rgba = ByteBuffer.allocateDirect(640 * 480 * 4)
        val (_, rules) = pipe.processFrame(ts = 5.0, width = 640, height = 480, rgba = rgba)
        assertThat(rules).isNotNull()
    }

    @Test
    fun `detector without a runner yields no rules`() {
        val pipe = OnDevicePipeline(config = DataConfig())
        val rgba = ByteBuffer.allocateDirect(640 * 480 * 4)
        val (_, rules) = pipe.processFrame(ts = 5.0, width = 640, height = 480, rgba = rgba)
        assertThat(rules).isNull()
    }
}
