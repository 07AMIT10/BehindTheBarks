package com.btb.ondevice.ml

import org.tensorflow.lite.InterpreterApi
import java.io.File
import java.nio.ByteBuffer

class LiteRtRunner(
    override val name: String,
    override val backend: Backend,
    private val interpreter: InterpreterApi,
) : ModelRunner {

    private val lock = Any()
    @Volatile private var closed = false

    override fun run(inputs: Array<ByteBuffer>, outputs: Array<ByteBuffer>) {
        synchronized(lock) {
            check(!closed) { "ModelRunner '$name' has been closed" }
            if (inputs.size == 1 && outputs.size == 1) {
                inputs[0].rewind()
                outputs[0].rewind()
                interpreter.run(inputs[0], outputs[0])
            } else {
                for (inp in inputs) inp.rewind()
                for (out in outputs) out.rewind()

                val outputMap = HashMap<Int, Any>(outputs.size)
                for (i in outputs.indices) {
                    outputMap[i] = outputs[i]
                }
                val inputObjects = Array<Any>(inputs.size) { i -> inputs[i] }
                interpreter.runForMultipleInputsOutputs(inputObjects, outputMap)
            }
        }
    }

    override fun warmup(n: Int) {
        if (n <= 0) return
        val numInputs = interpreter.inputTensorCount
        val numOutputs = interpreter.outputTensorCount

        val dummyInputs = Array(numInputs) { idx ->
            val tensor = interpreter.getInputTensor(idx)
            val bytes = tensor.numBytes()
            ByteBuffer.allocateDirect(bytes)
        }
        val dummyOutputs = Array(numOutputs) { idx ->
            val tensor = interpreter.getOutputTensor(idx)
            val bytes = tensor.numBytes()
            ByteBuffer.allocateDirect(bytes)
        }

        for (i in 0 until n) {
            run(dummyInputs, dummyOutputs)
        }
    }

    override fun close() {
        synchronized(lock) {
            if (!closed) {
                closed = true
                interpreter.close()
            }
        }
    }

    companion object {
        fun create(
            name: String,
            modelBuffer: ByteBuffer,
            backend: Backend = Backend.CPU_XNNPACK,
            threads: Int = 2,
        ): LiteRtRunner {
            val options = InterpreterApi.Options()
            options.setNumThreads(threads)
            if (backend == Backend.CPU_XNNPACK) {
                options.setUseXNNPACK(true)
            }
            val interpreter = InterpreterApi.create(modelBuffer, options)
            return LiteRtRunner(name, backend, interpreter)
        }

        fun create(
            name: String,
            modelFile: File,
            backend: Backend = Backend.CPU_XNNPACK,
            threads: Int = 2,
        ): LiteRtRunner {
            val options = InterpreterApi.Options()
            options.setNumThreads(threads)
            if (backend == Backend.CPU_XNNPACK) {
                options.setUseXNNPACK(true)
            }
            val interpreter = InterpreterApi.create(modelFile, options)
            return LiteRtRunner(name, backend, interpreter)
        }
    }
}
