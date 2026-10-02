package com.btb.ondevice.ui

import android.app.Activity
import android.os.Bundle
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(
            TextView(this).apply {
                text = getString(com.btb.ondevice.R.string.scaffold_notice)
            },
        )
    }
}