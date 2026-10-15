package com.btb.ondevice.ui

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class ListenerIndicatorTest {

    @Test
    fun `row text shows the count and hides at zero`() {
        assertThat(ListenerIndicator.rowText(0)).isNull()
        assertThat(ListenerIndicator.rowText(-1)).isNull()
        assertThat(ListenerIndicator.rowText(1)).isEqualTo("🎧 1 listening")
        assertThat(ListenerIndicator.rowText(3)).isEqualTo("🎧 3 listening")
        assertThat(ListenerIndicator.countText(2)).isEqualTo("2 listening")
    }

    @Test
    fun `spoken description agrees with the count`() {
        assertThat(ListenerIndicator.description(1)).isEqualTo("1 person is listening to the microphone")
        assertThat(ListenerIndicator.description(2)).isEqualTo("2 people are listening to the microphone")
    }

    @Test
    fun `chime fires only on zero to more than zero`() {
        assertThat(ListenerIndicator.chimesOn(0, 1)).isTrue()
        assertThat(ListenerIndicator.chimesOn(0, 4)).isTrue()
        assertThat(ListenerIndicator.chimesOn(1, 2)).isFalse()
        assertThat(ListenerIndicator.chimesOn(2, 1)).isFalse()
        assertThat(ListenerIndicator.chimesOn(1, 0)).isFalse()
        assertThat(ListenerIndicator.chimesOn(0, 0)).isFalse()
    }

    @Test
    fun `a session 0 to 1 to 2 to 0 to 3 chimes exactly twice and ends hidden after zero`() {
        val updates = listOf(0, 1, 2, 0, 3)
        var previous = 0
        var chimes = 0
        for (next in updates) {
            if (ListenerIndicator.chimesOn(previous, next)) chimes += 1
            previous = next
        }
        assertThat(chimes).isEqualTo(2)
        assertThat(ListenerIndicator.rowText(0)).isNull()
    }
}
