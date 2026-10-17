package com.btb.ondevice.audio

/**
 * IMA-ADPCM encoder used by the phone for live audio (docs/AUDIO_WIRE.md), plus the decoder the JVM tests
 * use to check reconstruction. Bit-exact with backend/web/audio_codec.py: the shared vectors in
 * tests/vectors/audio_v1.json must match byte for byte (encoder) and sample for sample (decoder).
 *
 * Block (codec 1): predictor s16 LE | step_index u8 (0 here) | 0x00 | 800 bytes of nibbles, low nibble first.
 * A 1600-sample chunk is one independent block, so a dropped frame costs only its own 100 ms.
 */
object AdpcmCodec {
    const val CHUNK_SAMPLES = 1600
    const val BLOCK_BYTES = 804
    const val PCM16_PAYLOAD_BYTES = CHUNK_SAMPLES * 2
    const val FRAME_MAGIC = 0x02
    const val FRAME_HEADER_BYTES = 10
    const val CODEC_PCM16 = 0
    const val CODEC_IMA_ADPCM = 1
    const val MAX_STEP_INDEX = 88

    private const val S16_MIN = -32768
    private const val S16_MAX = 32767
    private const val U32_MAX = 0xFFFFFFFFL

    private val STEP_TABLE = intArrayOf(
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
        50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
        253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
        1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
        3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
        11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
        32767,
    )
    private val INDEX_TABLE = intArrayOf(-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)

    private class State(var predictor: Int, var index: Int)

    /** The decoder's update for one 4-bit code. The encoder runs it too, so both stay in lockstep. */
    private fun apply(state: State, code: Int) {
        val step = STEP_TABLE[state.index]
        var diff = step shr 3
        if ((code and 4) != 0) diff += step
        if ((code and 2) != 0) diff += step shr 1
        if ((code and 1) != 0) diff += step shr 2
        val next = if ((code and 8) != 0) state.predictor - diff else state.predictor + diff
        state.predictor = next.coerceIn(S16_MIN, S16_MAX)
        state.index = (state.index + INDEX_TABLE[code]).coerceIn(0, MAX_STEP_INDEX)
    }

    /** Encode exactly [CHUNK_SAMPLES] samples into one [BLOCK_BYTES] block with initial step index 0. */
    fun encodeBlock(samples: ShortArray): ByteArray {
        require(samples.size == CHUNK_SAMPLES) { "a chunk is $CHUNK_SAMPLES samples, got ${samples.size}" }
        val out = ByteArray(BLOCK_BYTES)
        val first = samples[0].toInt()
        out[0] = (first and 0xFF).toByte()
        out[1] = ((first shr 8) and 0xFF).toByte()
        // out[2] = step_index 0 and out[3] = reserved 0 are already zero
        val state = State(first, 0)
        for (n in 1 until CHUNK_SAMPLES) {
            val step = STEP_TABLE[state.index]
            var diff = samples[n].toInt() - state.predictor
            var code = 0
            if (diff < 0) {
                code = 8
                diff = -diff
            }
            if (diff >= step) {
                code = code or 4
                diff -= step
            }
            if (diff >= (step shr 1)) {
                code = code or 2
                diff -= step shr 1
            }
            if (diff >= (step shr 2)) {
                code = code or 1
            }
            apply(state, code)
            val j = n - 1 // code index: sample n uses code j, low nibble first
            val at = 4 + j / 2
            out[at] = if (j % 2 == 0) {
                code.toByte()
            } else {
                (out[at].toInt() or (code shl 4)).toByte()
            }
        }
        return out
    }

    /** Decode one block to [CHUNK_SAMPLES] samples. Throws [IllegalArgumentException] on a malformed block. */
    fun decodeBlock(block: ByteArray): ShortArray {
        require(block.size == BLOCK_BYTES) { "ADPCM block must be $BLOCK_BYTES bytes, got ${block.size}" }
        val predictor = ((block[0].toInt() and 0xFF) or (block[1].toInt() shl 8)).toShort().toInt()
        val index = block[2].toInt() and 0xFF
        require(index <= MAX_STEP_INDEX) { "step index $index out of range 0..$MAX_STEP_INDEX" }
        require(block[3].toInt() == 0) { "reserved header byte must be 0" }
        require((block[BLOCK_BYTES - 1].toInt() and 0xFF) shr 4 == 0) { "padding nibble must be 0" }
        val state = State(predictor, index)
        val out = ShortArray(CHUNK_SAMPLES)
        out[0] = predictor.toShort()
        for (j in 0 until CHUNK_SAMPLES - 1) {
            val byte = block[4 + j / 2].toInt() and 0xFF
            val code = if (j % 2 == 0) byte and 0x0F else byte shr 4
            apply(state, code)
            out[j + 1] = state.predictor.toShort()
        }
        return out
    }

    /**
     * Build a phone frame: 0x02 | codec u8 | seq u32 LE | t_ms u32 LE | payload.
     * The payload length must match the codec (PCM16 3200 bytes, IMA-ADPCM 804); codec 2 is reserved.
     */
    fun packFrame(codec: Int, seq: Long, tMs: Long, payload: ByteArray): ByteArray {
        require(seq in 0..U32_MAX && tMs in 0..U32_MAX) { "seq and t_ms are u32" }
        val expected = when (codec) {
            CODEC_PCM16 -> PCM16_PAYLOAD_BYTES
            CODEC_IMA_ADPCM -> BLOCK_BYTES
            else -> throw IllegalArgumentException("codec $codec is not supported (0 PCM16, 1 IMA-ADPCM)")
        }
        require(payload.size == expected) { "codec $codec payload must be $expected bytes, got ${payload.size}" }
        val out = ByteArray(FRAME_HEADER_BYTES + payload.size)
        out[0] = FRAME_MAGIC.toByte()
        out[1] = codec.toByte()
        writeU32Le(out, 2, seq)
        writeU32Le(out, 6, tMs)
        payload.copyInto(out, FRAME_HEADER_BYTES)
        return out
    }

    private fun writeU32Le(out: ByteArray, at: Int, value: Long) {
        for (i in 0 until 4) {
            out[at + i] = ((value shr (8 * i)) and 0xFF).toByte()
        }
    }
}
