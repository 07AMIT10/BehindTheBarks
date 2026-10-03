package com.btb.ondevice.ml

import java.nio.ByteBuffer

enum class Backend {
    CPU_XNNPACK,
    GPU,
    ORT_XNNPACK,
}

interface ModelRunner : AutoCloseable {
    val name: String
    val backend: Backend
    fun run(inputs: Array<ByteBuffer>, outputs: Array<ByteBuffer>)
    fun warmup(n: Int = 3)
}
