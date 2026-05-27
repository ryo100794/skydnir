package io.github.ryo100794.pdocker

import android.net.LocalSocket
import android.net.LocalSocketAddress
import java.io.File
import java.io.InputStream
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import org.json.JSONArray
import org.json.JSONObject

/**
 * Docker Engine backed interactive terminal session.
 *
 * This class owns the Docker-compatible exec lifecycle for the terminal:
 * create `/containers/{id}/exec`, start `/exec/{id}/start` as a hijacked raw
 * TTY stream, forward input bytes, request `/exec/{id}/resize`, and write the
 * exec diagnostics consumed by the UI/device contract tests. Bridge remains a
 * WebView/terminal adapter and should not duplicate Engine exec routing.
 */
class EngineExecSession(
    private val filesDir: File,
    private val initialTerminalSize: Pair<Int, Int>? = null,
    private val emitTerminalBytes: (ByteArray) -> Unit,
    private val onOutput: ((ByteArray) -> Unit)? = null,
    private val onEnded: (() -> Unit)? = null,
) {
    private val alive = AtomicBoolean(false)
    private val socketRef = AtomicReference<LocalSocket?>(null)
    private val execIdRef = AtomicReference<String?>(null)
    private val lastTerminalSize = AtomicReference<Pair<Int, Int>?>(initialTerminalSize)
    private var reader: Thread? = null

    fun isActive(): Boolean = alive.get()

    fun start(containerId: String): Boolean {
        if (containerId.isBlank()) {
            sendTerminalText("[skydnir] missing container id\n")
            return false
        }
        if (!alive.compareAndSet(false, true)) return false
        resetEngineExecInputDiagnostics(containerId)
        reader = Thread({
            runCatching {
                sendTerminalText("[skydnir] Engine exec -it: $containerId\n")
                val execId = createEngineExec(containerId)
                execIdRef.set(execId)
                recordEngineExecEvent("created", execId = execId)
                lastTerminalSize.get()?.let { (rows, cols) ->
                    resizeEngineExecSync(execId, rows, cols)
                }
                val socket = startEngineExecStream(execId)
                socketRef.set(socket)
                recordEngineExecEvent("stream-started", execId = execId)
                val buffer = ByteArray(4096)
                while (alive.get()) {
                    val n = socket.inputStream.read(buffer)
                    if (n <= 0) break
                    val chunk = buffer.copyOf(n)
                    onOutput?.invoke(chunk)
                    emitTerminalBytes(chunk)
                }
            }.onFailure {
                recordEngineExecEvent("failure", error = it.message.orEmpty())
                sendTerminalText("\n[skydnir] Engine exec failed: ${it.message.orEmpty()}\n")
            }
            recordEngineExecEvent("reader-ended")
            execIdRef.set(null)
            alive.set(false)
            runCatching { socketRef.getAndSet(null)?.close() }
            onEnded?.invoke()
        }, "engine-exec-reader").also { it.start() }
        return true
    }

    fun write(bytes: ByteArray) {
        val socket = socketRef.get() ?: return
        runCatching {
            recordEngineExecInput(bytes)
            socket.outputStream.write(bytes)
            socket.outputStream.flush()
        }.onFailure {
            recordEngineExecEvent("input-failed", error = it.message.orEmpty())
        }
    }

    fun resize(rows: Int, cols: Int) {
        if (rows <= 0 || cols <= 0) return
        lastTerminalSize.set(rows to cols)
        execIdRef.get()?.let { execId ->
            resizeEngineExecAsync(execId, rows, cols)
        }
    }

    fun close() {
        alive.set(false)
        execIdRef.set(null)
        runCatching { socketRef.getAndSet(null)?.close() }
        reader?.interrupt()
    }

    private fun resizeEngineExecAsync(execId: String, rows: Int, cols: Int) {
        if (rows <= 0 || cols <= 0) return
        Thread({
            resizeEngineExecSync(execId, rows, cols)
        }, "engine-exec-resize").start()
    }

    private fun resizeEngineExecSync(execId: String, rows: Int, cols: Int) {
        if (rows <= 0 || cols <= 0) return
        val path = "/exec/${DockerEngineClient.encodePath(execId)}/resize?h=$rows&w=$cols"
        runCatching {
            engineRequest(
                "POST",
                path,
            )
        }.onSuccess { response ->
            recordEngineExecEvent("resize", execId = execId, status = response.status, body = path)
        }.onFailure {
            recordEngineExecEvent("resize-failed", execId = execId, body = path, error = it.message.orEmpty())
        }
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

    private fun readHttpBodyAfterHead(head: String, input: InputStream): String {
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
            val sock = File(filesDir, "pdocker/pdockerd.sock")
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
        emitTerminalBytes(text.toByteArray(Charsets.UTF_8))
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
        File(filesDir, "pdocker/diagnostics/engine-exec-input-latest.jsonl")
}
