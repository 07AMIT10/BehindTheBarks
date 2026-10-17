package com.btb.ondevice.ui

/**
 * The words TalkBack should speak for a label that may carry emoji or status symbols
 * ("🛡️ Privacy ON" -> "Privacy ON", "● Live · Connected" -> "Live · Connected"). Letters, digits,
 * spaces and plain punctuation survive; pictographs and symbols are dropped.
 */
internal fun spokenLabel(text: String): String =
    text.replace(Regex("[^\\p{L}\\p{N}\\s()'.,:/&+\\-\u00B7]+"), " ")
        .replace(Regex("\\s+"), " ")
        .trim()
