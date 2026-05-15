package io.github.ryo100794.pdocker

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.net.URLConnection
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

class SafDocumentsMediator(
    private val context: Context,
    treeUriText: String,
    private val mirrorRoot: File,
    private val sidecarRoot: File,
) {
    data class Entry(
        val relativePath: String,
        val displayName: String,
        val mimeType: String,
        val size: Long,
        val modifiedAt: Long,
        val directory: Boolean,
    )

    private val resolver: ContentResolver = context.contentResolver
    private val treeUri: Uri? = treeUriText.takeIf { it.isNotBlank() }?.let { Uri.parse(it) }
    private val treeDocumentId: String? = treeUri?.let {
        runCatching { DocumentsContract.getTreeDocumentId(it) }.getOrNull()
    }

    companion object {
        private val unsafeRelativePathExamples = listOf(
            "../escape",
            "/absolute",
            "nested/../../escape",
            "nested\\escape",
            "nested//escape",
        )

        fun normalizeRelativePathOrThrow(path: String, allowRoot: Boolean = false): String {
            if (path.indexOf('\u0000') >= 0) {
                throw IllegalArgumentException("Invalid SAF/Documents relative path: NUL byte is not allowed")
            }
            if (path.indexOf('\\') >= 0) {
                throw IllegalArgumentException(
                    "Invalid SAF/Documents relative path: backslash separators are not allowed; " +
                        "rejected examples=$unsafeRelativePathExamples",
                )
            }
            val raw = path
            if (raw.isBlank()) {
                if (allowRoot) return ""
                throw IllegalArgumentException("Invalid SAF/Documents relative path: empty path is not allowed")
            }
            if (raw.startsWith('/')) {
                throw IllegalArgumentException("Invalid SAF/Documents relative path: absolute paths are not allowed")
            }
            if (raw.contains("//")) {
                throw IllegalArgumentException("Invalid SAF/Documents relative path: empty path segments are not allowed")
            }
            val parts = raw.split('/')
            parts.forEach { part ->
                if (part.isBlank() || part == "." || part == "..") {
                    throw IllegalArgumentException(
                        "Invalid SAF/Documents relative path: traversal and dot segments are not allowed; " +
                            "rejected examples=$unsafeRelativePathExamples",
                    )
                }
            }
            return parts.joinToString("/")
        }
    }

    data class GrantState(
        val hasTreeUri: Boolean,
        val hasTreeDocumentId: Boolean,
        val read: Boolean,
        val write: Boolean,
    ) {
        val available: Boolean
            get() = hasTreeUri && hasTreeDocumentId && read && write
    }

    private data class CopyResult(
        val success: Boolean,
        val bytes: Long = 0L,
        val error: String = "",
        val evicted: Boolean = false,
    )

    fun persistedGrantState(): GrantState {
        val grant = treeUri?.let { uri ->
            context.contentResolver.persistedUriPermissions.firstOrNull { it.uri == uri }
        }
        return GrantState(
            hasTreeUri = treeUri != null,
            hasTreeDocumentId = treeDocumentId != null,
            read = grant?.isReadPermission == true,
            write = grant?.isWritePermission == true,
        )
    }

    fun available(): Boolean =
        persistedGrantState().available

    fun initializeContract() {
        mirrorRoot.mkdirs()
        sidecarRoot.mkdirs()
        if (!available()) return
        ensureDirectory("pdocker")
        ensureDirectory("pdocker/projects")
        ensureDirectory("pdocker/volumes")
        ensureDirectory("pdocker/shared")
        writeSidecar(
            "contract.json",
            JSONObject()
                .put("kind", "pdocker-saf-documents-mediator")
                .put("treeUri", treeUri.toString())
                .put("mirrorRoot", mirrorRoot.absolutePath)
                .put("supports", JSONArray(listOf("mkdir", "list", "exists", "read", "write")))
                .put("unixMetadata", "app-private-sidecar-minimal")
                .toString(2) + "\n",
        )
    }

    fun statusJson(): JSONObject =
        JSONObject().also { json ->
            val grants = persistedGrantState()
            json
            .put("Available", grants.available)
            .put("Success", grants.available)
            .put("HasTreeUri", grants.hasTreeUri)
            .put("HasTreeDocumentId", grants.hasTreeDocumentId)
            .put("PersistedReadGrant", grants.read)
            .put("PersistedWriteGrant", grants.write)
            .put("TreeUri", treeUri?.toString().orEmpty())
            .put("MirrorRoot", mirrorRoot.absolutePath)
            .put("SourceExists", mirrorRoot.exists())
            .put("SidecarRoot", sidecarRoot.absolutePath)
            .put("MirrorFiles", countFiles(mirrorRoot))
            .put("MirrorBytes", countBytes(mirrorRoot))
            .put("TreeTopLevel", JSONArray(list().map { it.relativePath }))
            .put("Mode", "saf-mediated-mirror")
            .put("Access", if (grants.available) "read-write" else "missing-persisted-grant")
            .put("ConflictPolicy", "phase1-no-conflict-detection")
            .put("PosixBindMount", false)
        }

    fun syncToTree(evictMirrorPayload: Boolean = false): JSONObject {
        initializeContract()
        val sourceExists = mirrorRoot.exists()
        val sourceFiles = countFiles(mirrorRoot)
        val sourceBytes = countBytes(mirrorRoot)
        val errors = JSONArray()
        val evicted = mutableListOf<String>()
        if (!available()) {
            errors.put(errorJson("", "SAF tree is not available; persisted read/write grant is missing"))
            return syncReport(
                direction = "sync-to-tree",
                available = false,
                sourceExists = sourceExists,
                sourceFiles = sourceFiles,
                sourceBytes = sourceBytes,
                errors = errors,
            )
        }
        var files = 0
        var dirs = 0
        var bytes = 0L
        if (mirrorRoot.isDirectory) {
            mirrorRoot.walkTopDown().forEach { file ->
                val relative = file.relativeTo(mirrorRoot).invariantSeparatorsPath
                if (relative.isBlank()) return@forEach
                if (file.isDirectory) {
                    runCatching { ensureDirectory(relative) }.fold(
                        onSuccess = { uri ->
                            if (uri != null) {
                                recordDirectorySidecar(relative, file)
                                dirs += 1
                            } else {
                                errors.put(errorJson(relative, "Failed to create directory"))
                            }
                        },
                        onFailure = { errors.put(errorJson(relative, it.message ?: it.toString())) },
                    )
                } else if (file.isFile) {
                    val result = copyFileToTree(relative, file, mimeTypeFor(file.name), evictMirrorPayload)
                    if (result.success) {
                        files += 1
                        bytes += result.bytes
                        if (result.evicted) evicted += relative
                    } else {
                        errors.put(errorJson(relative, result.error.ifBlank { "Failed to write file" }))
                    }
                }
            }
        }
        return syncReport(
            direction = "sync-to-tree",
            files = files,
            dirs = dirs,
            bytes = bytes,
            available = true,
            sourceExists = sourceExists,
            sourceFiles = sourceFiles,
            sourceBytes = sourceBytes,
            evictedFiles = evicted.size,
            errors = errors,
        )
    }

    fun syncPathToTree(relativePath: String, evictMirrorPayload: Boolean = false): JSONObject {
        initializeContract()
        val errors = JSONArray()
        val normalized = runCatching { normalizeRelativePath(relativePath, allowRoot = true) }.getOrElse {
            errors.put(errorJson(relativePath, it.message ?: it.toString()))
            return syncReport(
                direction = "sync-path-to-tree",
                available = available(),
                sourceExists = mirrorRoot.exists(),
                sourceFiles = countFiles(mirrorRoot),
                sourceBytes = countBytes(mirrorRoot),
                errors = errors,
            )
        }
        if (!available()) {
            errors.put(errorJson(normalized, "SAF tree is not available; persisted read/write grant is missing"))
            return syncReport(
                direction = "sync-path-to-tree",
                available = false,
                sourceExists = mirrorRoot.exists(),
                sourceFiles = countFiles(mirrorRoot),
                sourceBytes = countBytes(mirrorRoot),
                errors = errors,
            )
        }
        if (normalized.isBlank()) return syncToTree()
        val source = File(mirrorRoot, normalized)
        if (!source.exists()) {
            errors.put(errorJson(normalized, "Mirror path does not exist"))
            return syncReport(
                direction = "sync-path-to-tree",
                available = true,
                sourceExists = false,
                sourceFiles = 0,
                sourceBytes = 0L,
                errors = errors,
            )
        }
        if (source.isDirectory) {
            val uri = runCatching { ensureDirectory(normalized) }.getOrNull()
            if (uri != null) recordDirectorySidecar(normalized, source)
            if (uri == null) errors.put(errorJson(normalized, "Failed to create directory"))
            var files = 0
            var dirs = if (uri != null) 1 else 0
            var bytes = 0L
            var evicted = 0
            source.walkTopDown().forEach { child ->
                if (child == source) return@forEach
                val childRelative = child.relativeTo(mirrorRoot).invariantSeparatorsPath
                if (child.isDirectory) {
                    runCatching { ensureDirectory(childRelative) }.fold(
                        onSuccess = { childUri ->
                            if (childUri != null) {
                                recordDirectorySidecar(childRelative, child)
                                dirs += 1
                            } else {
                                errors.put(errorJson(childRelative, "Failed to create directory"))
                            }
                        },
                        onFailure = { errors.put(errorJson(childRelative, it.message ?: it.toString())) },
                    )
                } else if (child.isFile) {
                    val result = copyFileToTree(childRelative, child, mimeTypeFor(child.name), evictMirrorPayload)
                    if (result.success) {
                        files += 1
                        bytes += result.bytes
                        if (result.evicted) evicted += 1
                    } else {
                        errors.put(errorJson(childRelative, result.error.ifBlank { "Failed to write file" }))
                    }
                }
            }
            return syncReport(
                direction = "sync-path-to-tree",
                files = files,
                dirs = dirs,
                bytes = bytes,
                available = true,
                sourceExists = true,
                sourceFiles = countFiles(source),
                sourceBytes = countBytes(source),
                evictedFiles = evicted,
                errors = errors,
            )
        }
        val result = copyFileToTree(normalized, source, mimeTypeFor(source.name), evictMirrorPayload)
        if (!result.success) {
            errors.put(errorJson(normalized, result.error.ifBlank { "Failed to write file" }))
        }
        return syncReport(
            direction = "sync-path-to-tree",
            files = if (result.success) 1 else 0,
            bytes = result.bytes,
            available = true,
            sourceExists = true,
            sourceFiles = 1,
            sourceBytes = result.bytes.takeIf { result.success } ?: source.length(),
            evictedFiles = if (result.evicted) 1 else 0,
            errors = errors,
        )
    }

    fun syncFromTree(): JSONObject {
        initializeContract()
        val sourceExists = available()
        val errors = JSONArray()
        if (!available()) {
            errors.put(errorJson("", "SAF tree is not available; persisted read/write grant is missing"))
            return syncReport(
                direction = "sync-from-tree",
                available = false,
                sourceExists = false,
                sourceFiles = 0,
                sourceBytes = 0L,
                errors = errors,
            )
        }
        var sourceFiles = 0
        var sourceBytes = 0L
        var files = 0
        var dirs = 0
        var bytes = 0L
        fun copyChildren(relativePath: String) {
            list(relativePath).forEach { entry ->
                val target = File(mirrorRoot, entry.relativePath)
                if (entry.directory) {
                    target.mkdirs()
                    dirs += 1
                    copyChildren(entry.relativePath)
                } else {
                    sourceFiles += 1
                    if (entry.size >= 0L) sourceBytes += entry.size
                    val result = copyFileFromTree(entry.relativePath, target, entry.mimeType)
                    if (result.success) {
                        files += 1
                        bytes += result.bytes
                        if (entry.size < 0L) sourceBytes += result.bytes
                    } else {
                        errors.put(errorJson(entry.relativePath, result.error.ifBlank { "Failed to read file" }))
                    }
                }
            }
        }
        copyChildren("")
        return syncReport(
            direction = "sync-from-tree",
            files = files,
            dirs = dirs,
            bytes = bytes,
            available = true,
            sourceExists = sourceExists,
            sourceFiles = sourceFiles,
            sourceBytes = sourceBytes,
            errors = errors,
        )
    }

    fun exists(relativePath: String): Boolean =
        available() && runCatching { resolveDocumentUri(relativePath, createDirs = false) != null }.getOrDefault(false)

    fun list(relativePath: String = ""): List<Entry> {
        if (!available()) return emptyList()
        val parentUri = resolveDocumentUri(relativePath, createDirs = false) ?: return emptyList()
        val childUri = DocumentsContract.buildChildDocumentsUriUsingTree(
            requireTreeUri(),
            DocumentsContract.getDocumentId(parentUri),
        )
        val parent = normalizeRelativePath(relativePath)
        val entries = mutableListOf<Entry>()
        query(
            childUri,
            arrayOf(
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_MIME_TYPE,
                DocumentsContract.Document.COLUMN_SIZE,
                DocumentsContract.Document.COLUMN_LAST_MODIFIED,
            ),
        ) { cursor ->
            val name = cursor.getStringOrEmpty(0)
            if (name.isBlank()) return@query
            val mimeType = cursor.getStringOrEmpty(1)
            val childRelative = listOf(parent, name).filter { it.isNotBlank() }.joinToString("/")
            entries += Entry(
                relativePath = childRelative,
                displayName = name,
                mimeType = mimeType,
                size = cursor.getLongOrDefault(2, -1L),
                modifiedAt = cursor.getLongOrDefault(3, 0L),
                directory = mimeType == DocumentsContract.Document.MIME_TYPE_DIR,
            )
        }
        return entries
    }

    fun ensureDirectory(relativePath: String): Uri? =
        if (available()) resolveDocumentUri(relativePath, createDirs = true, directory = true) else null

    fun readBytes(relativePath: String): ByteArray? {
        if (!available()) return null
        val uri = resolveDocumentUri(relativePath, createDirs = false) ?: return null
        return resolver.openInputStream(uri)?.use { it.readBytes() }
    }

    fun writeBytes(relativePath: String, bytes: ByteArray, mimeType: String = "application/octet-stream"): Boolean {
        if (!available()) return false
        val normalized = runCatching { normalizeRelativePathOrThrow(relativePath) }.getOrElse { return false }
        checkNoProviderConflict(normalized)?.let { return false }
        val uri = resolveDocumentUri(normalized, createDirs = true, directory = false, leafMimeType = mimeType)
            ?: return false
        return runCatching {
            resolver.openOutputStream(uri, "wt")?.use { out ->
                out.write(bytes)
                true
            } ?: error("openOutputStream returned null")
        }.onSuccess {
            recordPayloadSidecar(normalized, bytes.size.toLong(), mimeType, contentSha256 = sha256Hex(bytes))
        }.getOrElse {
            false
        }
    }

    fun writeText(relativePath: String, text: String, mimeType: String = "text/plain"): Boolean =
        writeBytes(relativePath, text.toByteArray(Charsets.UTF_8), mimeType)

    fun writeFile(relativePath: String, source: File, mimeType: String = "application/octet-stream"): JSONObject {
        val normalized = runCatching { normalizeRelativePathOrThrow(relativePath) }.getOrElse {
            return JSONObject()
                .put("Success", false)
                .put("Mode", "saf-unixfs-provider")
                .put("RelativePath", relativePath)
                .put("Bytes", 0L)
                .put("PathValidationPolicy", "fail-closed")
                .put("Error", it.message ?: it.toString())
        }
        val result = copyFileToTree(normalized, source, mimeType, evictMirrorPayload = false)
        return JSONObject()
            .put("Success", result.success)
            .put("Mode", "saf-unixfs-provider")
            .put("RelativePath", normalized)
            .put("Bytes", result.bytes)
            .put("PathValidationPolicy", "fail-closed")
            .put("Error", result.error)
    }

    fun writeMirrorFallbackFile(
        relativePath: String,
        source: File,
        mimeType: String = "application/octet-stream",
        reason: String = "",
    ): JSONObject {
        val normalized = runCatching { normalizeRelativePathOrThrow(relativePath) }.getOrElse {
            return JSONObject()
                .put("Success", false)
                .put("Mode", "saf-unixfs-mirror-fallback")
                .put("RelativePath", relativePath)
                .put("MirrorPath", "")
                .put("Bytes", 0L)
                .put("PathValidationPolicy", "fail-closed")
                .put("Fallback", false)
                .put("Error", it.message ?: it.toString())
        }
        val target = File(mirrorRoot, normalized)
        return runCatching {
            target.parentFile?.mkdirs()
            val bytes = source.inputStream().use { input ->
                target.outputStream().use { output -> input.copyCountingTo(output) }
            }
            recordPayloadSidecar(
                relativePath = normalized,
                size = bytes,
                mimeType = mimeType,
                source = target,
                payloadState = "mirror-fallback-after-saf-error",
                fallbackReason = reason.ifBlank { "primary SAF/Documents write failed" },
            )
            JSONObject()
                .put("Success", true)
                .put("Mode", "saf-unixfs-mirror-fallback")
                .put("RelativePath", normalized)
                .put("MirrorPath", target.absolutePath)
                .put("Bytes", bytes)
                .put("PathValidationPolicy", "fail-closed")
                .put("Fallback", true)
                .put("Reason", reason)
        }.getOrElse {
            JSONObject()
                .put("Success", false)
                .put("Mode", "saf-unixfs-mirror-fallback")
                .put("RelativePath", normalized)
                .put("MirrorPath", target.absolutePath)
                .put("Bytes", 0L)
                .put("PathValidationPolicy", "fail-closed")
                .put("Fallback", false)
                .put("Reason", reason)
                .put("Error", it.message ?: it.toString())
        }
    }

    private fun resolveDocumentUri(
        relativePath: String,
        createDirs: Boolean,
        directory: Boolean = false,
        leafMimeType: String = "application/octet-stream",
    ): Uri? {
        val baseTreeUri = requireTreeUri()
        val rootId = treeDocumentId ?: return null
        var currentId = rootId
        var currentUri = DocumentsContract.buildDocumentUriUsingTree(baseTreeUri, currentId)
        val parts = normalizeRelativePath(relativePath, allowRoot = true).split('/').filter { it.isNotBlank() }
        if (parts.isEmpty()) return currentUri
        parts.forEachIndexed { index, part ->
            val leaf = index == parts.lastIndex
            val child = findChild(currentId, part)
            currentUri = when {
                child != null -> child
                !createDirs -> return null
                leaf && !directory -> DocumentsContract.createDocument(resolver, currentUri, leafMimeType, part)
                    ?: return null
                else -> DocumentsContract.createDocument(
                    resolver,
                    currentUri,
                    DocumentsContract.Document.MIME_TYPE_DIR,
                    part,
                ) ?: return null
            }
            currentId = DocumentsContract.getDocumentId(currentUri)
        }
        return currentUri
    }

    private fun findChild(parentDocumentId: String, displayName: String): Uri? {
        val childUri = DocumentsContract.buildChildDocumentsUriUsingTree(requireTreeUri(), parentDocumentId)
        var found: Uri? = null
        query(
            childUri,
            arrayOf(
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            ),
        ) { cursor ->
            if (found == null && cursor.getStringOrEmpty(1) == displayName) {
                found = DocumentsContract.buildDocumentUriUsingTree(requireTreeUri(), cursor.getStringOrEmpty(0))
            }
        }
        return found
    }

    private fun query(uri: Uri, projection: Array<String>, row: (android.database.Cursor) -> Unit) {
        resolver.query(uri, projection, null, null, null)?.use { cursor ->
            while (cursor.moveToNext()) row(cursor)
        }
    }

    private fun requireTreeUri(): Uri =
        treeUri ?: error("Documents SAF tree URI is not configured")

    private fun normalizeRelativePath(path: String, allowRoot: Boolean = true): String =
        normalizeRelativePathOrThrow(path, allowRoot = allowRoot)

    private fun recordPayloadSidecar(
        relativePath: String,
        size: Long,
        mimeType: String,
        source: File? = null,
        payloadState: String = "mirror-present",
        mode: String? = null,
        modifiedAt: Long? = null,
        fallbackReason: String? = null,
        contentSha256: String? = source?.takeIf { it.isFile }?.let { sha256Hex(it) },
    ) {
        val normalized = normalizeRelativePath(relativePath, allowRoot = false)
        val sidecarName = sidecarNameForNormalized(normalized)
        val directSafPublished = payloadState != "mirror-fallback-after-saf-error"
        val payloadLocation = if (directSafPublished) "saf-tree" else "app-private-mirror"
        val providerEvidence = if (directSafPublished) {
            providerEvidenceJson(normalized, includeHash = contentSha256 != null)
        } else {
            JSONObject().put("exists", false)
        }
        val json = JSONObject()
            .put("schemaVersion", 2)
            .put("relativePath", normalized)
            .put("size", size)
            .put("mimeType", mimeType)
            .put("type", "file")
            .put("mode", mode ?: source?.let { unixMode(it) } ?: "100644")
            .put("modifiedAt", modifiedAt ?: source?.lastModified() ?: 0L)
            .put("unixMetadata", "sidecar")
            .put("payloadState", payloadState)
            .put("payloadLocation", payloadLocation)
            .put("directSafPublished", directSafPublished)
            .put("mirrorPath", File(mirrorRoot, normalized).absolutePath)
            .put("pathValidationPolicy", "fail-closed")
            .put("conflictState", "clean")
            .put("providerEvidence", providerEvidence)
            .put("lastVerifiedAt", System.currentTimeMillis())
        contentSha256?.let { json.put("sha256", it) }
        if (!directSafPublished) {
            json.put("fallbackRecorded", true)
            json.put("fallbackReason", fallbackReason.orEmpty())
        }
        writeSidecar(
            sidecarName,
            json.toString(2) + "\n",
        )
    }

    private fun recordDirectorySidecar(relativePath: String, source: File? = null) {
        val normalized = normalizeRelativePath(relativePath, allowRoot = false)
        val sidecarName = sidecarNameForNormalized(normalized)
        writeSidecar(
            sidecarName,
            JSONObject()
                .put("schemaVersion", 2)
                .put("relativePath", normalized)
                .put("size", 0L)
                .put("mimeType", DocumentsContract.Document.MIME_TYPE_DIR)
                .put("type", "directory")
                .put("mode", source?.let { unixMode(it) } ?: "040755")
                .put("modifiedAt", source?.lastModified() ?: 0L)
                .put("unixMetadata", "sidecar")
                .put("pathValidationPolicy", "fail-closed")
                .put("conflictState", "clean")
                .put("providerEvidence", providerEvidenceJson(normalized, includeHash = false))
                .put("lastVerifiedAt", System.currentTimeMillis())
                .toString(2) + "\n",
        )
    }

    private fun recordConflictSidecar(
        relativePath: String,
        reason: String,
        previousSidecar: JSONObject,
        providerEvidence: JSONObject,
    ) {
        val normalized = normalizeRelativePath(relativePath, allowRoot = false)
        val json = JSONObject()
            .put("schemaVersion", 2)
            .put("relativePath", normalized)
            .put("type", previousSidecar.optString("type", "file"))
            .put("unixMetadata", "sidecar")
            .put("payloadState", "conflict-quarantined")
            .put("payloadLocation", "conflict")
            .put("directSafPublished", false)
            .put("fallbackRecorded", false)
            .put("pathValidationPolicy", "fail-closed")
            .put("conflictState", "external-provider-change")
            .put("conflictReason", reason)
            .put("previousSidecar", previousSidecar)
            .put("providerEvidence", providerEvidence)
            .put("lastVerifiedAt", System.currentTimeMillis())
        writeSidecar(sidecarNameForNormalized(normalized), json.toString(2) + "\n")
    }

    private fun writeSidecar(name: String, text: String) {
        runCatching {
            sidecarRoot.mkdirs()
            File(sidecarRoot, name).writeText(text)
        }
    }

    private fun copyFileToTree(
        relativePath: String,
        source: File,
        mimeType: String,
        evictMirrorPayload: Boolean = false,
    ): CopyResult {
        val normalized = runCatching { normalizeRelativePath(relativePath, allowRoot = false) }.getOrElse {
            return CopyResult(false, error = it.message ?: it.toString())
        }
        checkNoProviderConflict(normalized)?.let { conflict ->
            return CopyResult(false, error = conflict)
        }
        return writeStream(normalized, mimeType) { output ->
            source.inputStream().use { input ->
                input.copyCountingTo(output)
            }
        }.let { result ->
            if (!result.success) return@let result
            val mode = unixMode(source)
            val modifiedAt = source.lastModified()
            val evicted = if (evictMirrorPayload && evictableMirrorPayload(relativePath)) {
                runCatching { source.delete() }.getOrDefault(false)
            } else {
                false
            }
            recordPayloadSidecar(
                relativePath = normalized,
                size = result.bytes,
                mimeType = mimeType,
                source = source.takeUnless { evicted },
                payloadState = if (evicted) "saf-synced-mirror-evicted" else "mirror-present",
                mode = mode,
                modifiedAt = modifiedAt,
            )
            result.copy(evicted = evicted)
        }
    }

    private fun copyFileFromTree(relativePath: String, target: File, mimeType: String): CopyResult {
        if (!available()) return CopyResult(false, error = "SAF tree is not available")
        val uri = runCatching {
            resolveDocumentUri(relativePath, createDirs = false)
        }.getOrElse {
            return CopyResult(false, error = it.message ?: it.toString())
        } ?: return CopyResult(false, error = "Failed to resolve document")
        return runCatching {
            target.parentFile?.mkdirs()
            resolver.openInputStream(uri)?.use { input ->
                target.outputStream().use { output ->
                    input.copyCountingTo(output)
                }
            } ?: error("openInputStream returned null")
        }.fold(
            onSuccess = {
                recordPayloadSidecar(relativePath, it, mimeType)
                CopyResult(true, bytes = it)
            },
            onFailure = { CopyResult(false, error = it.message ?: it.toString()) },
        )
    }

    private fun writeStream(
        relativePath: String,
        mimeType: String,
        writer: (OutputStream) -> Long,
    ): CopyResult {
        if (!available()) return CopyResult(false, error = "SAF tree is not available")
        val normalized = runCatching { normalizeRelativePath(relativePath, allowRoot = false) }.getOrElse {
            return CopyResult(false, error = it.message ?: it.toString())
        }
        val uri = runCatching {
            resolveDocumentUri(normalized, createDirs = true, directory = false, leafMimeType = mimeType)
        }.getOrElse {
            return CopyResult(false, error = it.message ?: it.toString())
        } ?: return CopyResult(false, error = "Failed to resolve or create document")
        return runCatching {
            resolver.openOutputStream(uri, "wt")?.use { output ->
                writer(output)
            } ?: error("openOutputStream returned null")
        }.fold(
            onSuccess = { CopyResult(true, bytes = it) },
            onFailure = { CopyResult(false, error = it.message ?: it.toString()) },
        )
    }

    private fun evictableMirrorPayload(relativePath: String): Boolean {
        val normalized = normalizeRelativePath(relativePath, allowRoot = false)
        return normalized == "pdocker-exports" || normalized.startsWith("pdocker-exports/")
    }

    private fun checkNoProviderConflict(normalizedRelativePath: String): String? {
        val sidecar = readSidecar(normalizedRelativePath) ?: return null
        val existingConflict = sidecar.optString("conflictState")
        if (existingConflict.isNotBlank() && existingConflict != "clean") {
            return "SAF/Documents conflict detected: $existingConflict; refusing to overwrite without repair"
        }
        if (!sidecar.optBoolean("directSafPublished", false)) return null
        val expectedSha = sidecar.optString("sha256", "")
        val providerEvidence = providerEvidenceJson(normalizedRelativePath, includeHash = expectedSha.isNotBlank())
        val reason = when {
            !providerEvidence.optBoolean("exists", false) -> "provider-payload-missing"
            expectedSha.isNotBlank() &&
                providerEvidence.optString("sha256", "").isNotBlank() &&
                providerEvidence.optString("sha256") != expectedSha -> "provider-payload-hash-changed"
            expectedSha.isBlank() &&
                sidecar.has("size") &&
                providerEvidence.has("size") &&
                providerEvidence.optLong("size", -1L) != sidecar.optLong("size", -1L) -> "provider-payload-size-changed"
            else -> ""
        }
        if (reason.isBlank()) return null
        recordConflictSidecar(normalizedRelativePath, reason, sidecar, providerEvidence)
        return "SAF/Documents conflict detected: $reason; refusing to overwrite without repair"
    }

    private fun providerEvidenceJson(relativePath: String, includeHash: Boolean): JSONObject {
        val normalized = normalizeRelativePath(relativePath, allowRoot = false)
        val uri = runCatching { resolveDocumentUri(normalized, createDirs = false) }.getOrNull()
            ?: return JSONObject()
                .put("exists", false)
                .put("relativePath", normalized)
        val json = JSONObject()
            .put("exists", true)
            .put("relativePath", normalized)
            .put("documentId", runCatching { DocumentsContract.getDocumentId(uri) }.getOrDefault(""))
        query(
            uri,
            arrayOf(
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_MIME_TYPE,
                DocumentsContract.Document.COLUMN_SIZE,
                DocumentsContract.Document.COLUMN_LAST_MODIFIED,
            ),
        ) { cursor ->
            json
                .put("displayName", cursor.getStringOrEmpty(0))
                .put("mimeType", cursor.getStringOrEmpty(1))
                .put("size", cursor.getLongOrDefault(2, -1L))
                .put("modifiedAt", cursor.getLongOrDefault(3, 0L))
        }
        if (includeHash) {
            runCatching {
                resolver.openInputStream(uri)?.use { input -> sha256Hex(input) }
            }.getOrNull()?.let { json.put("sha256", it) }
        }
        return json
    }

    private fun readSidecar(relativePath: String): JSONObject? =
        runCatching {
            val normalized = normalizeRelativePath(relativePath, allowRoot = false)
            val file = File(sidecarRoot, sidecarNameForNormalized(normalized))
            if (file.isFile) JSONObject(file.readText()) else null
        }.getOrNull()

    private fun sidecarNameForNormalized(normalizedRelativePath: String): String =
        normalizedRelativePath.replace('/', '_').ifBlank { "root" } + ".json"

    private fun sha256Hex(file: File): String =
        file.inputStream().use { sha256Hex(it) }

    private fun sha256Hex(bytes: ByteArray): String =
        sha256Digest().digest(bytes).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun sha256Hex(input: InputStream): String {
        val digest = sha256Digest()
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
        while (true) {
            val read = input.read(buffer)
            if (read < 0) break
            digest.update(buffer, 0, read)
        }
        return digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }

    private fun sha256Digest(): MessageDigest =
        MessageDigest.getInstance("SHA-256")

    private fun InputStream.copyCountingTo(output: OutputStream): Long {
        var total = 0L
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
        while (true) {
            val read = read(buffer)
            if (read < 0) break
            output.write(buffer, 0, read)
            total += read.toLong()
        }
        return total
    }

    private fun syncReport(
        direction: String,
        files: Int = 0,
        dirs: Int = 0,
        bytes: Long = 0L,
        available: Boolean,
        sourceExists: Boolean = mirrorRoot.exists(),
        sourceFiles: Int = countFiles(mirrorRoot),
        sourceBytes: Long = countBytes(mirrorRoot),
        evictedFiles: Int = 0,
        errors: JSONArray = JSONArray(),
    ): JSONObject =
        JSONObject()
            .put("Direction", direction)
            .put("Available", available)
            .put("Success", available && errors.length() == 0)
            .put("SourceExists", sourceExists)
            .put("SourceFiles", sourceFiles)
            .put("SourceBytes", sourceBytes)
            .put("Files", files)
            .put("Directories", dirs)
            .put("Bytes", bytes)
            .put("EvictedMirrorFiles", evictedFiles)
            .put("Errors", errors)
            .put("MirrorRoot", mirrorRoot.absolutePath)
            .put("SidecarRoot", sidecarRoot.absolutePath)
            .put("TreeUri", treeUri?.toString().orEmpty())
            .put("ConflictPolicy", "phase1-no-conflict-detection")

    private fun errorJson(relativePath: String, message: String): JSONObject =
        JSONObject()
            .put("Path", relativePath)
            .put("Error", message)

    private fun mimeTypeFor(name: String): String =
        URLConnection.guessContentTypeFromName(name) ?: "application/octet-stream"

    private fun unixMode(file: File): String {
        val type = if (file.isDirectory) "040" else "100"
        var bits = 0
        if (file.canRead()) bits = bits or 0b100_100_100
        if (file.canWrite()) bits = bits or 0b010_010_010
        if (file.canExecute()) bits = bits or 0b001_001_001
        return type + bits.toString(8).padStart(3, '0')
    }

    private fun countFiles(root: File): Int =
        if (!root.exists()) 0 else root.walkTopDown().count { it.isFile }

    private fun countBytes(root: File): Long =
        if (!root.exists()) 0L else root.walkTopDown().filter { it.isFile }.sumOf { it.length() }

    private fun android.database.Cursor.getStringOrEmpty(index: Int): String =
        if (index >= 0 && !isNull(index)) getString(index).orEmpty() else ""

    private fun android.database.Cursor.getLongOrDefault(index: Int, default: Long): Long =
        if (index >= 0 && !isNull(index)) getLong(index) else default
}
