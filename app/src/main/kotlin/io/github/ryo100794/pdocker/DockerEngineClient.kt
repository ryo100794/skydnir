package io.github.ryo100794.pdocker

import android.net.LocalSocket
import android.net.LocalSocketAddress
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.net.URLEncoder
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.util.Locale

class DockerEngineClient(private val socket: File) {
    data class Response(
        val status: Int,
        val headers: Map<String, String>,
        val body: ByteArray,
    ) {
        val text: String get() = body.toString(Charsets.UTF_8)
    }

    fun request(
        method: String,
        path: String,
        body: ByteArray = ByteArray(0),
        contentType: String = "application/json",
        timeoutMs: Int = 30_000,
    ): Response {
        LocalSocket().use { ls ->
            ls.connect(LocalSocketAddress(socket.absolutePath, LocalSocketAddress.Namespace.FILESYSTEM))
            ls.soTimeout = timeoutMs
            val header = buildString {
                append(method).append(' ').append(path).append(" HTTP/1.1\r\n")
                append("Host: skydnir\r\n")
                append("Connection: close\r\n")
                if (body.isNotEmpty()) {
                    append("Content-Type: ").append(contentType).append("\r\n")
                    append("Content-Length: ").append(body.size).append("\r\n")
                }
                append("\r\n")
            }.toByteArray(Charsets.UTF_8)
            ls.outputStream.write(header)
            if (body.isNotEmpty()) ls.outputStream.write(body)
            ls.outputStream.flush()
            val raw = ls.inputStream.readBytes()
            return parse(raw)
        }
    }

    fun getArray(path: String): JSONArray {
        val resp = request("GET", path)
        require(resp.status in 200..299) { resp.text.ifBlank { "HTTP ${resp.status}" } }
        return JSONArray(resp.text.ifBlank { "[]" })
    }

    fun getObject(path: String): JSONObject {
        val resp = request("GET", path)
        require(resp.status in 200..299) { resp.text.ifBlank { "HTTP ${resp.status}" } }
        return JSONObject(resp.text.ifBlank { "{}" })
    }

    fun post(path: String): Response {
        val resp = request("POST", path)
        require(resp.status in 200..299) { resp.text.ifBlank { "HTTP ${resp.status}" } }
        return resp
    }

    fun postJson(path: String, json: JSONObject, timeoutMs: Int = 30_000): Response {
        val resp = request("POST", path, json.toString().toByteArray(Charsets.UTF_8), timeoutMs = timeoutMs)
        require(resp.status in 200..299) { resp.text.ifBlank { "HTTP ${resp.status}" } }
        return resp
    }

    fun pullImage(image: String, platform: String? = null): String {
        val platformQuery = platform
            ?.trim()
            ?.takeIf { it.isNotBlank() }
            ?.let { "&platform=${encodeQuery(it)}" }
            .orEmpty()
        val resp = request("POST", "/images/create?fromImage=${encodeQuery(image)}$platformQuery", timeoutMs = 600_000)
        require(resp.status in 200..299) { resp.text.ifBlank { "HTTP ${resp.status}" } }
        return decodeJsonStream(resp.text)
    }

    fun buildImage(contextDir: File, tag: String, dockerfile: String = "Dockerfile"): String {
        return buildImageStreaming(contextDir, tag, dockerfile) {}
    }

    fun buildImageStreaming(
        contextDir: File,
        tag: String,
        dockerfile: String = "Dockerfile",
        onLine: (String) -> Unit,
    ): String {
        val tar = createTar(contextDir)
        val path = "/build?t=${encodeQuery(tag)}&dockerfile=${encodeQuery(dockerfile)}"
        val text = requestJsonStream("POST", path, tar, "application/x-tar", timeoutMs = 900_000, onLine = onLine)
        require(!containsBuildFailure(text)) { text }
        require(containsBuildSuccess(text, tag)) { "build did not complete successfully\n$text" }
        return text
    }

