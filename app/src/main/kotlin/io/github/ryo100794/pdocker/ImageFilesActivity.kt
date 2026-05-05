package io.github.ryo100794.pdocker

import android.content.Intent
import android.content.pm.ActivityInfo
import android.os.Bundle
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.io.File
import java.nio.file.Files
import java.nio.file.LinkOption
import java.text.DateFormat
import java.util.Date
import org.json.JSONObject

/**
 * Browser for pulled image and container rootfs trees.
 *
 * This intentionally bypasses the docker CLI. The UI is inside the same app
 * sandbox as pdockerd, so files can be inspected directly without starting a
 * temporary container or round-tripping through docker cp. Container writable
 * layers can also hand files to the editor.
 */
class ImageFilesActivity : AppCompatActivity() {
    private data class BrowserRoot(
        val label: String,
        val root: File,
        val writable: Boolean,
        val overlayTarget: File? = null,
        val mergedLower: File? = null,
        val mergedUpper: File? = null,
    )

    private data class MergedEntry(
        val name: String,
        val file: File,
        val sourceRoot: File,
    )

    private lateinit var title: TextView
    private lateinit var path: TextView
    private lateinit var body: LinearLayout

    private val imageRoot: File by lazy { File(filesDir, "pdocker/images") }
    private val containerRoot: File by lazy { File(filesDir, "pdocker/containers") }
    private val projectRoot: File by lazy { File(filesDir, "pdocker/projects") }
    private var currentRoot: File? = null
    private var currentTitle: String? = null
    private var currentDir: File? = null
    private var currentWritable = false
    private var currentOverlayTarget: File? = null
    private var currentMergedLower: File? = null
    private var currentMergedUpper: File? = null
    private var currentMergedRel = ""
    private var standaloneRoot = false
    private var availableRoots: List<BrowserRoot> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_NOSENSOR

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 32, 32, 32)
        }

        val toolbar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val back = Button(this).apply {
            text = getString(R.string.button_back)
            setOnClickListener { navigateBack() }
        }
        val refresh = Button(this).apply {
            text = getString(R.string.button_refresh)
            setOnClickListener { render() }
        }
        toolbar.addView(back)
        toolbar.addView(refresh)

        title = TextView(this).apply {
            text = getString(R.string.title_image_files)
            textSize = 20f
            setSingleLine(true)
            ellipsize = TextUtils.TruncateAt.END
        }

        path = TextView(this).apply {
            textSize = 13f
            setPadding(0, 12, 0, 12)
        }

        body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        val scroll = ScrollView(this).apply {
            addView(body)
        }

        root.addView(toolbar)
        root.addView(title)
        root.addView(HorizontalScrollView(this).apply { addView(path) })
        root.addView(scroll, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1f
        ))
        setContentView(root)

        val selectedImage = intent.getStringExtra(EXTRA_IMAGE_NAME)
            ?.takeIf { it.isNotBlank() && it.indexOf(File.separatorChar) < 0 }
        val selectedContainer = intent.getStringExtra(EXTRA_CONTAINER_ID)
            ?.takeIf { it.isNotBlank() && it.indexOf(File.separatorChar) < 0 }
        val selectedRoot = intent.getStringExtra(EXTRA_ROOT_PATH)
            ?.takeIf { it.isNotBlank() }
            ?.let { File(it) }
        val selectedRootLabel = intent.getStringExtra(EXTRA_ROOT_LABEL)
            ?.takeIf { it.isNotBlank() }
            ?: getString(R.string.title_debug_resource_files)
        val selectedRootWritable = intent.getBooleanExtra(EXTRA_ROOT_WRITABLE, false)
        when {
            selectedRoot != null -> {
                standaloneRoot = true
                availableRoots = listOfNotNull(debugBrowserRoot(selectedRoot, selectedRootLabel, selectedRootWritable))
                if (availableRoots.isNotEmpty()) {
                    selectRoot(availableRoots.first())
                }
            }
            selectedImage != null -> {
                val image = File(imageRoot, selectedImage)
                selectRoot(image.name, File(image, "rootfs"))
            }
            selectedContainer != null -> {
                val container = File(containerRoot, selectedContainer)
                availableRoots = containerBrowserRoots(container)
                if (availableRoots.isNotEmpty()) {
                    selectRoot(availableRoots.first())
                }
            }
        }

        render()
    }

    private fun render() {
        body.removeAllViews()
        val root = currentRoot
        val dir = currentDir
        when {
            root == null -> renderImages()
            dir == null -> {
                currentDir = root
                render()
            }
            currentMergedLower != null && currentMergedUpper != null ->
                renderMergedDirectory(
                    currentTitle ?: getString(R.string.title_image_files),
                    currentMergedLower ?: root,
                    currentMergedUpper ?: root,
                    currentMergedRel,
                )
            else -> renderDirectory(currentTitle ?: getString(R.string.title_image_files), root, dir)
        }
    }

    private fun renderMergedDirectory(displayTitle: String, lowerRoot: File, upperRoot: File, rel: String) {
        val lower = lowerRoot.canonicalFile
        val upper = upperRoot.canonicalFile
        val cleanRel = rel.trim('/')
        val lowerDir = File(lower, cleanRel)
        val upperDir = File(upper, cleanRel)
        if (!lowerDir.isInside(lower) || !upperDir.isInside(upper)) {
            addMessage(getString(R.string.message_outside_rootfs))
            currentMergedRel = ""
            return
        }

        title.text = displayTitle
        path.text = "/" + cleanRel
        renderRootSwitcher()

        if (cleanRel.isNotBlank()) {
            addRow("..", getString(R.string.detail_parent_directory)) {
                currentMergedRel = cleanRel.substringBeforeLast('/', "")
                render()
            }
        }

        val entries = mergedEntries(lower, upper, cleanRel)
        if (entries.isEmpty()) {
            addMessage(getString(R.string.message_empty_directory))
            return
        }
        entries.forEach { entry ->
            addRow(entry.name, describe(entry.file)) {
                when {
                    isDirectoryEntry(entry.file) -> {
                        currentMergedRel = listOf(cleanRel, entry.name)
                            .filter { it.isNotBlank() }
                            .joinToString("/")
                        render()
                    }
                    isRegularFileEntry(entry.file) -> renderPreview(entry.sourceRoot, entry.file)
                    else -> addMessage(describe(entry.file))
                }
            }
        }
    }

    private fun renderImages() {
        title.text = getString(R.string.title_image_files)
        path.text = imageRoot.absolutePath
        val images = imageRoot.listFiles()
            ?.filter { File(it, "rootfs").isDirectory }
            ?.sortedBy { it.name }
            .orEmpty()

        if (images.isEmpty()) {
            addMessage(getString(R.string.message_no_images_refresh))
            return
        }

        images.forEach { image ->
            addRow(
                label = image.name,
                detail = summarizeDir(File(image, "rootfs")),
                onClick = {
                    selectRoot(image.name, File(image, "rootfs"))
                    render()
                }
            )
        }
    }

    private fun renderDirectory(displayTitle: String, root: File, dir: File) {
        val rootfs = root.canonicalFile
        val safeDir = runCatching { dir.canonicalFile }.getOrNull()
        if (safeDir == null || !safeDir.isInside(rootfs)) {
            addMessage(getString(R.string.message_outside_rootfs))
            currentDir = rootfs
            return
        }

        title.text = displayTitle
        path.text = "/" + rootfs.toPath().relativize(safeDir.toPath()).toString()
            .replace(File.separatorChar, '/')
            .trim('/')

        renderRootSwitcher()

        val parent = safeDir.parentFile
        if (parent != null && parent.isInside(rootfs)) {
            addRow("..", getString(R.string.detail_parent_directory)) {
                currentDir = parent
                render()
            }
        }

        val entries = safeDir.listFiles()?.sortedWith(
            compareBy<File> { !isDirectoryEntry(it) }.thenBy { it.name.lowercase() }
        ).orEmpty()
        if (entries.isEmpty()) {
            addMessage(getString(R.string.message_empty_directory))
            return
        }

        entries.forEach { entry ->
            addRow(entry.name, describe(entry)) {
                when {
                    isDirectoryEntry(entry) -> {
                        currentDir = entry
                        render()
                    }
                    isRegularFileEntry(entry) -> renderPreview(rootfs, entry)
                    else -> addMessage(describe(entry))
                }
            }
        }
    }

    private fun renderPreview(rootfs: File, file: File) {
        body.removeAllViews()
        val safeFile = runCatching { file.canonicalFile }.getOrNull()
        if (safeFile == null || !safeFile.isInside(rootfs)) {
            addMessage(getString(R.string.message_outside_rootfs))
            return
        }
        title.text = file.name
        path.text = "/" + rootfs.toPath().relativize(safeFile.toPath()).toString()
            .replace(File.separatorChar, '/')

        addRow("..", getString(R.string.detail_back_to_directory)) { render() }
        addMessage(describe(file))
        addRow(getString(R.string.action_copy_file_to_project), getString(R.string.detail_copy_file_to_project)) {
            copyFileToProject(rootfs, safeFile)
        }
        val isWritableFile = currentWritable || (currentMergedUpper
            ?.let { safeFile.isInside(it.canonicalFile) }
            ?: false)
        if (isWritableFile) {
            addRow(getString(R.string.action_edit_container_file), getString(R.string.detail_edit_container_file)) {
                openWritableFile(rootfs, safeFile)
            }
        } else {
            val overlayTarget = currentOverlayTarget
            if (overlayTarget != null) {
                addRow(getString(R.string.action_edit_container_overlay), getString(R.string.detail_edit_container_overlay)) {
                    copyToOverlayAndOpen(rootfs, safeFile, overlayTarget)
                }
            }
        }

        val maxPreview = 64 * 1024
        if (file.length() > maxPreview) {
            addMessage(getString(R.string.message_preview_too_large))
            return
        }

        val bytes = runCatching { file.readBytes() }.getOrElse {
            addMessage(getString(R.string.message_cannot_read_file, it.message.orEmpty()))
            return
        }
        if (bytes.any { it == 0.toByte() }) {
            addMessage(getString(R.string.message_binary_preview_hidden))
            return
        }
        TextView(this).apply {
            text = bytes.toString(Charsets.UTF_8)
            textSize = 13f
            setTextIsSelectable(true)
            setPadding(0, 16, 0, 16)
            body.addView(this)
        }
    }

    private fun copyFileToProject(rootfs: File, file: File) {
        val rel = rootfs.toPath().relativize(file.canonicalFile.toPath()).toString()
            .replace(File.separatorChar, '/')
            .trim('/')
            .ifBlank { file.name }
        val sourceLabel = (currentTitle ?: "rootfs")
            .replace(Regex("[^A-Za-z0-9._-]+"), "_")
            .trim('_')
            .ifBlank { "rootfs" }
        val target = File(projectRoot, "imports/$sourceLabel/$rel").canonicalFile
        val projects = projectRoot.apply { mkdirs() }.canonicalFile
        if (!target.toPath().startsWith(projects.toPath())) {
            addMessage(getString(R.string.message_outside_rootfs))
            return
        }
        runCatching {
            target.parentFile?.mkdirs()
            file.copyTo(target, overwrite = true)
        }.onFailure {
            addMessage(getString(R.string.message_cannot_copy_file, it.message.orEmpty()))
            return
        }
        addMessage(getString(R.string.message_copied_to_project_fmt, target.absolutePath))
        val bytes = runCatching { target.readBytes() }.getOrNull()
        if (target.length() <= 512 * 1024 && bytes?.any { it == 0.toByte() } == false) {
            startActivity(Intent(this, TextEditorActivity::class.java).apply {
                putExtra(TextEditorActivity.EXTRA_PATH, target.absolutePath)
            })
        }
    }

    private fun navigateBack() {
        val root = currentRoot
        val dir = currentDir
        if (root == null) {
            finish()
            return
        }
        val rootfs = root.canonicalFile
        if (dir == null || dir.canonicalFile == rootfs) {
            if (standaloneRoot) {
                finish()
                return
            }
            if (currentMergedLower != null && currentMergedRel.isNotBlank()) {
                currentMergedRel = currentMergedRel.substringBeforeLast('/', "")
                render()
                return
            }
            currentRoot = null
            currentTitle = null
            currentDir = null
        } else {
            currentDir = dir.parentFile
        }
        render()
    }

    private fun renderRootSwitcher() {
        if (availableRoots.size <= 1) return
        availableRoots.forEach { browserRoot ->
            runCatching { browserRoot.root.canonicalFile }.getOrNull() ?: return@forEach
            if (browserRoot.label == currentTitle) return@forEach
            val detail = when {
                browserRoot.mergedLower != null && browserRoot.mergedUpper != null ->
                    getString(R.string.detail_merged_container_root)
                browserRoot.writable -> getString(R.string.detail_writable_container_root)
                else -> getString(R.string.detail_readonly_container_root)
            }
            addRow(browserRoot.label, detail) {
                selectRoot(browserRoot)
                render()
            }
        }
    }

    private fun openWritableFile(rootfs: File, file: File) {
        startActivity(Intent(this, TextEditorActivity::class.java).apply {
            putExtra(TextEditorActivity.EXTRA_PATH, file.absolutePath)
            putExtra(TextEditorActivity.EXTRA_ROOT_PATH, rootfs.absolutePath)
        })
    }

    private fun copyToOverlayAndOpen(rootfs: File, file: File, overlayRoot: File) {
        val rel = rootfs.toPath().relativize(file.canonicalFile.toPath()).toString()
            .replace(File.separatorChar, '/')
            .trim('/')
        val target = File(overlayRoot, rel).canonicalFile
        val writableRoot = overlayRoot.apply { mkdirs() }.canonicalFile
        if (!target.toPath().startsWith(writableRoot.toPath())) {
            addMessage(getString(R.string.message_outside_rootfs))
            return
        }
        runCatching {
            target.parentFile?.mkdirs()
            file.copyTo(target, overwrite = true)
        }.onFailure {
            addMessage(getString(R.string.message_cannot_copy_file, it.message.orEmpty()))
            return
        }
        addMessage(getString(R.string.message_copied_to_container_overlay_fmt, target.absolutePath))
        openWritableFile(writableRoot, target)
    }

    private fun addRow(label: String, detail: String, onClick: () -> Unit) {
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            isClickable = true
            setPadding(0, 18, 0, 18)
            setOnClickListener { onClick() }
            addView(TextView(this@ImageFilesActivity).apply {
                text = label
                textSize = 16f
                setSingleLine(true)
                ellipsize = TextUtils.TruncateAt.MIDDLE
            })
            addView(TextView(this@ImageFilesActivity).apply {
                text = detail
                textSize = 12f
                alpha = 0.72f
                setSingleLine(true)
                ellipsize = TextUtils.TruncateAt.END
            })
            body.addView(this)
            addDivider()
        }
    }

    private fun addMessage(text: String) {
        TextView(this).apply {
            this.text = text
            textSize = 14f
            setPadding(0, 18, 0, 18)
            body.addView(this)
        }
    }

    private fun addDivider() {
        View(this).apply {
            alpha = 0.18f
            setBackgroundColor(0xff888888.toInt())
            body.addView(this, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                1
            ))
        }
    }

    private fun describe(file: File): String {
        if (Files.isSymbolicLink(file.toPath())) {
            val target = runCatching { Files.readSymbolicLink(file.toPath()).toString() }
                .getOrDefault(getString(R.string.detail_unreadable))
            return getString(R.string.detail_symlink_fmt, target)
        }
        val type = when {
            isDirectoryEntry(file) -> getString(R.string.detail_directory)
            isRegularFileEntry(file) -> getString(R.string.detail_file)
            else -> getString(R.string.detail_special)
        }
        val size = if (isRegularFileEntry(file)) getString(R.string.detail_size_bytes_fmt, file.length()) else ""
        val modified = DateFormat.getDateTimeInstance().format(Date(file.lastModified()))
        return getString(R.string.detail_modified_fmt, type, size, modified)
    }

    private fun summarizeDir(dir: File): String {
        val count = dir.list()?.size ?: 0
        return getString(R.string.detail_rootfs_entries_fmt, count)
    }

    private fun selectRoot(label: String, root: File) {
        if (!root.isDirectory) return
        currentRoot = root
        currentTitle = label
        currentDir = root
        currentWritable = false
        currentOverlayTarget = null
        currentMergedLower = null
        currentMergedUpper = null
        currentMergedRel = ""
    }

    private fun selectRoot(browserRoot: BrowserRoot) {
        if (!browserRoot.root.isDirectory) return
        currentRoot = browserRoot.root
        currentTitle = browserRoot.label
        currentDir = browserRoot.root
        currentWritable = browserRoot.writable
        currentOverlayTarget = browserRoot.overlayTarget
        currentMergedLower = browserRoot.mergedLower
        currentMergedUpper = browserRoot.mergedUpper
        currentMergedRel = ""
    }

    private fun containerBrowserRoots(container: File): List<BrowserRoot> {
        val title = getString(R.string.title_container_files_fmt, container.name.take(12))
        val state = runCatching { JSONObject(File(container, "state.json").readText()) }.getOrNull()
        val storage = state?.optJSONObject("Storage")
        val upper = storage?.optString("UpperDir")?.takeIf { it.isNotBlank() }?.let { File(it) }
            ?: File(container, "upper").takeIf { it.isDirectory }
        val lower = storage?.optString("LowerDir")?.takeIf { it.isNotBlank() }?.let { File(it) }
        val rootfs = storage?.optString("Rootfs")?.takeIf { it.isNotBlank() }?.let { File(it) }
            ?: File(container, "rootfs")
        val roots = mutableListOf<BrowserRoot>()
        if (lower?.isDirectory == true && upper?.isDirectory == true) {
            roots += BrowserRoot(
                getString(R.string.title_container_merged_files_fmt, container.name.take(12)),
                lower,
                writable = false,
                overlayTarget = upper,
                mergedLower = lower,
                mergedUpper = upper,
            )
        }
        if (rootfs.isDirectory) {
            roots += BrowserRoot(title, rootfs, writable = true)
        }
        if (lower?.isDirectory == true) {
            roots += BrowserRoot(
                getString(R.string.title_container_lower_files_fmt, container.name.take(12)),
                lower,
                writable = false,
                overlayTarget = upper?.takeIf { it.isDirectory },
            )
        }
        if (upper?.isDirectory == true) {
            roots += BrowserRoot(
                getString(R.string.title_container_upper_files_fmt, container.name.take(12)),
                upper,
                writable = true,
            )
        }
        return roots.distinctBy {
            "${it.label}:${runCatching { it.root.canonicalPath }.getOrDefault(it.root.absolutePath)}"
        }
    }

    private fun debugBrowserRoot(root: File, label: String, writable: Boolean): BrowserRoot? {
        val canonical = runCatching { root.canonicalFile }.getOrNull() ?: return null
        if (!canonical.isDirectory) return null
        if (!isAllowedDebugRoot(canonical)) return null
        return BrowserRoot(label, canonical, writable = writable && canonical.canWrite())
    }

    private fun isAllowedDebugRoot(root: File): Boolean {
        val allowed = listOfNotNull(
            filesDir,
            cacheDir,
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.LOLLIPOP) codeCacheDir else null,
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.LOLLIPOP) noBackupFilesDir else null,
            applicationInfo.nativeLibraryDir?.takeIf { it.isNotBlank() }?.let { File(it) },
        ).mapNotNull { runCatching { it.canonicalFile }.getOrNull() }
        return allowed.any { base -> root == base || root.isInside(base) }
    }

    private fun mergedEntries(lowerRoot: File, upperRoot: File, rel: String): List<MergedEntry> {
        val lowerDir = File(lowerRoot, rel)
        val upperDir = File(upperRoot, rel)
        val upperNames = upperDir.listFiles().orEmpty().map { it.name }.toSet()
        val hidden = upperNames
            .filter { it.startsWith(".wh.") && it != ".wh..wh..opq" }
            .map { it.removePrefix(".wh.") }
            .toSet()
        val opaque = ".wh..wh..opq" in upperNames
        val entries = mutableMapOf<String, MergedEntry>()
        if (!opaque) {
            lowerDir.listFiles().orEmpty()
                .filter { it.name !in hidden && ".wh.${it.name}" !in upperNames }
                .forEach { file ->
                    entries[file.name] = MergedEntry(file.name, file, lowerRoot)
                }
        }
        upperDir.listFiles().orEmpty()
            .filter { !it.name.startsWith(".wh.") }
            .forEach { file ->
                entries[file.name] = MergedEntry(file.name, file, upperRoot)
            }
        return entries.values.sortedWith(
            compareBy<MergedEntry> { !isDirectoryEntry(it.file) }.thenBy { it.name.lowercase() }
        )
    }

    private fun File.isInside(root: File): Boolean {
        val base = root.canonicalFile.toPath()
        val child = canonicalFile.toPath()
        return child == base || child.startsWith(base)
    }

    private fun isDirectoryEntry(file: File): Boolean =
        Files.isDirectory(file.toPath(), LinkOption.NOFOLLOW_LINKS)

    private fun isRegularFileEntry(file: File): Boolean =
        Files.isRegularFile(file.toPath(), LinkOption.NOFOLLOW_LINKS)

    companion object {
        const val EXTRA_IMAGE_NAME = "io.github.ryo100794.pdocker.extra.IMAGE_NAME"
        const val EXTRA_CONTAINER_ID = "io.github.ryo100794.pdocker.extra.CONTAINER_ID"
        const val EXTRA_ROOT_PATH = "io.github.ryo100794.pdocker.extra.ROOT_PATH"
        const val EXTRA_ROOT_LABEL = "io.github.ryo100794.pdocker.extra.ROOT_LABEL"
        const val EXTRA_ROOT_WRITABLE = "io.github.ryo100794.pdocker.extra.ROOT_WRITABLE"
    }
}
