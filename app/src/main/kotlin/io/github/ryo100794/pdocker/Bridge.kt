package io.github.ryo100794.pdocker

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Bridge between xterm.js (WebView) and a PTY child.
 *
 * JS side calls:
 *   PdockerBridge.start("docker exec -it CID sh")
 *   PdockerBridge.startInitial()
 *   PdockerBridge.input(base64_utf8)
 *   PdockerBridge.resize(rows, cols)
 *
 * Kotlin pushes output back via `window.pdockerRecv(base64)` on the UI thread.
 */
class Bridge(
    private val activity: AppCompatActivity,
    private val webView: WebView,
    private val initialCommand: String = "sh",
    private val onOutput: ((ByteArray) -> Unit)? = null,
) {
    private var fd: Int = -1
    private val engineExecSession = AtomicReference<EngineExecSession?>(null)
    private var reader: Thread? = null
    private val alive = AtomicBoolean(false)
    private val lastTerminalSize = AtomicReference<Pair<Int, Int>?>(null)

    @JavascriptInterface
    fun start(cmdline: String) {
        if (alive.get()) return
        if (cmdline.startsWith(ENGINE_EXEC_PREFIX)) {
            startEngineExec(cmdline.removePrefix(ENGINE_EXEC_PREFIX).trim())
            return
        }
        val shell = detectShell()
        // Pass the requested cmdline to `sh -c` so xterm.js doesn't need
        // to tokenize.
        val argv = arrayOf("sh", "-c", cmdline)
        // Stage runtime so crane/direct-runtime symlinks exist and the socket
        // path is predictable. PdockerdRuntime.prepare is idempotent.
        val runtime = PdockerdRuntime.prepare(activity)
        val sock = File(activity.filesDir, "pdocker/pdockerd.sock")
        val env = arrayOf(
            "TERM=xterm-256color",
            "HOME=${activity.filesDir}",
            // Product APKs do not bundle upstream Docker CLI; docker-bin is
            // still first so pdocker-native helpers staged there are visible.
            "PATH=${runtime.absolutePath}/docker-bin:/system/bin:/system/xbin",
            "DOCKER_HOST=unix://${sock.absolutePath}",
            "DOCKER_BUILDKIT=0",
            "COMPOSE_DOCKER_CLI_BUILD=0",
            "BUILDKIT_PROGRESS=plain",
            "COMPOSE_PROGRESS=plain",
            "COMPOSE_MENU=false",
            "PDOCKER_RUNTIME_BACKEND=${BuildConfig.PDOCKER_RUNTIME_BACKEND}",
            "PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC=${if (BuildConfig.PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC) "1" else "0"}",
            "PDOCKER_DIRECT_TRACE_SYSCALLS=0",
            "PDOCKER_DIRECT_TRACE_MODE=seccomp",
            // Test-only Docker CLI staging may use this config path. Normal UI
            // actions use Engine API/native orchestration instead.
            "DOCKER_CONFIG=${runtime.absolutePath}/docker-bin"
        )
        fd = PtyNative.open(shell, argv, env)
        if (fd < 0) return
        alive.set(true)
        reader = Thread({
            val buf = ByteArray(4096)
            while (alive.get()) {
                val n = PtyNative.read(fd, buf)
                if (n <= 0) break
                onOutput?.invoke(buf.copyOf(n))
                val b64 = Base64.encodeToString(buf, 0, n, Base64.NO_WRAP)
                activity.runOnUiThread {
                    webView.evaluateJavascript("window.pdockerRecv('$b64')", null)
                }
            }
            alive.set(false)
        }, "pty-reader").also { it.start() }
    }

    @JavascriptInterface
    fun initialCommand(): String = initialCommand

    @JavascriptInterface
    fun startInitial() {
        start(initialCommand)
    }

    @JavascriptInterface
    fun readOnly(): Boolean = false

    @JavascriptInterface
    fun input(b64: String) {
        val bytes = Base64.decode(b64, Base64.DEFAULT)
        engineExecSession.get()?.let { session ->
            session.write(bytes)
            return
        }
        if (!alive.get() || fd < 0) return
        PtyNative.write(fd, bytes)
    }

    @JavascriptInterface
    fun resize(rows: Int, cols: Int) {
        if (rows > 0 && cols > 0) {
            lastTerminalSize.set(rows to cols)
        }
        engineExecSession.get()?.let { session ->
            session.resize(rows, cols)
            return
        }
        if (!alive.get() || fd < 0) return
        PtyNative.resize(fd, rows, cols)
    }

    @JavascriptInterface
    fun copyToClipboard(b64: String) {
        val text = runCatching {
            String(Base64.decode(b64, Base64.DEFAULT), Charsets.UTF_8)
        }.getOrDefault("")
        if (text.isEmpty()) return
        activity.runOnUiThread {
            val clipboard = activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("Skydnir terminal", text))
            Toast.makeText(activity, activity.getString(R.string.toast_copied), Toast.LENGTH_SHORT).show()
        }
    }

    fun close() {
        alive.set(false)
        if (fd >= 0) PtyNative.close(fd)
        fd = -1
        engineExecSession.getAndSet(null)?.close()
        reader?.interrupt()
    }

    private fun startEngineExec(containerId: String) {
        lateinit var session: EngineExecSession
        session = EngineExecSession(
            filesDir = activity.filesDir,
            initialTerminalSize = lastTerminalSize.get(),
            emitTerminalBytes = { bytes -> sendTerminalBytes(bytes) },
            onOutput = onOutput,
            onEnded = {
                engineExecSession.compareAndSet(session, null)
                alive.set(false)
            },
        )
        if (!engineExecSession.compareAndSet(null, session)) return
        if (session.start(containerId)) {
            alive.set(true)
        } else {
            engineExecSession.compareAndSet(session, null)
        }
    }

    private fun sendTerminalBytes(bytes: ByteArray) {
        val b64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
        activity.runOnUiThread {
            webView.evaluateJavascript("window.pdockerRecv('$b64')", null)
        }
    }

    private fun detectShell(): String {
        // Prefer the bundled proot-run entrypoint once assets are unpacked;
        // fall back to /system/bin/sh for the scaffold phase.
        val bundled = File(activity.applicationInfo.nativeLibraryDir, "libpdocker-sh.so")
        return if (bundled.exists()) bundled.absolutePath else "/system/bin/sh"
    }

    companion object {
        const val ENGINE_EXEC_PREFIX = "pdocker-engine-exec:"
    }
}