    private fun requestJsonStream(
        method: String,
        path: String,
        body: ByteArray,
        contentType: String,
        timeoutMs: Int,
        onLine: (String) -> Unit,
    ): String {
        LocalSocket().use { ls ->
            ls.connect(LocalSocketAddress(socket.absolutePath, LocalSocketAddress.Namespace.FILESYSTEM))
            ls.soTimeout = timeoutMs
            val header = buildString {
                append(method).append(' ').append(path).append(" HTTP/1.1\r\n")
                append("Host: skydnir\r\n")
                append("Connection: close\r\n")
                if (body.isNotEmpty()) {
                    append("Content-Type: ").append(contentType).append("\r\n")
                    append("Content-Length: ").append(body.size).append("\r\n")
                }
                append("\r\n")
            }.toByteArray(Charsets.UTF_8)
            ls.outputStream.write(header)
            if (body.isNotEmpty()) ls.outputStream.write(body)
            ls.outputStream.flush()

            val head = readHttpHead(ls.inputStream)
            val (status, _) = parseHead(head)
            if (status !in 200..299) {
                val error = ls.inputStream.readBytes().toString(Charsets.UTF_8)
                error(error.ifBlank { "HTTP $status" })
            }

            val output = StringBuilder()
            val pending = StringBuilder()
            val buffer = ByteArray(8192)
            while (true) {
                val n = ls.inputStream.read(buffer)
                if (n < 0) break
                pending.append(String(buffer, 0, n, Charsets.UTF_8))
                consumeJsonLines(pending, output, onLine)
            }
            consumeJsonLines(pending.append('\n'), output, onLine)
            return output.toString()
        }
    }

    fun createContainer(name: String?, config: JSONObject): String {
        val path = if (name.isNullOrBlank()) "/containers/create"
        else "/containers/create?name=${encodeQuery(name)}"
        val resp = postJson(path, config, timeoutMs = 900_000)
        return JSONObject(resp.text).getString("Id")
    }

    fun logs(containerId: String, tail: Int = 200): String {
        val resp = request("GET", "/containers/${encodePath(containerId)}/logs?stdout=1&stderr=1&tail=$tail")
        require(resp.status in 200..299) { resp.text.ifBlank { "HTTP ${resp.status}" } }
        return decodeRawStream(resp.body)
    }

    fun deleteImage(image: String): String {
        val resp = request("DELETE", "/images/${encodePath(image)}", timeoutMs = 120_000)
        require(resp.status in 200..299) { resp.text.ifBlank { "HTTP ${resp.status}" } }
        return resp.text
    }

    fun pruneBuildCache(): String {
        val resp = post("/build/prune")
        return resp.text
    }

