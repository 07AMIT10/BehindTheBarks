package com.btb.ondevice.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View

data class OverlayState(
    val bbox: FloatArray? = null, // [x1, y1, x2, y2] in normalized [0, 1] coords
    val keypoints: Map<String, FloatArray>? = null, // name -> [x, y, conf] in normalized coords
    val wagRoi: FloatArray? = null, // [x1, y1, x2, y2] in normalized coords
    val emotionLabel: String? = null,
    val confidence: Float = 0f,
    val latencyMs: Double = 0.0,
    val fps: Double = 0.0,
    val degradeLevel: String? = null,
)

class DebugOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    private val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 5f
        color = Color.parseColor("#00E676") // Bright green
    }

    private val wagPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
        color = Color.parseColor("#00E5FF") // Cyan
        pathEffect = DashPathEffect(floatArrayOf(10f, 10f), 0f)
    }

    private val bonePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
        color = Color.parseColor("#FFD600") // Amber yellow
    }

    private val pointPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.parseColor("#FF1744") // Red
    }

    private val textBgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.argb(180, 0, 0, 0) // Semi-transparent black
    }

    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textSize = 36f
        isFakeBoldText = true
    }

    private var state: OverlayState = OverlayState()
    private val boxRect = RectF()
    private val wagRect = RectF()

    fun updateState(newState: OverlayState) {
        state = newState
        postInvalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0 || h <= 0) return

        // 1. Draw Bounding Box
        state.bbox?.let { box ->
            if (box.size >= 4) {
                boxRect.set(box[0] * w, box[1] * h, box[2] * w, box[3] * h)
                canvas.drawRect(boxRect, boxPaint)
            }
        }

        // 2. Draw Wag ROI
        state.wagRoi?.let { roi ->
            if (roi.size >= 4) {
                wagRect.set(roi[0] * w, roi[1] * h, roi[2] * w, roi[3] * h)
                canvas.drawRect(wagRect, wagPaint)
            }
        }

        // 3. Draw Skeleton Bones & Keypoints
        val kpts = state.keypoints
        if (kpts != null) {
            for ((p1, p2) in SKELETON_PAIRS) {
                val pt1 = kpts[p1]
                val pt2 = kpts[p2]
                if (pt1 != null && pt2 != null && pt1.size >= 3 && pt2.size >= 3) {
                    if (pt1[2] >= 0.3f && pt2[2] >= 0.3f) {
                        canvas.drawLine(pt1[0] * w, pt1[1] * h, pt2[0] * w, pt2[1] * h, bonePaint)
                    }
                }
            }

            for ((_, pt) in kpts) {
                if (pt.size >= 3 && pt[2] >= 0.3f) {
                    canvas.drawCircle(pt[0] * w, pt[1] * h, 6f, pointPaint)
                }
            }
        }

        // 4. Draw Status & Emotion Badge
        val label = state.emotionLabel
        if (label != null || state.fps > 0 || state.latencyMs > 0) {
            val confPct = (state.confidence * 100).toInt()
            val text = buildString {
                if (label != null) {
                    append(label.uppercase())
                    if (state.confidence > 0) append(" ($confPct%)")
                    append(" | ")
                }
                if (state.fps > 0) append(String.format("%.1f fps", state.fps))
                if (state.latencyMs > 0) append(String.format(" (%.0f ms)", state.latencyMs))
                if (state.degradeLevel != null) append(" [${state.degradeLevel}]")
            }

            val padding = 16f
            val textWidth = textPaint.measureText(text)
            val textHeight = textPaint.textSize
            val bgRect = RectF(20f, 20f, 20f + textWidth + padding * 2, 20f + textHeight + padding * 2)

            canvas.drawRoundRect(bgRect, 8f, 8f, textBgPaint)
            canvas.drawText(text, 20f + padding, 20f + padding + textHeight - 4f, textPaint)
        }
    }

    companion object {
        val SKELETON_PAIRS = listOf(
            "L_Eye" to "R_Eye",
            "Nose" to "Neck",
            "Neck" to "Root_of_tail",
            "L_Shoulder" to "L_Elbow",
            "L_Elbow" to "L_F_Paw",
            "R_Shoulder" to "R_Elbow",
            "R_Elbow" to "R_F_Paw",
            "Neck" to "L_Shoulder",
            "Neck" to "R_Shoulder",
            "Root_of_tail" to "L_Hip",
            "Root_of_tail" to "R_Hip",
            "L_Hip" to "L_Knee",
            "L_Knee" to "L_B_Paw",
            "R_Hip" to "R_Knee",
            "R_Knee" to "R_B_Paw",
        )
    }
}
