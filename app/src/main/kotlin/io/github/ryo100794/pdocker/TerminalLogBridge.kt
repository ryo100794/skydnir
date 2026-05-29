package io.github.ryo100794.pdocker

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.util.Base64
import android.webkit.JavascriptInterface
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

/**
 * No-op xterm.js bridge for terminal-emulated log panes.
 *
 * The shared xterm page expects a PdockerBridge object so copy/select and
 * resize hooks continue to work, but log panes must not spawn a shell.
 */
@Suppress("UNUSED_PARAMETER")
class TerminalLogBridge(private val activity: AppCompatActivity) {
    @JavascriptInterface
    fun start(_cmdline: String) {
        // Display-only pane: output is pushed from Kotlin.
    }

    @JavascriptInterface
    fun initialCommand(): String = ""

    @JavascriptInterface
    fun readOnly(): Boolean = true

    @JavascriptInterface
    fun input(_b64: String) {
        // Display-only pane.
    }

    @JavascriptInterface
    fun resize(_rows: Int, _cols: Int) {
        // Display-only pane.
    }

    @JavascriptInterface
    fun copyToClipboard(b64: String) {
        val text = runCatching {
            String(Base64.decode(b64, Base64.DEFAULT), Charsets.UTF_8)
        }.getOrDefault("")
        if (text.isEmpty()) return
        activity.runOnUiThread {
            val clipboard = activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("Skydnir log", text))
            Toast.makeText(activity, activity.getString(R.string.toast_copied), Toast.LENGTH_SHORT).show()
        }
    }
}
