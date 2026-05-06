package io.github.ryo100794.pdocker

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.io.File
import java.io.InputStream
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONArray
import org.json.JSONObject

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
    private var engineSocket: LocalSocket? = null
    private var reader: Thread? = null
    private val alive = AtomicBoolean(false)

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
        val socket = engineSocket
        if (socket != null) {
            runCatching {
                recordEngineExecInput(bytes)
                socket.outputStream.write(bytes)
                socket.outputStream.flush()
            }.onFailure {
                recordEngineExecEvent("input-failed", error = it.message.orEmpty())
            }
            return
        }
        if (!alive.get() || fd < 0) return
        PtyNative.write(fd, bytes)
    }

    @JavascriptInterface
    fun resize(rows: Int, cols: Int) {
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
            clipboard.setPrimaryClip(ClipData.newPlainText("pdocker terminal", text))
            Toast.makeText(activity, activity.getString(R.string.toast_copied), Toast.LENGTH_SHORT).show()
        }
    }

    fun close() {
        alive.set(false)
        if (fd >= 0) PtyNative.close(fd)
        fd = -1
        runCatching { engineSocket?.close() }
        engineSocket = null
        reader?.interrupt()
    }

    private fun startEngineExec(containerId: String) {
        if (containerId.isBlank()) {
            sendTerminalText("[pdocker] missing container id\n")
            return
        }
        resetEngineExecInputDiagnostics(containerId)
        alive.set(true)
        reader = Thread({
            runCatching {
                sendTerminalText("[pdocker] Engine exec -it: $containerId\n")
                val execId = createEngineExec(containerId)
                recordEngineExecEvent("created", execId = execId)
                val socket = startEngineExecStream(execId)
                engineSocket = socket
                recordEngineExecEvent("stream-started", execId = execId)
                val buffer = ByteArray(4096)
                while (alive.get()) {
                    val n = socket.inputStream.read(buffer)
                    if (n <= 0) break
                    val chunk = buffer.copyOf(n)
                    onOutput?.invoke(chunk)
                    sendTerminalBytes(chunk)
                }
            }.onFailure {
                recordEngineExecEvent("failure", error = it.message.orEmpty())
                sendTerminalText("\n[pdocker] Engine exec failed: ${it.message.orEmpty()}\n")
            }
            recordEngineExecEvent("reader-ended")
            alive.set(false)
        }, "engine-exec-reader").also { it.start() }
    }

    private fun createEngineExec(containerId: String): String {
        val payload = JSONObject()
            .put("AttachStdin", true)
            .put("AttachStdout", true)
            .put("AttachStderr", true)
            .put("Tty", true)
            .put("Env", JSONArray(listOf("TERM=xterm-256color", "COLORTERM=truecolor", "ENV=", "BASH_ENV=")))
            .put("Cmd", JSONArray(listOf("/bin/sh", "-lc", "if command -v /bin/bash >/dev/null 2>&1; then exec /bin/bash -i; else exec /bin/sh -i; fi")))
        val response = engineRequest(
            "POST",
            "/containers/${DockerEngineClient.encodePath(containerId)}/exec",
            payload.toString().toByteArray(Charsets.UTF_8),
        )
        val text = response.body.toString(Charsets.UTF_8)
        recordEngineExecEvent("create-response", status = response.status, body = text)
        check(response.status in 200..299) { text.ifBlank { "HTTP ${response.status}" } }
        return JSONObject(text).getString("Id")
    }

    private fun startEngineExecStream(execId: String): LocalSocket {
        val payload = JSONObject()
            .put("Detach", false)
            .put("Tty", true)
        val body = payload.toString().toByteArray(Charsets.UTF_8)
        val socket = connectEngineSocket()
        val header = buildString {
            append("POST /exec/$execId/start HTTP/1.1\r\n")
            append("Host: pdocker\r\n")
            append("Connection: Upgrade\r\n")
            append("Upgrade: tcp\r\n")
            append("Content-Type: application/json\r\n")
            append("Content-Length: ").append(body.size).append("\r\n")
            append("\r\n")
        }.toByteArray(Charsets.UTF_8)
        socket.outputStream.write(header)
        socket.outputStream.write(body)
        socket.outputStream.flush()
        val head = readHttpHead(socket.inputStream)
        if (!head.startsWith("HTTP/1.1 101") && !head.startsWith("HTTP/1.0 101")) {
            val errorBody = readHttpBodyAfterHead(head, socket.inputStream)
            val detail = listOf(head, errorBody).filter { it.isNotBlank() }.joinToString("\n")
            recordEngineExecEvent("start-response", execId = execId, body = detail)
            socket.close()
            error(detail.ifBlank { head.lineSequence().firstOrNull().orEmpty() })
        }
        recordEngineExecEvent("start-response", execId = execId, body = head)
        return socket
    }

    private fun readHttpBodyAfterHead(head: String, input: java.io.InputStream): String {
        val contentLength = head.lineSequence()
            .firstOrNull { it.startsWith("Content-Length:", ignoreCase = true) }
            ?.substringAfter(':')
            ?.trim()
            ?.toIntOrNull()
            ?: return ""
        if (contentLength <= 0) return ""
        val body = ByteArray(contentLength)
        var off = 0
        while (off < contentLength) {
            val n = input.read(body, off, contentLength - off)
            if (n <= 0) break
            off += n
        }
        return body.copyOf(off).toString(Charsets.UTF_8)
    }

    private data class EngineResponse(val status: Int, val body: ByteArray)

    private fun engineRequest(
        method: String,
        path: String,
        body: ByteArray = ByteArray(0),
        contentType: String = "application/json",
    ): EngineResponse {
        connectEngineSocket().use { socket ->
            val header = buildString {
                append(method).append(' ').append(path).append(" HTTP/1.1\r\n")
                append("Host: pdocker\r\n")
                append("Connection: close\r\n")
                if (body.isNotEmpty()) {
                    append("Content-Type: ").append(contentType).append("\r\n")
                    append("Content-Length: ").append(body.size).append("\r\n")
                }
                append("\r\n")
            }.toByteArray(Charsets.UTF_8)
            socket.outputStream.write(header)
            if (body.isNotEmpty()) socket.outputStream.write(body)
            socket.outputStream.flush()
            val head = readHttpHead(socket.inputStream)
            val status = head.lineSequence().firstOrNull()
                ?.split(' ')
                ?.getOrNull(1)
                ?.toIntOrNull()
                ?: 0
            return EngineResponse(status, socket.inputStream.readBytes())
        }
    }

    private fun connectEngineSocket(): LocalSocket =
        LocalSocket().apply {
            val sock = File(activity.filesDir, "pdocker/pdockerd.sock")
            connect(LocalSocketAddress(sock.absolutePath, LocalSocketAddress.Namespace.FILESYSTEM))
        }

    private fun readHttpHead(input: InputStream): String {
        val bytes = ArrayList<Byte>(512)
        var matched = 0
        val marker = byteArrayOf('\r'.code.toByte(), '\n'.code.toByte(), '\r'.code.toByte(), '\n'.code.toByte())
        while (true) {
            val b = input.read()
            if (b < 0) break
            bytes += b.toByte()
            matched = if (b.toByte() == marker[matched]) matched + 1 else if (b == '\r'.code) 1 else 0
            if (matched == marker.size) break
        }
        return bytes.toByteArray().toString(Charsets.UTF_8)
    }

    private fun sendTerminalText(text: String) {
        sendTerminalBytes(text.toByteArray(Charsets.UTF_8))
    }

    private fun sendTerminalBytes(bytes: ByteArray) {
        val b64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
        activity.runOnUiThread {
            webView.evaluateJavascript("window.pdockerRecv('$b64')", null)
        }
    }

    private fun resetEngineExecInputDiagnostics(containerId: String) {
        runCatching {
            val file = engineExecInputDiagnosticsFile()
            file.parentFile?.mkdirs()
            file.writeText(
                JSONObject()
                    .put("event", "start")
                    .put("container", containerId)
                    .put("timestampMs", System.currentTimeMillis())
                    .toString() + "\n",
            )
        }
    }

    private fun recordEngineExecInput(bytes: ByteArray) {
        runCatching {
            val file = engineExecInputDiagnosticsFile()
            file.parentFile?.mkdirs()
            if (file.length() > 64 * 1024) file.delete()
            file.appendText(
                JSONObject()
                    .put("event", "input")
                    .put("timestampMs", System.currentTimeMillis())
                    .put("bytes", bytes.size)
                    .put("hex", bytes.joinToString(" ") { "%02x".format(it.toInt() and 0xff) })
                    .put("text", bytes.toString(Charsets.UTF_8).replace("\u001b", "\\e"))
                    .toString() + "\n",
            )
        }
    }

    private fun recordEngineExecEvent(
        event: String,
        execId: String = "",
        status: Int = 0,
        body: String = "",
        error: String = "",
    ) {
        runCatching {
            val file = engineExecInputDiagnosticsFile()
            file.parentFile?.mkdirs()
            file.appendText(
                JSONObject()
                    .put("event", event)
                    .put("timestampMs", System.currentTimeMillis())
                    .put("execId", execId)
                    .put("status", status)
                    .put("body", body.take(2048))
                    .put("error", error)
                    .toString() + "\n",
            )
        }
    }

    private fun engineExecInputDiagnosticsFile(): File =
        File(activity.filesDir, "pdocker/diagnostics/engine-exec-input-latest.jsonl")

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
