package com.btb.ondevice.capture

data class FpsRange(val lower: Int, val upper: Int)

object CaptureConfig {
    /**
     * Given available FPS ranges, pick the lowest available range containing targetFps (e.g. 15),
     * falling back to one containing 30, or the lowest available upper bound.
     */
    fun selectFpsRange(availableRanges: List<FpsRange>, targetFps: Int = 15): FpsRange? {
        if (availableRanges.isEmpty()) return null

        val containingTarget = availableRanges.filter { it.lower <= targetFps && targetFps <= it.upper }
        if (containingTarget.isNotEmpty()) {
            return containingTarget.minWithOrNull(
                compareBy<FpsRange> { it.upper }
                    .thenBy { it.lower }
            )
        }

        val containing30 = availableRanges.filter { it.lower <= 30 && 30 <= it.upper }
        if (containing30.isNotEmpty()) {
            return containing30.minWithOrNull(
                compareBy<FpsRange> { it.upper }
                    .thenBy { it.lower }
            )
        }

        return availableRanges.minByOrNull { it.upper }
    }

    /**
     * Calculate rotated dimensions.
     * When rotationDegrees is 90 or 270, width and height are swapped.
     */
    fun getRotatedDimensions(width: Int, height: Int, rotationDegrees: Int): Pair<Int, Int> {
        val normalizedRotation = ((rotationDegrees % 360) + 360) % 360
        return if (normalizedRotation == 90 || normalizedRotation == 270) {
            Pair(height, width)
        } else {
            Pair(width, height)
        }
    }

    /**
     * Map a point (x, y) normalized [0, 1] from sensor coordinate space to display coordinate space.
     */
    fun mapSensorPointToDisplay(x: Float, y: Float, rotationDegrees: Int): Pair<Float, Float> {
        val r = ((rotationDegrees % 360) + 360) % 360
        return when (r) {
            90 -> Pair(1f - y, x)
            180 -> Pair(1f - x, 1f - y)
            270 -> Pair(y, 1f - x)
            else -> Pair(x, y)
        }
    }

    /**
     * Convert monotonic nanoseconds timestamp from CameraX to epoch seconds.
     */
    fun timestampNsToEpochS(timestampNs: Long, bootToEpochOffsetS: Double): Double {
        return (timestampNs / 1_000_000_000.0) + bootToEpochOffsetS
    }
}