    companion object {
        fun encodePath(value: String): String =
            URLEncoder.encode(value, "UTF-8").replace("+", "%20")

        fun encodeQuery(value: String): String =
            URLEncoder.encode(value, "UTF-8").replace("+", "%20")

        fun decodeJsonStream(text: String): String =
            text.lineSequence()
                .map { it.trim() }
                .filter { it.isNotEmpty() }
                .map { line -> decodeJsonLine(line) }
                .joinToString("")

        private fun parse(raw: ByteArray): Response {
            val marker = "\r\n\r\n".toByteArray(Charsets.ISO_8859_1)
            val split = raw.indexOf(marker)
            val headBytes = if (split >= 0) raw.copyOfRange(0, split) else raw
            val body = if (split >= 0) raw.copyOfRange(split + marker.size, raw.size) else ByteArray(0)
            val (status, headers) = parseHead(headBytes)
            return Response(status, headers, body)
        }

        private fun parseHead(headBytes: ByteArray): Pair<Int, Map<String, String>> {
            val lines = headBytes.toString(Charsets.ISO_8859_1).split("\r\n")
            val status = lines.firstOrNull()?.split(" ")?.getOrNull(1)?.toIntOrNull() ?: 0
            val headers = lines.drop(1)
                .mapNotNull { line ->
                    val pos = line.indexOf(':')
                    if (pos <= 0) null else line.substring(0, pos).lowercase() to line.substring(pos + 1).trim()
                }
                .toMap()
            return status to headers
        }

        private fun readHttpHead(input: InputStream): ByteArray {
            val out = ByteArrayOutputStream()
            var state = 0
            while (true) {
                val b = input.read()
                if (b < 0) break
                out.write(b)
                if (state == 3 && b == '\n'.code) break
                state = when {
                    state == 0 && b == '\r'.code -> 1
                    state == 1 && b == '\n'.code -> 2
                    state == 2 && b == '\r'.code -> 3
                    b == '\r'.code -> 1
                    else -> 0
                }
            }
            return out.toByteArray()
        }

        private fun decodeJsonLine(line: String): String =
            runCatching {
                val obj = JSONObject(line)
                val stream = obj.optString("stream")
                if (stream.isNotEmpty()) return@runCatching stream
                val error = obj.optString("error")
                    .ifBlank { obj.optJSONObject("errorDetail")?.optString("message").orEmpty() }
                if (error.isNotEmpty()) return@runCatching "ERROR: $error\n"
                val status = obj.optString("status")
                val id = obj.optString("id")
                val progress = obj.optString("progress")
                val progressLine = listOf(id, status, progress)
                    .filter { it.isNotBlank() }
                    .joinToString(" ")
                    .trim()
                if (progressLine.isNotEmpty()) return@runCatching "\r\u001B[2K$progressLine\r"
                line
            }.getOrDefault(line)

        private fun consumeJsonLines(
            pending: StringBuilder,
            output: StringBuilder,
            onLine: (String) -> Unit,
        ) {
            while (true) {
                val next = pending.indexOf("\n")
                if (next < 0) return
                val rawLine = pending.substring(0, next).trim()
                pending.delete(0, next + 1)
                if (rawLine.isBlank()) continue
                val decoded = decodeJsonLine(rawLine)
                val segments = decoded.split('\n')
                segments.forEachIndexed { index, segment ->
                    if (segment.isEmpty()) return@forEachIndexed
                    val line = if (index < segments.lastIndex) "$segment\n" else segment
                    output.append(line)
                    onLine(line)
                }
            }
        }

        fun containsBuildFailure(text: String): Boolean =
            text.lineSequence().any { line ->
                val cleaned = cleanBuildStreamLine(line)
                cleaned.equals("build failed", ignoreCase = true) ||
                    cleaned.startsWith("ERROR:", ignoreCase = true) ||
                    cleaned.contains("ERROR: build failed", ignoreCase = true)
            }

        fun containsBuildSuccess(text: String, tag: String): Boolean {
            var built = false
            var tagged = tag.isBlank()
            text.lineSequence().forEach { line ->
                val cleaned = cleanBuildStreamLine(line)
                if (cleaned.startsWith("Successfully built ", ignoreCase = true)) built = true
                if (tag.isNotBlank() && cleaned.equals("Successfully tagged $tag", ignoreCase = true)) tagged = true
            }
            return built && tagged
        }

        private fun cleanBuildStreamLine(line: String): String =
            line
                .replace(Regex("""\u001B\[[0-9;?]*[ -/]*[@-~]"""), "")
                .replace("\r", "")
                .trim()

        private fun ByteArray.indexOf(needle: ByteArray): Int {
            if (needle.isEmpty() || size < needle.size) return -1
            for (i in 0..(size - needle.size)) {
                var ok = true
                for (j in needle.indices) {
                    if (this[i + j] != needle[j]) {
                        ok = false
                        break
                    }
                }
                if (ok) return i
            }
            return -1
        }

        private fun decodeRawStream(body: ByteArray): String {
            val out = StringBuilder()
            var i = 0
            while (i + 8 <= body.size) {
                val length = ((body[i + 4].toInt() and 0xff) shl 24) or
                    ((body[i + 5].toInt() and 0xff) shl 16) or
                    ((body[i + 6].toInt() and 0xff) shl 8) or
                    (body[i + 7].toInt() and 0xff)
                i += 8
                if (length < 0 || i + length > body.size) break
                out.append(body.copyOfRange(i, i + length).toString(Charsets.UTF_8))
                i += length
            }
            if (out.isEmpty() && body.isNotEmpty()) out.append(body.toString(Charsets.UTF_8))
            return out.toString()
        }

        private fun createTar(root: File): ByteArray {
            val out = ByteArrayOutputStream()
            val ignore = DockerIgnore.load(root)
            val rootPath = root.toPath()
            root.listFiles()
                ?.sortedBy { it.name }
                ?.forEach { file ->
                    addTarPath(out, rootPath, file, ignore)
                }
            out.write(ByteArray(1024))
            return out.toByteArray()
        }

        private fun addTarPath(
            out: ByteArrayOutputStream,
            rootPath: Path,
            file: File,
            ignore: DockerIgnore,
        ) {
            val path = file.toPath()
            val rel = rootPath.relativize(path).joinToString("/").replace('\\', '/')
            if (rel.isBlank()) return

            val isSymlink = Files.isSymbolicLink(path)
            val isDir = !isSymlink && file.isDirectory
            if (ignore.excludes(rel, isDir = isDir)) return

            val mtime = linkAwareMtime(path, file)
            val mode = tarMode(path, file, isDir = isDir, isSymlink = isSymlink)
            when {
                isSymlink -> {
                    val target = Files.readSymbolicLink(path).toString().replace('\\', '/')
                    writeTarEntry(
                        out = out,
                        name = rel,
                        data = ByteArray(0),
                        mtime = mtime,
                        mode = mode,
                        typeFlag = '2',
                        linkName = target,
                    )
                }
                isDir -> {
                    writeTarEntry(
                        out = out,
                        name = rel.trimEnd('/') + "/",
                        data = ByteArray(0),
                        mtime = mtime,
                        mode = mode,
                        typeFlag = '5',
                    )
                    file.listFiles()
                        ?.sortedBy { it.name }
                        ?.forEach { child -> addTarPath(out, rootPath, child, ignore) }
                }
                file.isFile -> {
                    writeTarEntry(
                        out = out,
                        name = rel,
                        data = file.readBytes(),
                        mtime = mtime,
                        mode = mode,
                        typeFlag = '0',
                    )
                }
            }
        }

        private fun linkAwareMtime(path: Path, file: File): Long =
            runCatching {
                Files.getLastModifiedTime(path, LinkOption.NOFOLLOW_LINKS).toMillis() / 1000
            }.getOrDefault(file.lastModified() / 1000)

        private fun tarMode(path: Path, file: File, isDir: Boolean, isSymlink: Boolean): Int {
            val unixMode = runCatching {
                (Files.getAttribute(path, "unix:mode", LinkOption.NOFOLLOW_LINKS) as Number).toInt() and 0xfff
            }.getOrNull()
            if (unixMode != null) return unixMode
            return when {
                isSymlink -> 511 // 0777
                isDir -> 493 // 0755
                file.canExecute() -> 493 // 0755
                else -> 420 // 0644
            }
        }

        private data class DockerIgnore(val rules: List<Rule>) {
            data class Rule(val pattern: String, val directoryOnly: Boolean, val basenameOnly: Boolean, val negated: Boolean)

            fun excludes(root: File, file: File, isDir: Boolean): Boolean {
                val rel = root.toPath().relativize(file.toPath()).joinToString("/")
                return excludes(rel, isDir)
            }

            fun excludes(rel: String, isDir: Boolean): Boolean {
                val normalized = rel.trim('/').replace('\\', '/')
                if (normalized.isEmpty()) return false
                var excluded = false
                rules.forEach { rule ->
                    if (rule.matches(normalized, isDir)) excluded = !rule.negated
                }
                return excluded
            }

            private fun Rule.matches(rel: String, isDir: Boolean): Boolean {
                if (directoryOnly && !isDir && !rel.startsWith("$pattern/")) return false
                val target = if (basenameOnly) rel.substringAfterLast('/') else rel
                if (glob(pattern, target)) return true
                return !basenameOnly && rel.startsWith("$pattern/")
            }

            private fun glob(pattern: String, value: String): Boolean {
                val regex = buildString {
                    append('^')
                    pattern.forEach { ch ->
                        when (ch) {
                            '*' -> append("[^/]*")
                            '?' -> append("[^/]")
                            '.', '(', ')', '+', '$', '^', '[', ']', '{', '}', '|', '\\' -> append('\\').append(ch)
                            else -> append(ch)
                        }
                    }
                    append('$')
                }.toRegex()
                return regex.matches(value)
            }

            companion object {
                fun load(root: File): DockerIgnore {
                    val file = File(root, ".dockerignore")
                    if (!file.isFile) return DockerIgnore(emptyList())
                    val rules = file.readLines()
                        .map { it.trim() }
                        .filter { it.isNotBlank() && !it.startsWith("#") }
                        .mapNotNull { raw ->
                            val negated = raw.startsWith("!")
                            val body = (if (negated) raw.drop(1) else raw)
                                .trim()
                                .trimStart('/')
                            if (body.isBlank()) return@mapNotNull null
                            val directoryOnly = body.endsWith("/")
                            val pattern = body.trimEnd('/').replace('\\', '/')
                            Rule(
                                pattern = pattern,
                                directoryOnly = directoryOnly,
                                basenameOnly = "/" !in pattern,
                                negated = negated,
                            )
                        }
                    return DockerIgnore(rules)
                }
            }
        }

        private fun writeTarEntry(
            out: ByteArrayOutputStream,
            name: String,
            data: ByteArray,
            mtime: Long,
            mode: Int,
            typeFlag: Char,
            linkName: String = "",
        ) {
            writePaxHeaderIfNeeded(out, name, linkName, mtime)
            writeTarHeader(out, name, data.size.toLong(), mtime, mode, typeFlag, linkName)
            if (data.isNotEmpty()) out.write(data)
            val pad = (512 - (data.size % 512)) % 512
            if (pad > 0) out.write(ByteArray(pad))
        }

        private fun writePaxHeaderIfNeeded(
            out: ByteArrayOutputStream,
            name: String,
            linkName: String,
            mtime: Long,
        ) {
            val records = linkedMapOf<String, String>()
            if (splitUstarName(name) == null) records["path"] = name
            if (linkName.toByteArray(Charsets.UTF_8).size > 100) records["linkpath"] = linkName
            if (records.isEmpty()) return

            val body = records.entries
                .joinToString(separator = "") { (key, value) -> paxRecord(key, value) }
                .toByteArray(Charsets.UTF_8)
            val safeName = trimUtf8ToMax("PaxHeaders/${name.substringAfterLast('/').ifBlank { "path" }}", 100)
            writeTarHeader(out, safeName, body.size.toLong(), mtime, 420, 'x', "")
            out.write(body)
            val pad = (512 - (body.size % 512)) % 512
            if (pad > 0) out.write(ByteArray(pad))
        }

        private fun paxRecord(key: String, value: String): String {
            var length = 0
            while (true) {
                val record = "$length $key=$value\n"
                val actual = record.toByteArray(Charsets.UTF_8).size
                if (actual == length) return record
                length = actual
            }
        }

        private fun writeTarHeader(
            out: ByteArrayOutputStream,
            name: String,
            size: Long,
            mtime: Long,
            mode: Int,
            typeFlag: Char,
            linkName: String,
        ) {
            val header = ByteArray(512)
            val (entryName, prefix) = splitUstarName(name)
                ?: (trimUtf8ToMax(name.substringAfterLast('/').ifBlank { "pax-path" }, 100) to "")
            putString(header, 0, 100, entryName)
            putOctal(header, 100, 8, mode.toLong())
            putOctal(header, 108, 8, 0)
            putOctal(header, 116, 8, 0)
            putOctal(header, 124, 12, size)
            putOctal(header, 136, 12, mtime)
            for (i in 148 until 156) header[i] = 0x20
            header[156] = typeFlag.code.toByte()
            if (linkName.isNotEmpty()) putString(header, 157, 100, trimUtf8ToMax(linkName, 100))
            putString(header, 257, 6, "ustar")
            putString(header, 263, 2, "00")
            if (prefix.isNotEmpty()) putString(header, 345, 155, prefix)
            val sum = header.sumOf { it.toInt() and 0xff }
            val chk = String.format(Locale.US, "%06o\u0000 ", sum).toByteArray(Charsets.US_ASCII)
            chk.copyInto(header, 148, 0, chk.size.coerceAtMost(8))
            out.write(header)
        }

        private fun splitUstarName(name: String): Pair<String, String>? {
            if (name.toByteArray(Charsets.UTF_8).size <= 100) return name to ""
            name.indices
                .filter { name[it] == '/' }
                .asReversed()
                .forEach { slash ->
                    val prefix = name.substring(0, slash)
                    val entryName = name.substring(slash + 1)
                    if (
                        prefix.toByteArray(Charsets.UTF_8).size <= 155 &&
                        entryName.toByteArray(Charsets.UTF_8).size <= 100
                    ) {
                        return entryName to prefix
                    }
                }
            return null
        }

        private fun trimUtf8ToMax(value: String, maxBytes: Int): String {
            val out = ByteArrayOutputStream()
            for (ch in value) {
                val bytes = ch.toString().toByteArray(Charsets.UTF_8)
                if (out.size() + bytes.size > maxBytes) break
                out.write(bytes)
            }
            return out.toByteArray().toString(Charsets.UTF_8)
        }

        private fun putString(buf: ByteArray, offset: Int, length: Int, value: String) {
            val bytes = value.toByteArray(Charsets.UTF_8)
            bytes.copyInto(buf, offset, 0, bytes.size.coerceAtMost(length))
        }

        private fun putOctal(buf: ByteArray, offset: Int, length: Int, value: Long) {
            val s = String.format(Locale.US, "%0${length - 1}o\u0000", value)
            putString(buf, offset, length, s)
        }
    }
}
