package com.btb.ondevice.capture

import android.view.Surface
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Regression for the MonitorService crash: `Context.getDisplay()` throws on a non-visual context
 * (a Service or the application context), so CameraSource.start() killed the app on every START.
 * The initial rotation must be read from a context that has no display association.
 */
@RunWith(AndroidJUnit4::class)
class CameraSourceRotationInstrumentedTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun initialRotationWorksFromNonVisualContext() {
        var threw = false
        try {
            context.display
        } catch (e: UnsupportedOperationException) {
            threw = true
        }
        // On API 30+ the plain context must refuse to report a display; this is the crashing path.
        assertTrue("expected a non-visual context on API 30+", threw || android.os.Build.VERSION.SDK_INT < 30)

        val rotation = initialDisplayRotation(context)
        assertTrue(
            "rotation $rotation is not a Surface rotation",
            rotation in setOf(Surface.ROTATION_0, Surface.ROTATION_90, Surface.ROTATION_180, Surface.ROTATION_270),
        )
    }
}
