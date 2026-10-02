package com.btb.ondevice.capture

import android.app.Service
import android.content.Intent
import android.os.IBinder

/** Placeholder so the manifest's camera|microphone foreground-service entry resolves; Phase 2 Task 2.4 implements it. */
class MonitorService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_NOT_STICKY
}