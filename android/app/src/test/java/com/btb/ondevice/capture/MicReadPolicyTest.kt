package com.btb.ondevice.capture

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

/** M4: the empty-read ladder of MicSource, pure logic. */
class MicReadPolicyTest {

    @Test
    fun `the first few empty reads retry immediately`() {
        assertThat(MicReadPolicy.onEmptyRead(1)).isEqualTo(MicReadPolicy.RETRY)
        assertThat(MicReadPolicy.onEmptyRead(2)).isEqualTo(MicReadPolicy.RETRY)
        assertThat(MicReadPolicy.onEmptyRead(MicReadPolicy.PAUSE_AFTER - 1)).isEqualTo(MicReadPolicy.RETRY)
    }

    @Test
    fun `a run of empty reads pauses out of the spin instead of burning a core`() {
        assertThat(MicReadPolicy.onEmptyRead(MicReadPolicy.PAUSE_AFTER)).isEqualTo(MicReadPolicy.PAUSE)
        assertThat(MicReadPolicy.onEmptyRead(10)).isEqualTo(MicReadPolicy.PAUSE)
        assertThat(MicReadPolicy.onEmptyRead(MicReadPolicy.GIVE_UP_AFTER - 1)).isEqualTo(MicReadPolicy.PAUSE)
        assertThat(MicReadPolicy.PAUSE.sleepMs).isAtLeast(50L)
    }

    @Test
    fun `a dead recorder is given up, not spun on forever`() {
        assertThat(MicReadPolicy.onEmptyRead(MicReadPolicy.GIVE_UP_AFTER)).isEqualTo(MicReadPolicy.GIVE_UP)
        assertThat(MicReadPolicy.onEmptyRead(1_000)).isEqualTo(MicReadPolicy.GIVE_UP)
    }
}
