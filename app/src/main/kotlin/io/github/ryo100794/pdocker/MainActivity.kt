package io.github.ryo100794.pdocker

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.ActivityInfo
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Shader
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.FileObserver
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Environment
import android.os.PowerManager
import android.provider.DocumentsContract
import android.provider.Settings
import android.system.Os
import android.text.Editable
import android.text.TextUtils
import android.text.TextWatcher
import android.util.Base64
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : AppCompatActivity() {
    private enum class Tab { Overview, Library, Compose, Dockerfiles, Images, Containers, Sessions, Debug }
    private enum class ToolKind { Terminal, Editor, Split }

    private data class ToolTab(
        val group: String,
        val title: String,
        val kind: ToolKind,
        val view: View,
        val bridge: Bridge? = null,
        val key: String = title,
    )

    private data class ProjectTemplate(
        val id: String,
        val name: String,
        val category: String,
        val description: String,
        val assetPath: String,
        val projectDir: String,
        val compose: String,
        val dockerfile: String,
        val gpu: String,
        val version: Int,
        val features: List<String>,
    )

    private data class TemplateInstallReport(
        var copied: Int = 0,
        var kept: Int = 0,
    )

    private data class StorageMetrics(
        val fsTotalBytes: Long,
        val fsFreeBytes: Long,
        val pdockerBytes: Long,
        val layersBytes: Long,
        val imageViewBytes: Long,
        val containerPrivateBytes: Long,
    )

    private data class DaemonOperation(
        val id: String,
        val kind: String,
        val title: String,
        val detail: String,
        val status: String,
        val startedAtMs: Long,
        val updatedAtMs: Long,
    )

    private data class DiskUsage(
        val bytes: Long,
        val inodeKeys: Set<String>,
    )

    private data class ImageReferenceInfo(
        val dir: File,
        val ref: String,
        val displayRef: String,
        val diffIds: List<String>,
        val viewBytes: Long,
        val uniqueLayerBytes: Long,
        val sharedLayerBytes: Long,
        val totalLayerBytes: Long,
    )

    private data class ImageCacheHealth(
        val layerCount: Int,
        val referencedLayerCount: Int,
        val unreferencedLayerCount: Int,
        val unreferencedLayerBytes: Long,
        val buildCacheCount: Int,
        val staleBuildCacheCount: Int,
        val tempArtifactCount: Int,
    )

    private enum class ImageGraphKind(
        val label: String,
        val color: Int,
    ) {
        Image("image", 0xff66bb6a.toInt()),
        Cache("cache", 0xffffb74d.toInt()),
        Compose("compose", 0xff42a5f5.toInt()),
        Container("container", 0xff42a5f5.toInt()),
    }

    private data class ImageReferenceGraphRow(
        val depth: Int,
        val ancestorLast: List<Boolean>,
        val isLast: Boolean,
        val title: String,
        val detail: String,
        val kind: ImageGraphKind,
        val actions: List<ImageGraphAction> = emptyList(),
    )

    private data class ImageGraphAction(
        val label: String,
        val run: () -> Unit,
    )

    private class ImageGraphLayout(context: Context) : LinearLayout(context) {
        private var graphRows: List<ImageReferenceGraphRow> = emptyList()
        private val density = resources.displayMetrics.density
        private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = 0xaa888888.toInt()
            strokeWidth = 2f * density
            strokeCap = Paint.Cap.SQUARE
        }

        init {
            setWillNotDraw(false)
        }

        fun setGraphRows(rows: List<ImageReferenceGraphRow>) {
            graphRows = rows
            invalidate()
        }

        override fun dispatchDraw(canvas: Canvas) {
            val guideStep = 18f * density
            val branchWidth = 24f * density
            val branchXOffset = 9f * density
            val horizontalEndInset = 2f * density
            graphRows.forEachIndexed { index, row ->
                val child = getChildAt(index) ?: return@forEachIndexed
                val top = child.top.toFloat()
                val bottom = child.bottom.toFloat()
                val centerY = (top + bottom) / 2f
                row.ancestorLast.forEachIndexed { level, ancestorIsLast ->
                    if (!ancestorIsLast) {
                        val x = level * guideStep + branchXOffset
                        canvas.drawLine(x, top, x, bottom, linePaint)
                    }
                }
                val x = row.depth * guideStep + branchXOffset
                canvas.drawLine(x, top, x, if (row.isLast) centerY else bottom, linePaint)
                canvas.drawLine(x, centerY, row.depth * guideStep + branchWidth - horizontalEndInset, centerY, linePaint)
            }
            super.dispatchDraw(canvas)
        }
    }

    private data class DockerJob(
        val id: String,
        val title: String,
        val detail: String,
        val command: String,
        val group: String,
        val toolKey: String,
        var status: String,
        var exitCode: Int? = null,
        var startedAt: Long = System.currentTimeMillis(),
        var endedAt: Long? = null,
        var progress: String = "",
        var output: MutableList<String> = mutableListOf(),
    )

    private data class LiveJobView(
        val header: TextView,
        val progress: TextView,
        val services: LinearLayout,
        val terminal: JobLogPane,
    )

    private interface JobLogPane {
        val view: View
        fun write(text: String)
    }

    private class TerminalLogPane(override val view: WebView) : JobLogPane {
        private val pending = StringBuilder()
        private var ready = false
        private var flushScheduled = false

        fun markReady() {
            ready = true
            scheduleFlush(0L)
        }

        override fun write(text: String) {
            if (text.isEmpty()) return
            pending.append(text)
            if (!ready) return
            scheduleFlush(8L)
        }

        private fun scheduleFlush(delayMs: Long) {
            if (flushScheduled) return
            flushScheduled = true
            view.postDelayed({
                flushScheduled = false
                if (!ready || pending.isEmpty()) return@postDelayed
                val text = pending.toString()
                pending.clear()
                flush(text)
                if (pending.isNotEmpty()) scheduleFlush(8L)
            }, delayMs)
        }

        private fun flush(text: String) {
            val b64 = Base64.encodeToString(text.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
            view.evaluateJavascript("window.pdockerRecv('$b64')", null)
        }
    }

    private inner class LightweightJobLogPane : JobLogPane {
        private val lines = ArrayDeque<String>()
        private var current = StringBuilder()
        private var renderScheduled = false
        private val textView = TextView(this@MainActivity).apply {
            textSize = 8f
            typeface = Typeface.MONOSPACE
            setTextIsSelectable(true)
            setTextColor(0xFFE5E7EB.toInt())
            setBackgroundColor(0xFF0F172A.toInt())
            setPadding(14, 12, 14, 12)
        }
        private val scroll = ScrollView(this@MainActivity).apply {
            isFillViewport = true
            setBackgroundColor(0xFF0F172A.toInt())
            addView(textView)
        }
        override val view: View = scroll

        override fun write(text: String) {
            if (text.isEmpty()) return
            text.forEach { ch ->
                when (ch) {
                    '\r' -> current = StringBuilder()
                    '\n' -> commitCurrent()
                    else -> current.append(ch)
                }
            }
            if (current.length > 4096) current = StringBuilder(current.takeLast(4096))
            scheduleRender()
        }

        private fun commitCurrent() {
            val line = cleanTerminalLine(current.toString())
            if (line.isNotBlank()) {
                lines.addLast(line)
                while (lines.size > MAX_JOB_LINES) lines.removeFirst()
            }
            current = StringBuilder()
        }

        private fun scheduleRender() {
            if (renderScheduled) return
            renderScheduled = true
            scroll.postDelayed({
                renderScheduled = false
                val tail = buildString {
                    lines.forEach { append(it).append('\n') }
                    val live = cleanTerminalLine(current.toString())
                    if (live.isNotBlank()) append(live)
                }
                textView.text = tail
                scroll.fullScroll(View.FOCUS_DOWN)
            }, 100L)
        }
    }

    private data class ComposeService(
        val name: String,
        var image: String = "",
        var containerName: String = "",
        var buildContext: String? = null,
        var workingDir: String = "",
        var command: List<String> = emptyList(),
        val environment: MutableMap<String, String> = mutableMapOf(),
        val labels: MutableMap<String, String> = mutableMapOf(),
        val ports: MutableList<String> = mutableListOf(),
        val volumes: MutableList<String> = mutableListOf(),
        val dependsOn: MutableList<String> = mutableListOf(),
        var memLimit: String = "",
        var memSwapLimit: String = "",
        var deployMemoryLimit: String = "",
        var gpus: String = "",
        var hasHealthcheck: Boolean = false,
        val serviceLinks: MutableList<ComposeServiceLink> = mutableListOf(),
    )

    private data class ComposeServiceLink(
        val port: Int?,
        val label: String,
        val url: String?,
        val autoOpen: Boolean = false,
    )

    private data class ComposePortBinding(
        val hostPort: Int,
        val containerPort: Int,
    )

    private data class ContainerSnapshotLookup(
        val byEngineId: Map<String, JSONObject>,
        val byUniqueName: Map<String, JSONObject>,
    )

    private data class ProjectSummary(
        val dir: File,
        val compose: List<File>,
        val dockerfiles: List<File>,
        val editable: List<File>,
        val services: List<ComposeService>,
        val serviceUrls: List<ProjectServiceUrl>,
        val serviceHealth: String,
        val modelSummary: String,
        val gpuProfileSummary: String,
        val gpuDiagnostics: File?,
        val containerStatusSummary: String,
        val runtimeDiagnosticSummary: String,
        val runtimeDiagnosticLog: File?,
        val jobSummary: String,
    )

    private data class ProjectRuntimeDiagnostic(
        val summary: String,
        val log: File?,
    )

    private data class ProjectServiceUrl(
        val serviceName: String,
        val label: String,
        val url: String,
    )

    private data class ServiceContainerProof(
        val serviceName: String,
        val engineContainerId: String,
    )

    private enum class DocumentsWriteAccess(val envValue: String) {
        DirectPathWritable("direct-path-writable"),
        SafMediated("saf-mediated"),
    }

    private data class PersistedDocumentsTreeMetadata(
        val treeUri: String,
        val displayName: String,
        val selectedHostPath: String,
        val directHostPath: String,
        val activeHostPath: String,
        val writeAccess: DocumentsWriteAccess,
    )

    companion object {
        private const val REQUEST_POST_NOTIFICATIONS = 100
        private const val REQUEST_DOCUMENTS_TREE = 101
        private const val REQUEST_EXTERNAL_STORAGE = 102
        private const val MAX_INLINE_EDIT_BYTES = 512 * 1024
        private const val MAX_JOB_HISTORY = 20
        private const val MAX_JOB_LINES = 200
        private const val MAX_JOB_LOG_VIEW_BYTES = 256 * 1024
        private const val MAX_TEXT_TOOL_VIEW_BYTES = 128 * 1024
        private const val MAX_UI_WALK_ENTRIES = 512
        private const val MAX_PROJECT_DASHBOARD_PROJECTS = 8
        private const val DOCUMENTS_SYNC_DEBOUNCE_MS = 1_500L
        private const val DOCUMENTS_SYNC_MIN_INTERVAL_MS = 3_000L
        private const val MAX_DOCUMENTS_MIRROR_OBSERVERS = 256
        private const val MAX_DOCUMENTS_SYNC_SCAN_ENTRIES = 512
        private const val FALLBACK_IMAGE_PLATFORM = "linux/arm64"
        private const val PDOCKER_SERVICE_URL_LABEL_PREFIX = "io.github.ryo100794.pdocker.service-url."
        private const val PDOCKER_PROJECT_ID_LABEL = "io.github.ryo100794.pdocker.project-id"
        private const val PDOCKER_PROJECT_DIR_LABEL = "io.github.ryo100794.pdocker.project-dir"
        private const val PDOCKER_PROJECT_NAME_LABEL = "io.github.ryo100794.pdocker.project-name"
        private const val PDOCKER_COMPOSE_SERVICE_LABEL = "io.github.ryo100794.pdocker.compose-service"
        private const val ACTION_SMOKE_START = "io.github.ryo100794.pdocker.action.SMOKE_START"
        private const val ACTION_SMOKE_GPU_BENCH = "io.github.ryo100794.pdocker.action.SMOKE_GPU_BENCH"
        private const val ACTION_SMOKE_COMPOSE_UP = "io.github.ryo100794.pdocker.action.SMOKE_COMPOSE_UP"
        private const val ACTION_SMOKE_DOCUMENTS_SYNC_TO_TREE = "io.github.ryo100794.pdocker.action.SMOKE_DOCUMENTS_SYNC_TO_TREE"
        private const val ACTION_SMOKE_DOCUMENTS_SYNC_FROM_TREE = "io.github.ryo100794.pdocker.action.SMOKE_DOCUMENTS_SYNC_FROM_TREE"
        private const val ACTION_SMOKE_DOCUMENTS_WRITE_FILE = "io.github.ryo100794.pdocker.action.SMOKE_DOCUMENTS_WRITE_FILE"
        private const val ACTION_SMOKE_UI_IT_SELFTEST = "io.github.ryo100794.pdocker.action.SMOKE_UI_IT_SELFTEST"
        private const val PREFS_NAME = "pdocker-settings"
        private const val PREF_DOCUMENTS_TREE_URI = "documents.treeUri"
        private const val PREF_DOCUMENTS_HOST_PATH = "documents.hostPath"
        private const val PREF_DOCUMENTS_DISPLAY_NAME = "documents.displayName"
        private const val PDOCKER_DOCUMENTS_MOUNT = "/documents"
    }

    private val ui = Handler(Looper.getMainLooper())
    private val logIo: ExecutorService = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "pdocker-job-log-writer").apply { isDaemon = true }
    }
    private val tabs = listOf(Tab.Overview, Tab.Library, Tab.Compose, Tab.Dockerfiles, Tab.Images, Tab.Containers, Tab.Sessions, Tab.Debug)
    private val pollTask = object : Runnable {
        override fun run() {
            refreshStatus()
            refreshDaemonOperationsAsync()
            if (currentTab in setOf(Tab.Overview, Tab.Containers, Tab.Compose)) {
                refreshContainerSnapshotAsync()
            }
            ui.postDelayed(this, 3000)
        }
    }
    private val jobTickerTask = object : Runnable {
        override fun run() {
            tickRunningJobs()
            ui.postDelayed(this, 1000)
        }
    }
    private val documentsMirrorScanTask = object : Runnable {
        override fun run() {
            scanDocumentsExportMirrorForChanges()
            ui.postDelayed(this, DOCUMENTS_SYNC_MIN_INTERVAL_MS)
        }
    }

    private lateinit var status: TextView
    private lateinit var content: LinearLayout
    private lateinit var tabRow: LinearLayout
    private lateinit var upperPane: LinearLayout
    private lateinit var lowerPane: LinearLayout
    private lateinit var lowerGroupRow: LinearLayout
    private lateinit var lowerTabRow: LinearLayout
    private lateinit var lowerHost: FrameLayout
    private var currentTab = Tab.Overview
    private val toolTabs = mutableListOf<ToolTab>()
    private var currentTool = -1
    private var currentToolGroup: String? = null
    private val dockerJobs = mutableListOf<DockerJob>()
    private val dockerJobBuffers = mutableMapOf<String, String>()
    private val dockerJobPendingCarriageReturn = mutableSetOf<String>()
    private val liveJobViews = mutableMapOf<String, LiveJobView>()
    private var dockerJobsSaveScheduled = false
    private var dockerJobsDirty = false
    private var jobRenderScheduled = false
    private val ansiControlRegex = Regex("\u001B\\[[0-?]*[ -/]*[@-~]")
    private val serviceHealth = mutableMapOf<String, String>()
    private val serviceHealthCheckedAt = mutableMapOf<String, Long>()
    private val serviceHealthInFlight = mutableSetOf<String>()
    private var upperWeight = 0.56f
    private var lowerWeight = 0.44f
    private var splitDragStartY = 0f
    private var splitDragStartUpper = 0f
    private var lastDaemonStartAttemptAt = 0L
    private var storageMetrics: StorageMetrics? = null
    private var storageMetricsScanning = false
    private var lastStorageMetricsAt = 0L
    private var daemonOperations: List<DaemonOperation> = emptyList()
    private var daemonOperationsRefreshing = false
    private var containerSnapshot: List<JSONObject> = emptyList()
    private var containerSnapshotFingerprint = ""
    private var containerSnapshotRefreshing = false
    private var lastContainerSnapshotAt = 0L
    private var hostEnvironment: JSONObject? = null
    private var hostEnvironmentRefreshing = false
    private var lastHostEnvironmentAt = 0L
    private var documentsProjectRootProbePath: String? = null
    private var documentsProjectRootProbeWritable: Boolean = false
    private val documentsSyncLock = Any()
    private val documentsMirrorObservers = mutableMapOf<String, FileObserver>()
    private val pendingDocumentsSyncPaths = linkedSetOf<String>()
    private val documentsMirrorScanState = mutableMapOf<String, String>()
    private var documentsSyncScheduled = false
    private var documentsSyncRunning = false
    private var lastDocumentsSyncAt = 0L
    private var pdockerdServiceBound = false
    private val pdockerdServiceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            pdockerdServiceBound = true
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            pdockerdServiceBound = false
        }
    }

    private val pdockerHome: File by lazy { File(filesDir, "pdocker") }
    private val imageRoot: File by lazy { File(pdockerHome, "images") }
    private val layerRoot: File by lazy { File(pdockerHome, "layers") }
    private val containerRoot: File by lazy { File(pdockerHome, "containers") }
    private val legacyProjectRoot: File by lazy { File(pdockerHome, "projects") }
    private val projectRoot: File
        get() = if (documentsProjectsRootWritable()) documentsProjectsRoot() else legacyProjectRoot
    private val engine: DockerEngineClient by lazy { DockerEngineClient(File(pdockerHome, "pdockerd.sock")) }
    private val diagnosticsEnabled: Boolean
        get() = BuildConfig.DEBUG ||
            (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_NOSENSOR
        migrateLegacyProjectsToDocuments()
        seedDefaultProject()
        migrateInstalledProjects()
        syncDocumentsVolumeEnv()
        startDocumentsMirrorSync()
        loadDockerJobs()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 28, 28, 28)
        }
        upperPane = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        lowerPane = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }

        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        status = TextView(this).apply {
            text = getString(R.string.status_unknown)
            textSize = 14f
            setSingleLine(true)
            ellipsize = TextUtils.TruncateAt.END
        }
        val buildInfo = TextView(this).apply {
            text = appBuildInfo()
            textSize = 11f
            gravity = Gravity.END
            setSingleLine(true)
            ellipsize = TextUtils.TruncateAt.START
            typeface = Typeface.MONOSPACE
        }
        tabRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        lowerGroupRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        lowerTabRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        lowerHost = FrameLayout(this)

        headerRow.addView(status, LinearLayout.LayoutParams(
            0,
            LinearLayout.LayoutParams.WRAP_CONTENT,
            1f,
        ))
        headerRow.addView(buildInfo, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ))
        upperPane.addView(headerRow)
        upperPane.addView(HorizontalScrollView(this).apply { addView(tabRow) })
        upperPane.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1f,
        ))
        lowerPane.addView(HorizontalScrollView(this).apply { addView(lowerGroupRow) })
        lowerPane.addView(HorizontalScrollView(this).apply { addView(lowerTabRow) })
        lowerPane.addView(lowerHost, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1f,
        ))
        root.addView(upperPane, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            upperWeight,
        ))
        root.addView(splitterView())
        root.addView(lowerPane, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            lowerWeight,
        ))
        setContentView(root)

        renderTabs()
        renderContent()
        renderToolChrome()
        ensureDaemonStarted()
        handleAutomationIntent(intent)
    }

    override fun onStart() {
        super.onStart()
        bindPdockerdService()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleAutomationIntent(intent)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQUEST_DOCUMENTS_TREE || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        val flags = data.flags and (
            Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        )
        runCatching { contentResolver.takePersistableUriPermission(uri, flags) }
        val hostPath = documentTreeHostPath(uri).orEmpty()
        val displayName = documentTreeDisplayName(uri, hostPath)
        prefs().edit()
            .putString(PREF_DOCUMENTS_TREE_URI, uri.toString())
            .putString(PREF_DOCUMENTS_HOST_PATH, hostPath)
            .putString(PREF_DOCUMENTS_DISPLAY_NAME, displayName)
            .apply()
        documentsProjectRootProbePath = null
        syncDocumentsVolumeEnv()
        startDocumentsMirrorSync()
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(
                    Manifest.permission.READ_EXTERNAL_STORAGE,
                    Manifest.permission.WRITE_EXTERNAL_STORAGE,
                ),
                REQUEST_EXTERNAL_STORAGE,
            )
        }
        status.text = getString(
            R.string.status_documents_volume_set_fmt,
            displayName,
            documentsWriteAccessLabel(documentsTreeMetadata().writeAccess),
        )
        renderContent()
    }

    override fun onResume() {
        super.onResume()
        migrateInstalledProjects()
        startDocumentsMirrorSync()
        ensureDaemonStarted()
        renderContent()
        ui.post(pollTask)
        ui.post(jobTickerTask)
        ui.post(documentsMirrorScanTask)
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(pollTask)
        ui.removeCallbacks(jobTickerTask)
        ui.removeCallbacks(documentsMirrorScanTask)
        flushPendingDocumentsSync()
        flushDockerJobsSave()
    }

    override fun onDestroy() {
        toolTabs.forEach { it.bridge?.close() }
        toolTabs.clear()
        liveJobViews.clear()
        stopDocumentsMirrorSync()
        ui.removeCallbacks(pollTask)
        ui.removeCallbacks(jobTickerTask)
        ui.removeCallbacks(documentsMirrorScanTask)
        unbindPdockerdService()
        logIo.shutdown()
        super.onDestroy()
    }

    private fun appBuildInfo(): String {
        val info = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            packageManager.getPackageInfo(packageName, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            packageManager.getPackageInfo(packageName, 0)
        }
        val versionName = info.versionName ?: "dev"
        val versionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.longVersionCode
        } else {
            @Suppress("DEPRECATION")
            info.versionCode.toLong()
        }
        val rawBuildTime = BuildConfig.BUILD_TIME_UTC
            .replace('T', ' ')
            .removeSuffix("Z")
        val buildTime = rawBuildTime.substringBeforeLast('.', rawBuildTime)
        return getString(
            R.string.app_build_info_fmt,
            versionName,
            versionCode,
            buildTime,
            BuildConfig.BUILD_GIT_COMMIT,
        )
    }

    private fun splitterView(): View =
        TextView(this).apply {
            text = "━━"
            gravity = Gravity.CENTER
            textSize = 12f
            alpha = 0.62f
            setPadding(0, 3, 0, 3)
            setBackgroundColor(0x11888888)
            setOnTouchListener { _, event ->
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        splitDragStartY = event.rawY
                        splitDragStartUpper = upperWeight
                        true
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val total = (upperPane.height + lowerPane.height).coerceAtLeast(1)
                        val delta = (event.rawY - splitDragStartY) / total
                        upperWeight = (splitDragStartUpper + delta).coerceIn(0.24f, 0.78f)
                        lowerWeight = 1f - upperWeight
                        upperPane.layoutParams = LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            0,
                            upperWeight,
                        )
                        lowerPane.layoutParams = LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            0,
                            lowerWeight,
                        )
                        true
                    }
                    else -> true
                }
            }
        }

    private fun renderTabs() {
        tabRow.removeAllViews()
        tabs.forEach { tab ->
            tabRow.addView(Button(this).apply {
                text = tabLabel(tab)
                isAllCaps = false
                alpha = if (tab == currentTab) 1f else 0.72f
                setOnClickListener {
                    currentTab = tab
                    renderTabs()
                    renderContent()
                }
            })
        }
    }

    private fun renderContent() {
        content.removeAllViews()
        when (currentTab) {
            Tab.Overview -> renderOverview()
            Tab.Library -> renderLibrary()
            Tab.Compose -> renderCompose()
            Tab.Dockerfiles -> renderDockerfiles()
            Tab.Images -> renderImages()
            Tab.Containers -> renderContainers()
            Tab.Sessions -> renderSessions()
            Tab.Debug -> renderDebugResources()
        }
    }

    private fun tabLabel(tab: Tab): String = getString(when (tab) {
        Tab.Overview -> R.string.tab_overview
        Tab.Library -> R.string.tab_library
        Tab.Compose -> R.string.tab_compose
        Tab.Dockerfiles -> R.string.tab_dockerfile
        Tab.Images -> R.string.tab_images
        Tab.Containers -> R.string.tab_containers
        Tab.Sessions -> R.string.tab_sessions
        Tab.Debug -> R.string.tab_debug
    })

    private fun renderOverview() {
        refreshContainerSnapshotAsync()
        addSection(getString(R.string.section_workspace))
        addAction(getString(R.string.action_default_dev_workspace), getString(R.string.detail_default_dev_workspace)) {
            openEditor(File(projectRoot, "default/Dockerfile"))
        }
        addAction(getString(R.string.action_set_documents_volume), documentsVolumeDetail()) {
            requestDocumentsVolumeFolder()
        }
        addAction(getString(R.string.action_documents_sync_to_tree), getString(R.string.detail_documents_sync_to_tree)) {
            runDocumentsMediatorAction(getString(R.string.action_documents_sync_to_tree)) {
                safDocumentsMediator().syncToTree()
            }
        }
        addAction(getString(R.string.action_documents_sync_from_tree), getString(R.string.detail_documents_sync_from_tree)) {
            runDocumentsMediatorAction(getString(R.string.action_documents_sync_from_tree)) {
                safDocumentsMediator().syncFromTree()
            }
        }

        addSection(getString(R.string.section_inventory))
        addWidget(getString(R.string.widget_images), imageDirs().size.toString(), getString(R.string.detail_images_inventory))
        addWidget(getString(R.string.widget_containers), containerInventoryValue(), getString(R.string.detail_containers_inventory))
        addWidget(getString(R.string.widget_compose_projects), composeFiles().size.toString(), projectRoot.absolutePath)
        addWidget(getString(R.string.widget_dockerfiles), dockerfiles().size.toString(), getString(R.string.detail_dockerfiles_inventory))
        renderStorageMetrics()
        renderHostEnvironment()
        renderMemoryLayerVisualization()
        renderDaemonOperations()
        renderProjectDashboard()
        val daemonJobIds = daemonOperations.mapNotNull { daemonOperationJob(it)?.id }.toSet()
        renderDockerJobs { it.id !in daemonJobIds }
    }

    private fun renderLibrary() {
        addSection(getString(R.string.section_project_library))
        val templates = projectTemplates()
        if (templates.isEmpty()) {
            addMessage(getString(R.string.message_library_empty))
            return
        }
        templates.forEach { template ->
            val target = File(projectRoot, template.projectDir)
            val installed = File(target, template.compose).isFile || File(target, template.dockerfile).isFile
            val detail = listOf(
                template.description,
                getString(R.string.library_features_fmt, template.features.joinToString(", ")),
                getString(R.string.library_gpu_fmt, template.gpu),
                getString(R.string.library_target_fmt, target.absolutePath),
            ).joinToString("\n")
            addWidget(
                template.name,
                if (installed) getString(R.string.library_installed) else template.category,
                detail,
            ) {
                installTemplate(template)
                openEditor(File(target, template.compose))
            }
            addAction(getString(R.string.action_install_template_fmt, template.name), getString(R.string.detail_install_template)) {
                installTemplate(template)
                renderContent()
            }
            addAction(getString(R.string.action_open_template_compose_fmt, template.name), template.compose) {
                installTemplate(template)
                openEditor(File(target, template.compose))
            }
            addAction(getString(R.string.action_open_template_dockerfile_fmt, template.name), template.dockerfile) {
                installTemplate(template)
                openEditor(File(target, template.dockerfile))
            }
            if (template.gpu == "auto") {
                addAction(getString(R.string.action_gpu_profile_fmt, template.name), getString(R.string.detail_gpu_profile)) {
                    installTemplate(template)
                    openTerminal(
                        getString(R.string.terminal_gpu_profile),
                        "cd ${shellQuote(target.absolutePath)} && LLAMA_GPU_DIAGNOSTICS=profiles/pdocker-gpu-diagnostics.json bash scripts/pdocker-gpu-profile.sh profiles/pdocker-gpu.env; printf '\\n'; cat profiles/pdocker-gpu.env; printf '\\n'; cat profiles/pdocker-gpu-diagnostics.json; sh",
                    )
                }
            }
            addAction(getString(R.string.action_compose_up_template_fmt, template.name), getString(R.string.detail_compose_up)) {
                installTemplate(template)
                runComposeUp(target, getString(R.string.terminal_compose_up_fmt, template.projectDir))
            }
        }
    }

    private fun renderCompose() {
        addSection(getString(R.string.section_compose))
        renderDockerJobs { it.command.contains("compose up") }
        addAction(getString(R.string.action_new_compose), getString(R.string.detail_new_compose)) {
            openEditor(File(projectRoot, "default/compose.yaml"))
        }
        addAction(getString(R.string.action_default_dev_compose), getString(R.string.detail_default_dev_compose)) {
            openEditor(File(projectRoot, "default/compose.yaml"))
        }
        val files = composeFiles()
        if (files.isEmpty()) {
            addMessage(getString(R.string.message_no_compose_fmt, projectRoot.absolutePath))
            return
        }
        files.forEach { file ->
            addWidget(file.name, getString(R.string.detail_compose_file), file.parentFile?.absolutePath.orEmpty()) {
                openEditor(file)
            }
            addAction(getString(R.string.action_up_fmt, file.parentFile?.name ?: file.name), getString(R.string.detail_compose_up)) {
                val dir = file.parentFile ?: projectRoot
                runComposeUp(dir, getString(R.string.terminal_compose_up_fmt, dir.name))
            }
        }
    }

    private fun renderDockerfiles() {
        addSection(getString(R.string.section_dockerfile))
        renderDockerJobs { it.command.contains("docker build") }
        addAction(getString(R.string.action_new_dockerfile), getString(R.string.detail_new_dockerfile)) {
            openEditor(File(projectRoot, "default/Dockerfile"))
        }
        addAction(getString(R.string.action_default_dev_image), getString(R.string.detail_default_dev_image)) {
            openEditor(File(projectRoot, "default/Dockerfile"))
        }
        val files = dockerfiles()
        if (files.isEmpty()) {
            addMessage(getString(R.string.message_no_dockerfile_fmt, projectRoot.absolutePath))
            return
        }
        files.forEach { file ->
            addWidget(file.parentFile?.name ?: file.name, getString(R.string.section_dockerfile), file.absolutePath) {
                openEditor(file)
            }
            addAction(getString(R.string.action_build_fmt, file.parentFile?.name ?: file.name), file.absolutePath) {
                val dir = file.parentFile ?: projectRoot
                runImageBuild(dir, getString(R.string.terminal_docker_build_fmt, dir.name))
            }
        }
    }

    private fun renderImages() {
        addSection(getString(R.string.section_images))
        addAction(getString(R.string.action_pull_image), getString(R.string.detail_pull_image)) {
            showPullImageDialog()
        }
        addAction(getString(R.string.action_browse_image_files), getString(R.string.detail_browse_image_files)) {
            openImageFiles()
        }
        addAction(getString(R.string.action_prune_build_cache), getString(R.string.detail_prune_build_cache)) {
            runPruneBuildCache()
        }
        val images = imageDirs()
        val imageInfos = imageReferenceInfos(images)
        renderImageCacheHealth(imageInfos)
        if (images.isEmpty()) {
            addMessage(getString(R.string.message_no_pulled_images))
            return
        }
        renderImageReferenceTree(imageInfos)
    }

    private fun showPullImageDialog() {
        val input = EditText(this).apply {
            setSingleLine(true)
            hint = getString(R.string.hint_image_reference)
        }
        var imagePlatform = currentImagePlatform()
        val suggestions = imagePullSuggestions().toMutableList()
        val suggestionList = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        val suggestionLabel = TextView(this).apply {
            text = getString(R.string.label_image_pull_suggestions_fmt, imagePlatform)
            textSize = 12f
            setTextColor(0xff666666.toInt())
            setPadding(0, dp(12), 0, dp(4))
        }
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(10), dp(20), 0)
            addView(input, LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            addView(suggestionLabel)
            addView(ScrollView(this@MainActivity).apply {
                addView(suggestionList)
            }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(220)))
        }
        var pendingHubSearch: Runnable? = null
        var hubSearchSerial = 0

        fun renderSuggestions(query: String) {
            suggestionList.removeAllViews()
            val q = query.trim()
            suggestions
                .filter { q.isBlank() || it.contains(q, ignoreCase = true) }
                .take(30)
                .forEach { ref ->
                    suggestionList.addView(TextView(this).apply {
                        text = ref
                        textSize = 14f
                        setPadding(dp(8), dp(8), dp(8), dp(8))
                        setOnClickListener {
                            input.setText(ref)
                            input.setSelection(ref.length)
                        }
                    }, LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                }
        }

        fun scheduleDockerHubSearch(query: String) {
            pendingHubSearch?.let { ui.removeCallbacks(it) }
            val q = query.trim()
            if (q.length < 2 || "/" in q || ":" in q || "@" in q) {
                suggestionLabel.text = getString(R.string.label_image_pull_suggestions_fmt, imagePlatform)
                return
            }
            val serial = ++hubSearchSerial
            val task = Runnable {
                suggestionLabel.text = getString(R.string.message_docker_hub_searching_fmt, q, imagePlatform)
                thread(isDaemon = true, name = "pdocker-docker-hub-search") {
                    val remote = fetchDockerHubImageRefs(q)
                    ui.post {
                        if (serial != hubSearchSerial) return@post
                        var added = 0
                        remote.forEach { ref ->
                            if (suggestions.none { it.equals(ref, ignoreCase = true) }) {
                                suggestions.add(ref)
                                added++
                            }
                        }
                        suggestions.sortWith(compareBy<String> { it.substringBefore(":") }.thenBy { it })
                        suggestionLabel.text = if (added > 0) {
                            getString(R.string.message_docker_hub_results_fmt, added, q, imagePlatform)
                        } else {
                            getString(R.string.label_image_pull_suggestions_fmt, imagePlatform)
                        }
                        renderSuggestions(input.text?.toString().orEmpty())
                    }
                }
            }
            pendingHubSearch = task
            ui.postDelayed(task, 450)
        }

        refreshImagePlatformForDialog { platform ->
            imagePlatform = platform
            suggestionLabel.text = getString(R.string.label_image_pull_suggestions_fmt, imagePlatform)
            scheduleDockerHubSearch(input.text?.toString().orEmpty())
        }

        input.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                val text = s?.toString().orEmpty()
                renderSuggestions(text)
                scheduleDockerHubSearch(text)
            }
            override fun afterTextChanged(s: Editable?) = Unit
        })
        renderSuggestions("")

        AlertDialog.Builder(this)
            .setTitle(getString(R.string.dialog_pull_image_title))
            .setView(body)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                val image = input.text?.toString()?.trim().orEmpty()
                if (image.isBlank()) {
                    status.text = getString(R.string.message_image_reference_required)
                } else {
                    runPullImage(image)
                }
            }
            .show()
    }

    private fun runPullImage(image: String) {
        runEngineJob(
            getString(R.string.terminal_pull_image_fmt, image),
            workspaceGroup(),
            "engine pull $image",
        ) { emit ->
            emit("Image $image Pulling")
            pullImage(image)
        }
    }

    private fun imagePullSuggestions(): List<String> {
        val refs = linkedSetOf(
            "ubuntu:22.04",
            "ubuntu:24.04",
            "debian:bookworm",
            "alpine:3.20",
            "busybox:latest",
        )
        imageDirs().mapNotNullTo(refs) { displayImageRef(it.name).takeIf { ref -> ref.isNotBlank() } }
        val imageLine = Regex("""^\s*image\s*:\s*['"]?([^'"\s#]+)""")
        composeFiles().forEach { file ->
            runCatching {
                file.readLines().forEach { line ->
                    imageLine.find(line)?.groupValues?.getOrNull(1)?.let { refs.add(it) }
                }
            }
        }
        val fromLine = Regex("""^\s*FROM\s+(?:--platform=\S+\s+)?([^@\s]+(?:@[^\s]+|:[^\s]+)?)""", RegexOption.IGNORE_CASE)
        dockerfiles().forEach { file ->
            runCatching {
                file.readLines().forEach { line ->
                    fromLine.find(line)?.groupValues?.getOrNull(1)?.let { refs.add(it) }
                }
            }
        }
        return refs.filter { it.isNotBlank() }.sortedWith(compareBy<String> { it.substringBefore(":") }.thenBy { it })
    }

    private fun fetchDockerHubImageRefs(query: String): List<String> =
        runCatching {
            val encoded = URLEncoder.encode(query, "UTF-8")
            val url = URL("https://hub.docker.com/v2/search/repositories/?query=$encoded&page_size=25")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                connectTimeout = 2500
                readTimeout = 3500
                requestMethod = "GET"
                setRequestProperty("Accept", "application/json")
            }
            try {
                val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
                val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val results = JSONObject(text).optJSONArray("results") ?: JSONArray()
                (0 until results.length()).mapNotNull { i ->
                    results.optJSONObject(i)?.optString("repo_name")
                        ?.takeIf { it.isNotBlank() }
                }
            } finally {
                conn.disconnect()
            }
        }.getOrDefault(emptyList())

    private fun currentImagePlatform(): String =
        hostEnvironment?.optJSONObject("Runtime")?.optString("Platform").orEmpty()
            .ifBlank { abiDefaultImagePlatform() }

    private fun refreshImagePlatformForDialog(onReady: (String) -> Unit) {
        thread(isDaemon = true, name = "pdocker-image-platform") {
            val platform = runCatching {
                engine.getObject("/system/host")
                    .optJSONObject("Runtime")
                    ?.optString("Platform")
                    .orEmpty()
            }.getOrDefault("")
                .ifBlank { abiDefaultImagePlatform() }
            ui.post { onReady(platform) }
        }
    }

    private fun abiDefaultImagePlatform(): String =
        when (Build.SUPPORTED_ABIS.firstOrNull().orEmpty()) {
            "arm64-v8a" -> "linux/arm64"
            "armeabi-v7a", "armeabi" -> "linux/arm/v7"
            "x86_64" -> "linux/amd64"
            "x86" -> "linux/386"
            else -> FALLBACK_IMAGE_PLATFORM
        }

    private fun renderContainers() {
        addSection(getString(R.string.section_containers))
        refreshContainerSnapshotAsync()
        addAction(getString(R.string.action_docker_ps), getString(R.string.detail_docker_ps)) {
            runEngineAction(getString(R.string.terminal_docker_ps), workspaceGroup()) {
                formatContainers(getArray("/containers/json?all=1"))
            }
        }
        val containers = containerDirs()
        if (containers.isEmpty()) {
            addMessage(getString(R.string.message_no_containers))
            return
        }
        val snapshotLookup = containerSnapshotLookup()
        containers.forEach { dir ->
            val state = readState(dir)
            val snapshot = containerSnapshotFor(dir, state, snapshotLookup)
            val target = containerActionTarget(snapshot, state, dir)
            val name = containerDisplayName(snapshot, state, dir)
            val image = state?.optString("Image")?.ifBlank { getString(R.string.unknown_image) } ?: getString(R.string.unknown_image)
            val statusText = snapshot?.optString("Status")?.takeIf { it.isNotBlank() }
                ?: containerCachedStatus(state)
            val running = containerIsRunning(snapshot, state)
            addWidget(name, statusText, "$image\n${containerNetworkSummary(state)}\n${containerLogPreview(dir)}") {
                if (running) {
                    openDockerInteractiveTerminal(
                        getString(R.string.terminal_container_fmt, name),
                        target,
                        name,
                    )
                } else {
                    runContainerAction(name, getString(R.string.terminal_container_start_fmt, name)) {
                        post("/containers/${DockerEngineClient.encodePath(target)}/start")
                        formatContainers(getArray("/containers/json?all=1"))
                    }
                }
            }
            if (running) {
                addAction(getString(R.string.action_container_terminal_fmt, name), getString(R.string.detail_container_terminal)) {
                    openDockerInteractiveTerminal(
                        getString(R.string.terminal_container_fmt, name),
                        target,
                        name,
                    )
                }
            }
            addAction(getString(R.string.action_container_start_fmt, name), target) {
                runContainerAction(name, getString(R.string.terminal_container_start_fmt, name)) {
                    post("/containers/${DockerEngineClient.encodePath(target)}/start")
                    formatContainers(getArray("/containers/json?all=1"))
                }
            }
            addAction(getString(R.string.action_container_stop_fmt, name), target) {
                runContainerAction(name, getString(R.string.terminal_container_stop_fmt, name)) {
                    post("/containers/${DockerEngineClient.encodePath(target)}/stop?t=10")
                    formatContainers(getArray("/containers/json?all=1"))
                }
            }
            addAction(getString(R.string.action_container_restart_fmt, name), target) {
                runContainerAction(name, getString(R.string.terminal_container_restart_fmt, name)) {
                    runCatching { post("/containers/${DockerEngineClient.encodePath(target)}/stop?t=10") }
                    post("/containers/${DockerEngineClient.encodePath(target)}/start")
                    formatContainers(getArray("/containers/json?all=1"))
                }
            }
            addAction(getString(R.string.action_container_logs_fmt, name), target) {
                runContainerAction(name, getString(R.string.terminal_container_logs_fmt, name)) {
                    logs(target, 200).ifBlank { "(no logs)" }
                }
            }
            addAction(getString(R.string.action_browse_container_files_fmt, name), dir.name) {
                openContainerFiles(dir)
            }
            containerServiceUrls(state).forEach { (label, url) ->
                addAction(serviceActionTitle(label, url), serviceActionDetail(url)) {
                    openServiceUrl(url)
                }
            }
        }
    }

    private fun containerDisplayName(snapshot: JSONObject?, state: JSONObject?, dir: File): String {
        val names = snapshot?.optJSONArray("Names")
        val fromSnapshot = names?.optString(0).orEmpty().trim('/').takeIf { it.isNotBlank() }
        return fromSnapshot
            ?: state?.optString("Name")?.trim('/')?.takeIf { it.isNotBlank() }
            ?: dir.name
    }

    private fun containerSnapshotFor(dir: File, state: JSONObject?, lookup: ContainerSnapshotLookup): JSONObject? {
        containerEngineIdKeys(dir, state).forEach { key ->
            lookup.byEngineId[key]?.let { return it }
        }
        val stateName = state?.optString("Name").orEmpty().trim('/')
        return lookup.byUniqueName[stateName]
    }

    private fun containerActionTarget(snapshot: JSONObject?, state: JSONObject?, dir: File): String =
        snapshot?.optString("Id")?.takeIf { it.isNotBlank() }
            ?: state?.optString("Id")?.takeIf { it.isNotBlank() }
            ?: dir.name

    @Suppress("UNUSED_PARAMETER")
    private fun containerIsRunning(snapshot: JSONObject?, state: JSONObject?): Boolean {
        if (snapshot != null) return containerSnapshotIsRunning(snapshot)
        // Persisted state is useful context, but is not current Engine truth.
        // Do not expose running-only UI until /containers/json confirms this
        // container by Engine ID/name in the current process table.
        return false
    }

    private fun containerEngineIdKeys(dir: File, state: JSONObject?): List<String> =
        listOf(
            state?.optString("Id").orEmpty(),
            dir.name.takeIf { looksLikeContainerEngineId(it) }.orEmpty(),
        ).filter { it.isNotBlank() }.distinct()

    private fun looksLikeContainerEngineId(value: String): Boolean =
        value.length >= 12 && value.all { it in '0'..'9' || it in 'a'..'f' }

    private fun containerSnapshotLookup(): ContainerSnapshotLookup {
        val byEngineId = mutableMapOf<String, JSONObject>()
        val byName = mutableMapOf<String, MutableList<JSONObject>>()
        containerSnapshot.forEach { obj ->
            val id = obj.optString("Id")
            if (id.isNotBlank()) {
                byEngineId[id] = obj
                byEngineId[id.take(12)] = obj
            }
            val names = obj.optJSONArray("Names")
            if (names != null) {
                for (i in 0 until names.length()) {
                    val name = names.optString(i).trim('/')
                    if (name.isNotBlank()) byName.getOrPut(name) { mutableListOf() } += obj
                }
            }
        }
        val byUniqueName = byName.mapNotNull { (name, matches) ->
            matches.distinctBy { it.optString("Id") }.singleOrNull()?.let { name to it }
        }.toMap()
        return ContainerSnapshotLookup(byEngineId, byUniqueName)
    }

    private fun containerInventoryValue(): String {
        val local = containerDirs().size
        if (containerSnapshot.isEmpty()) {
            return if (lastContainerSnapshotAt == 0L && local > 0) {
                getString(R.string.container_inventory_syncing_fmt, local)
            } else {
                getString(R.string.container_inventory_fmt, local, 0)
            }
        }
        val running = containerSnapshot.count { obj ->
            obj.optString("State").equals("running", ignoreCase = true) ||
                obj.optString("Status").startsWith("Up", ignoreCase = true)
        }
        return getString(R.string.container_inventory_fmt, containerSnapshot.size, running)
    }

    private fun containerCachedStatus(state: JSONObject?): String {
        val cached = state?.optJSONObject("State")
            ?.optString("Status")
            ?.ifBlank { null }
        return when {
            lastContainerSnapshotAt > 0L -> getString(R.string.container_status_not_in_ps)
            cached != null -> getString(R.string.container_status_cached_fmt, cached)
            else -> getString(R.string.container_status_syncing)
        }
    }

    private fun refreshContainerSnapshotAsync(force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (containerSnapshotRefreshing) return
        if (!force && now - lastContainerSnapshotAt < 2500L) return
        containerSnapshotRefreshing = true
        thread(isDaemon = true, name = "pdocker-container-snapshot") {
            val arr = runCatching { engine.getArray("/containers/json?all=1") }.getOrNull()
            val list = if (arr == null) {
                emptyList()
            } else {
                (0 until arr.length())
                    .mapNotNull { arr.optJSONObject(it) }
                    .sortedWith(containerSnapshotComparator())
            }
            val fingerprint = list.joinToString("\n") { obj ->
                listOf(
                    obj.optString("Id"),
                    obj.optString("Status"),
                    obj.optString("State"),
                    obj.optJSONArray("Names")?.toString().orEmpty(),
                ).joinToString("|")
            }
            ui.post {
                val changed = fingerprint != containerSnapshotFingerprint
                containerSnapshot = list
                containerSnapshotFingerprint = fingerprint
                containerSnapshotRefreshing = false
                lastContainerSnapshotAt = System.currentTimeMillis()
                if (changed && currentTab in setOf(Tab.Overview, Tab.Containers, Tab.Compose)) {
                    renderContent()
                }
            }
        }
    }

    private fun renderSessions() {
        addSection(getString(R.string.section_sessions))
        addAction(getString(R.string.action_text_editor), getString(R.string.detail_text_editor)) {
            openEditor(File(projectRoot, "default/Dockerfile"))
        }
        addAction(getString(R.string.action_console_editor_split), getString(R.string.detail_console_editor_split)) {
            openConsoleEditorSplit(
                getString(R.string.action_console_editor_split),
                "cd ${shellQuote(projectRoot.absolutePath)} && sh",
                File(projectRoot, "default/Dockerfile"),
            )
        }
        renderProjectFileShortcuts()
        renderDiagnostics()
    }

    private fun renderDiagnostics() {
        addSection(getString(R.string.section_diagnostics))
        renderHostEnvironment()
        addAction(getString(R.string.action_debug_resources), getString(R.string.detail_debug_resources)) {
            openDebugResources(filesDir, getString(R.string.debug_resource_app_files), writable = true)
        }
        addAction(getString(R.string.action_keep_resident), getString(R.string.detail_keep_resident)) {
            requestBatteryOptimizationBypass()
        }
        addAction(getString(R.string.action_enable_notifications), getString(R.string.detail_enable_notifications)) {
            requestNotificationPermission()
        }
        addAction(getString(R.string.action_set_documents_volume), documentsVolumeDetail()) {
            requestDocumentsVolumeFolder()
        }
        addAction(getString(R.string.action_documents_sync_to_tree), getString(R.string.detail_documents_sync_to_tree)) {
            runDocumentsMediatorAction(getString(R.string.action_documents_sync_to_tree)) {
                safDocumentsMediator().syncToTree()
            }
        }
        addAction(getString(R.string.action_documents_sync_from_tree), getString(R.string.detail_documents_sync_from_tree)) {
            runDocumentsMediatorAction(getString(R.string.action_documents_sync_from_tree)) {
                safDocumentsMediator().syncFromTree()
            }
        }
        addAction(getString(R.string.action_prune_build_cache), getString(R.string.detail_prune_build_cache)) {
            runEngineAction(getString(R.string.action_prune_build_cache), getString(R.string.section_diagnostics)) {
                post("/build/prune").text
            }
        }
        if (!diagnosticsEnabled) return
        addAction(getString(R.string.action_start_pdockerd), getString(R.string.detail_start_pdockerd)) { startDaemon() }
        addAction(getString(R.string.action_stop_pdockerd), getString(R.string.detail_stop_pdockerd)) {
            startService(Intent(this, PdockerdService::class.java).setAction(PdockerdService.ACTION_STOP))
            status.text = getString(R.string.status_stopped)
        }
        addAction(getString(R.string.action_run_gpu_bench), getString(R.string.detail_run_gpu_bench)) {
            runAndroidGpuBench()
        }
        addAction(getString(R.string.action_docker_console), getString(R.string.detail_docker_console)) {
            startDaemon()
            openTerminal(
                getString(R.string.action_docker_console),
                "printf '[pdocker] upstream Docker CLI is not packaged in this APK.\\n[pdocker] Use UI Engine actions; test suites may stage Docker CLI separately.\\n'; sh",
            )
        }
        addAction(getString(R.string.action_host_shell), getString(R.string.detail_host_shell)) {
            openTerminal(getString(R.string.terminal_host_shell), "sh")
        }
        addAction(getString(R.string.action_library_shell), getString(R.string.detail_library_shell)) {
            projectRoot.mkdirs()
            openTerminal(getString(R.string.action_library_shell), "cd ${shellQuote(projectRoot.absolutePath)} && find . -maxdepth 2 -name compose.yaml -o -name Dockerfile; sh")
        }
        addAction(getString(R.string.action_compose_shell), getString(R.string.detail_open_at_fmt, projectRoot.absolutePath)) {
            projectRoot.mkdirs()
            openTerminal(getString(R.string.section_compose), "cd ${shellQuote(projectRoot.absolutePath)} && sh")
        }
        addAction(getString(R.string.action_build_shell), getString(R.string.detail_build_shell)) {
            projectRoot.mkdirs()
            openTerminal(getString(R.string.terminal_docker_build), "cd ${shellQuote(projectRoot.absolutePath)} && sh")
        }
    }

    private data class DebugResourceRoot(
        val label: String,
        val root: File,
        val writable: Boolean,
    )

    private fun renderDebugResources() {
        addSection(getString(R.string.section_debug_resources))
        addMessage(getString(R.string.message_debug_resources))
        addAction(getString(R.string.action_debug_memory), getString(R.string.detail_debug_memory)) {
            openTextToolAsync(getString(R.string.section_debug_resources), getString(R.string.action_debug_memory)) {
                debugMemorySnapshot()
            }
        }
        addAction(getString(R.string.action_debug_processes), getString(R.string.detail_debug_processes)) {
            openTextToolAsync(getString(R.string.section_debug_resources), getString(R.string.action_debug_processes)) {
                debugProcessSnapshot()
            }
        }
        addAction(getString(R.string.action_debug_handles), getString(R.string.detail_debug_handles)) {
            openTextToolAsync(getString(R.string.section_debug_resources), getString(R.string.action_debug_handles)) {
                debugHandleSnapshot()
            }
        }
        debugResourceRoots().forEach { item ->
            val detail = listOf(
                item.root.absolutePath,
                debugResourceSummary(item.root),
            ).joinToString("\n")
            addAction(item.label, detail) {
                openDebugResources(item.root, item.label, item.writable)
            }
        }
    }

    private fun debugResourceRoots(): List<DebugResourceRoot> {
        val roots = mutableListOf(
            DebugResourceRoot(getString(R.string.debug_resource_app_files), filesDir, writable = true),
            DebugResourceRoot(getString(R.string.debug_resource_pdocker_home), pdockerHome, writable = true),
            DebugResourceRoot(getString(R.string.debug_resource_projects), projectRoot, writable = true),
            DebugResourceRoot(getString(R.string.debug_resource_containers), containerRoot, writable = true),
            DebugResourceRoot(getString(R.string.debug_resource_images), imageRoot, writable = true),
            DebugResourceRoot(getString(R.string.debug_resource_layers), layerRoot, writable = true),
            DebugResourceRoot(getString(R.string.debug_resource_runtime), File(filesDir, "pdocker-runtime"), writable = true),
            DebugResourceRoot(getString(R.string.debug_resource_cache), cacheDir, writable = true),
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            roots += DebugResourceRoot(getString(R.string.debug_resource_code_cache), codeCacheDir, writable = true)
            roots += DebugResourceRoot(getString(R.string.debug_resource_no_backup), noBackupFilesDir, writable = true)
        }
        applicationInfo.nativeLibraryDir
            ?.takeIf { it.isNotBlank() }
            ?.let { roots += DebugResourceRoot(getString(R.string.debug_resource_native_libs), File(it), writable = false) }
        return roots.distinctBy { runCatching { it.root.canonicalPath }.getOrDefault(it.root.absolutePath) }
            .filter { it.root.exists() }
    }

    private fun debugResourceSummary(root: File): String =
        if (root.isDirectory) {
            getString(R.string.debug_resource_dir_summary_fmt, root.list()?.size ?: 0)
        } else {
            getString(R.string.debug_resource_file_summary_fmt, formatBytes(root.length()))
        }

    private fun openDebugResources(root: File, label: String, writable: Boolean) {
        startActivity(Intent(this, ImageFilesActivity::class.java).apply {
            putExtra(ImageFilesActivity.EXTRA_ROOT_PATH, root.absolutePath)
            putExtra(ImageFilesActivity.EXTRA_ROOT_LABEL, label)
            putExtra(ImageFilesActivity.EXTRA_ROOT_WRITABLE, writable)
        })
    }

    private data class DebugProc(
        val pid: Int,
        val name: String,
        val state: String,
        val ppid: String,
        val threads: String,
        val vmRss: String,
        val fdCount: Int,
        val cmdline: String,
    )

    private data class MemoryLayerSnapshot(
        val memTotal: Long,
        val memAvailable: Long,
        val swapTotal: Long,
        val swapFree: Long,
        val pdockerProcessCount: Int,
        val pdockerVmSize: Long,
        val pdockerRss: Long,
        val pdockerSwap: Long,
        val appVmSize: Long,
        val appVmRss: Long,
        val appVmData: Long,
        val appVmStk: Long,
        val appVmSwap: Long,
        val javaHeapMax: Long,
        val javaHeapUsed: Long,
        val managedReserveBytes: Long,
        val managedResidentBytes: Long,
        val managedBackingBytes: Long,
        val managedPageIns: Long,
        val managedPageOuts: Long,
        val managedBytesIn: Long,
        val managedBytesOut: Long,
        val managedDirtyPageOuts: Long,
        val managedElapsedNs: Long,
        val transparentRegistered: Boolean,
        val transparentSigsegvStops: Long,
        val transparentLastMmapLen: Long,
        val transparentPendingAfterEntry: Long,
        val transparentMaxResidentPages: Long,
        val transparentBytesIn: Long,
        val transparentBytesOut: Long,
        val transparentDirtyPageOuts: Long,
        val artifactCreatedAtEpoch: Long,
        val artifactStatus: String,
        val artifactAgeSeconds: Long,
        val source: String,
    )

    private data class PdockerMemoryFootprint(
        val processCount: Int,
        val vmSize: Long,
        val rss: Long,
        val swap: Long,
    )

    private fun renderMemoryLayerVisualization() {
        val snapshot = memoryLayerSnapshot()
        addSection(getString(R.string.section_memory_layers))
        content.addView(MemoryLayerView(this).apply {
            setSnapshot(snapshot)
        }, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(430),
        ))
        addMessage(memoryLayerSummary(snapshot))
        addAction(getString(R.string.action_debug_memory_layers), getString(R.string.detail_debug_memory_layers)) {
            openTextToolAsync(getString(R.string.section_memory_layers), getString(R.string.action_debug_memory_layers)) {
                memoryLayerSnapshotText(memoryLayerSnapshot())
            }
        }
        addAction(getString(R.string.action_run_memory_pager_selftest), getString(R.string.detail_run_memory_pager_selftest)) {
            runMemoryPagerSelfTest()
        }
    }

    private fun memoryLayerSnapshot(): MemoryLayerSnapshot {
        val mem = readMeminfoBytes()
        val proc = readProcStatus(File("/proc/${android.os.Process.myPid()}")).orEmpty()
        val runtime = Runtime.getRuntime()
        val pdockerFootprint = pdockerMemoryFootprint()
        val managedArtifact = readLatestPagerArtifact("apk-memory-pager-managed-latest.json", "managed")
        val transparentArtifact = readLatestPagerArtifact("apk-memory-pager-transparent-latest.json", "transparent")
        val managed = managedArtifact.metrics
        val transparent = transparentArtifact.metrics
        val managedReserve = managed.optLong("reserve_bytes", 0L)
        val transparentLastMmapLen = transparent.optLong("last_mmap_len", 0L)
        val transparentPendingAfterEntry = transparent.optLong("pending_after_entry", 0L)
        val transparentMaxResidentPages = transparent.optLong("max_resident_pages", 0L)
        val managedResidentPages = maxOf(
            managed.optLong("resident_pages", managed.optLong("max_resident_pages", 0L)),
            transparentMaxResidentPages,
        )
        val managedBytesOut = maxOf(managed.optLong("bytes_out", 0L), transparent.optLong("bytes_out", 0L))
        val managedBytesIn = maxOf(managed.optLong("bytes_in", 0L), transparent.optLong("bytes_in", 0L))
        val managedDirtyPageOuts = maxOf(managed.optLong("dirty_page_outs", 0L), transparent.optLong("dirty_page_outs", 0L))
        val transparentRegistered = transparent.optString("registered").equals("yes", ignoreCase = true) ||
            transparent.optString("result").equals("ok", ignoreCase = true)
        val displayArtifact = if (transparentArtifact.present) transparentArtifact else managedArtifact
        val artifactAgeSeconds = if (displayArtifact.createdAtEpoch > 0L) {
            (System.currentTimeMillis() / 1000L - displayArtifact.createdAtEpoch).coerceAtLeast(0L)
        } else 0L
        val artifactLabel = if (displayArtifact.present) {
            "past self-test ${displayArtifact.kind} artifact"
        } else {
            "live /proc only"
        }
        return MemoryLayerSnapshot(
            memTotal = mem["MemTotal"] ?: 0L,
            memAvailable = mem["MemAvailable"] ?: mem["MemFree"] ?: 0L,
            swapTotal = mem["SwapTotal"] ?: 0L,
            swapFree = mem["SwapFree"] ?: 0L,
            pdockerProcessCount = pdockerFootprint.processCount,
            pdockerVmSize = pdockerFootprint.vmSize,
            pdockerRss = pdockerFootprint.rss,
            pdockerSwap = pdockerFootprint.swap,
            appVmSize = procStatusBytes(proc, "VmSize"),
            appVmRss = procStatusBytes(proc, "VmRSS"),
            appVmData = procStatusBytes(proc, "VmData"),
            appVmStk = procStatusBytes(proc, "VmStk"),
            appVmSwap = procStatusBytes(proc, "VmSwap"),
            javaHeapMax = runtime.maxMemory(),
            javaHeapUsed = runtime.totalMemory() - runtime.freeMemory(),
            managedReserveBytes = maxOf(managedReserve, transparentLastMmapLen, transparentPendingAfterEntry),
            managedResidentBytes = managedResidentPages * 4096L,
            managedBackingBytes = maxOf(managedBytesOut, managedReserve, transparentLastMmapLen, transparentPendingAfterEntry),
            managedPageIns = maxOf(managed.optLong("page_ins", 0L), transparent.optLong("page_ins", 0L)),
            managedPageOuts = maxOf(managed.optLong("page_outs", 0L), transparent.optLong("page_outs", 0L)),
            managedBytesIn = managedBytesIn,
            managedBytesOut = managedBytesOut,
            managedDirtyPageOuts = managedDirtyPageOuts,
            managedElapsedNs = maxOf(managed.optLong("elapsed_ns", 0L), transparent.optLong("elapsed_ns", 0L)),
            transparentRegistered = transparentRegistered,
            transparentSigsegvStops = transparent.optLong("sigsegv_stops", 0L),
            transparentLastMmapLen = transparentLastMmapLen,
            transparentPendingAfterEntry = transparentPendingAfterEntry,
            transparentMaxResidentPages = transparentMaxResidentPages,
            transparentBytesIn = transparent.optLong("bytes_in", 0L),
            transparentBytesOut = transparent.optLong("bytes_out", 0L),
            transparentDirtyPageOuts = transparent.optLong("dirty_page_outs", 0L),
            artifactCreatedAtEpoch = displayArtifact.createdAtEpoch,
            artifactStatus = displayArtifact.status,
            artifactAgeSeconds = artifactAgeSeconds,
            source = artifactLabel,
        )
    }

    private fun memoryLayerSummary(snapshot: MemoryLayerSnapshot): String =
        listOf(
            getString(
                R.string.memory_layers_os_summary_fmt,
                formatBytes((snapshot.memTotal - snapshot.memAvailable).coerceAtLeast(0L)),
                formatBytes(snapshot.memAvailable),
                formatBytes((snapshot.swapTotal - snapshot.swapFree).coerceAtLeast(0L)),
                formatBytes(snapshot.swapFree),
            ),
            getString(
                R.string.memory_layers_pdocker_share_fmt,
                formatBytes(snapshot.pdockerRss),
                formatPercent(snapshot.pdockerRss, snapshot.memTotal),
                formatPercent(snapshot.pdockerRss, (snapshot.memTotal - snapshot.memAvailable).coerceAtLeast(0L)),
                formatBytes(snapshot.pdockerSwap),
                formatPercent(snapshot.pdockerSwap, (snapshot.swapTotal - snapshot.swapFree).coerceAtLeast(0L)),
                snapshot.pdockerProcessCount,
            ),
            getString(
                R.string.memory_layers_app_summary_fmt,
                formatBytes(snapshot.appVmSize),
                formatBytes(snapshot.appVmRss),
                formatBytes(snapshot.javaHeapUsed),
                formatBytes(snapshot.javaHeapMax),
            ),
            getString(
                R.string.memory_layers_artifact_summary_fmt,
                snapshot.source,
                formatArtifactAge(snapshot.artifactAgeSeconds, snapshot.artifactCreatedAtEpoch),
                snapshot.artifactStatus.ifBlank { "unknown" },
            ),
            getString(
                R.string.memory_layers_pager_summary_fmt,
                if (snapshot.transparentRegistered) "transparent SIGSEGV" else "available / idle",
                formatBytes(snapshot.managedReserveBytes),
                formatBytes(snapshot.managedResidentBytes),
                formatBytes(snapshot.managedBackingBytes),
                snapshot.managedPageIns,
                snapshot.managedPageOuts,
                formatBytes(snapshot.managedBytesIn),
                formatBytes(snapshot.managedBytesOut),
                snapshot.managedDirtyPageOuts,
            ),
            getString(
                R.string.memory_layers_perf_summary_fmt,
                formatDurationMs(snapshot.managedElapsedNs),
                formatPagerRate(snapshot.managedPageIns + snapshot.managedPageOuts, snapshot.managedElapsedNs),
                snapshot.transparentSigsegvStops,
                formatBytes(snapshot.transparentLastMmapLen),
                formatBytes(snapshot.transparentPendingAfterEntry),
                snapshot.transparentMaxResidentPages,
                formatBytes(snapshot.transparentBytesIn),
                formatBytes(snapshot.transparentBytesOut),
                snapshot.transparentDirtyPageOuts,
            ),
        ).joinToString("\n")

    private fun memoryLayerSnapshotText(snapshot: MemoryLayerSnapshot): String = buildString {
        appendLine("pdocker memory layer visualization")
        appendLine("source=${snapshot.source}")
        appendLine("artifact.created_at_epoch=${snapshot.artifactCreatedAtEpoch}")
        appendLine("artifact.age=${formatArtifactAge(snapshot.artifactAgeSeconds, snapshot.artifactCreatedAtEpoch)}")
        appendLine("artifact.status=${snapshot.artifactStatus.ifBlank { "unknown" }}")
        appendLine()
        appendLine("== OS-governed memory ==")
        appendLine("physical.total=${formatBytes(snapshot.memTotal)}")
        appendLine("physical.used=${formatBytes((snapshot.memTotal - snapshot.memAvailable).coerceAtLeast(0L))}")
        appendLine("physical.available=${formatBytes(snapshot.memAvailable)}")
        appendLine("swap.total=${formatBytes(snapshot.swapTotal)}")
        appendLine("swap.used=${formatBytes((snapshot.swapTotal - snapshot.swapFree).coerceAtLeast(0L))}")
        appendLine("swap.free=${formatBytes(snapshot.swapFree)}")
        appendLine("pdocker.processes=${snapshot.pdockerProcessCount}")
        appendLine("pdocker.VmSize=${formatBytes(snapshot.pdockerVmSize)}")
        appendLine("pdocker.RSS=${formatBytes(snapshot.pdockerRss)}")
        appendLine("pdocker.RSS.percent_of_RAM=${formatPercent(snapshot.pdockerRss, snapshot.memTotal)}")
        appendLine("pdocker.RSS.percent_of_used_RAM=${formatPercent(snapshot.pdockerRss, (snapshot.memTotal - snapshot.memAvailable).coerceAtLeast(0L))}")
        appendLine("pdocker.VmSwap=${formatBytes(snapshot.pdockerSwap)}")
        appendLine("pdocker.VmSwap.percent_of_used_swap=${formatPercent(snapshot.pdockerSwap, (snapshot.swapTotal - snapshot.swapFree).coerceAtLeast(0L))}")
        appendLine()
        appendLine("== App process allocation view ==")
        appendLine("VmSize=${formatBytes(snapshot.appVmSize)}")
        appendLine("VmRSS=${formatBytes(snapshot.appVmRss)}")
        appendLine("VmData=${formatBytes(snapshot.appVmData)}")
        appendLine("VmStk=${formatBytes(snapshot.appVmStk)}")
        appendLine("VmSwap=${formatBytes(snapshot.appVmSwap)}")
        appendLine("JavaHeap.used=${formatBytes(snapshot.javaHeapUsed)}")
        appendLine("JavaHeap.max=${formatBytes(snapshot.javaHeapMax)}")
        appendLine()
        appendLine("== pdocker managed virtual-memory skin ==")
        appendLine("mode=${if (snapshot.transparentRegistered) "transparent SIGSEGV pager observed" else "capability present / no live region observed"}")
        appendLine("reserve=${formatBytes(snapshot.managedReserveBytes)}")
        appendLine("resident=${formatBytes(snapshot.managedResidentBytes)}")
        appendLine("backing=${formatBytes(snapshot.managedBackingBytes)}")
        appendLine("page_ins=${snapshot.managedPageIns}")
        appendLine("page_outs=${snapshot.managedPageOuts}")
        appendLine("bytes_in=${formatBytes(snapshot.managedBytesIn)}")
        appendLine("bytes_out=${formatBytes(snapshot.managedBytesOut)}")
        appendLine("dirty_page_outs=${snapshot.managedDirtyPageOuts}")
        appendLine("transparent.last_mmap_len=${formatBytes(snapshot.transparentLastMmapLen)}")
        appendLine("transparent.pending_after_entry=${formatBytes(snapshot.transparentPendingAfterEntry)}")
        appendLine("transparent.max_resident_pages=${snapshot.transparentMaxResidentPages}")
        appendLine("transparent.bytes_in=${formatBytes(snapshot.transparentBytesIn)}")
        appendLine("transparent.bytes_out=${formatBytes(snapshot.transparentBytesOut)}")
        appendLine("transparent.dirty_page_outs=${snapshot.transparentDirtyPageOuts}")
        appendLine("sigsegv_stops=${snapshot.transparentSigsegvStops}")
        appendLine("elapsed=${formatDurationMs(snapshot.managedElapsedNs)}")
        appendLine("page_ops_per_sec=${formatPagerRate(snapshot.managedPageIns + snapshot.managedPageOuts, snapshot.managedElapsedNs)}")
        appendLine()
        appendLine("Layer model:")
        appendLine("Linux/Android kernel owns physical RAM, swap/zram, page tables, and LMK.")
        appendLine("pdocker only wraps selected large private anonymous mappings.")
        appendLine("The wrapper reserves guest virtual address space as PROT_NONE, pages data into a bounded resident window, and writes evicted pages into an app-owned backing file.")
    }

    private fun readMeminfoBytes(): Map<String, Long> =
        readSmallProcFile(File("/proc/meminfo"), 120)
            .lineSequence()
            .mapNotNull { line ->
                val key = line.substringBefore(':', "").trim()
                val kb = Regex("""([0-9]+)\s+kB""").find(line)?.groupValues?.getOrNull(1)?.toLongOrNull()
                if (key.isBlank() || kb == null) null else key to kb * 1024L
            }
            .toMap()

    private fun procStatusBytes(status: Map<String, String>, key: String): Long =
        Regex("""([0-9]+)\s+kB""")
            .find(status[key].orEmpty())
            ?.groupValues
            ?.getOrNull(1)
            ?.toLongOrNull()
            ?.times(1024L)
            ?: 0L

    private fun pdockerMemoryFootprint(): PdockerMemoryFootprint {
        val appUid = applicationInfo.uid.toString()
        var count = 0
        var vmSize = 0L
        var rss = 0L
        var swap = 0L
        File("/proc").listFiles().orEmpty()
            .filter { it.name.all(Char::isDigit) }
            .forEach { dir ->
                val status = readProcStatus(dir) ?: return@forEach
                val uid = status["Uid"]?.split(Regex("\\s+"))?.firstOrNull().orEmpty()
                val cmdline = procCmdline(dir)
                val name = status["Name"].orEmpty()
                val interesting = uid == appUid ||
                    cmdline.contains(packageName) ||
                    cmdline.contains("pdocker") ||
                    name.contains("pdocker", ignoreCase = true)
                if (!interesting) return@forEach
                count++
                vmSize += procStatusBytes(status, "VmSize")
                rss += procStatusBytes(status, "VmRSS")
                swap += procStatusBytes(status, "VmSwap")
            }
        return PdockerMemoryFootprint(count, vmSize, rss, swap)
    }

    private fun formatPercent(part: Long, total: Long): String =
        if (part <= 0L || total <= 0L) "0.0%" else String.format("%.1f%%", part * 100.0 / total)

    private data class PagerMetricsArtifact(
        val metrics: JSONObject,
        val kind: String,
        val createdAtEpoch: Long,
        val status: String,
        val present: Boolean,
    )

    private fun readLatestPagerArtifact(fileName: String, kind: String): PagerMetricsArtifact {
        val empty = PagerMetricsArtifact(JSONObject(), kind, 0L, "", false)
        val candidates = listOf(
            File(pdockerHome, "docs/test/$fileName"),
            File(pdockerHome, "test/$fileName"),
            File(projectRoot, "default/docs/test/$fileName"),
            File(filesDir, "docs/test/$fileName"),
        )
        val file = candidates.firstOrNull { it.isFile } ?: return empty
        return runCatching {
            val root = JSONObject(file.readText())
            PagerMetricsArtifact(
                metrics = root.optJSONObject("metrics") ?: JSONObject(),
                kind = kind,
                createdAtEpoch = root.optLong("created_at_epoch", 0L),
                status = root.optString("status"),
                present = true,
            )
        }.getOrDefault(empty)
    }

    private fun formatArtifactAge(ageSeconds: Long, createdAtEpoch: Long): String {
        if (createdAtEpoch <= 0L) return "unknown age"
        val days = ageSeconds / 86_400L
        val hours = (ageSeconds % 86_400L) / 3_600L
        val minutes = (ageSeconds % 3_600L) / 60L
        return when {
            days > 0L -> "${days}d ${hours}h ago"
            hours > 0L -> "${hours}h ${minutes}m ago"
            minutes > 0L -> "${minutes}m ago"
            else -> "${ageSeconds}s ago"
        }
    }

    private fun runMemoryPagerSelfTest() {
        status.text = getString(R.string.action_run_memory_pager_selftest)
        thread(isDaemon = true, name = "pdocker-memory-pager-selftest") {
            val report = runCatching {
                val testDir = File(pdockerHome, "docs/test").apply { mkdirs() }
                val managed = runDirectPagerProbe("--pdocker-memory-pager-managed-poc")
                val transparent = runDirectPagerProbe("--pdocker-memory-pager-transparent-poc")
                val managedJson = pagerProbeJson("pdocker.apk-memory-pager-managed.ui.v1", managed)
                val transparentJson = pagerProbeJson("pdocker.apk-memory-pager-transparent.ui.v1", transparent)
                File(testDir, "apk-memory-pager-managed-latest.json").writeText(managedJson.toString(2))
                File(testDir, "apk-memory-pager-transparent-latest.json").writeText(transparentJson.toString(2))
                buildString {
                    appendLine("pdocker memory pager self-test")
                    appendLine("managed=${managed.status} rc=${managed.exitCode}")
                    appendLine(managed.output.trim())
                    appendLine()
                    appendLine("transparent=${transparent.status} rc=${transparent.exitCode}")
                    appendLine(transparent.output.trim())
                }
            }.getOrElse { getString(R.string.engine_operation_failed_fmt, it.message.orEmpty()) }
            ui.post {
                renderContent()
                openTextTool(getString(R.string.section_memory_layers), getString(R.string.action_run_memory_pager_selftest), report)
            }
        }
    }

    private data class PagerProbeRun(val status: String, val exitCode: Int, val output: String)

    private fun runDirectPagerProbe(arg: String): PagerProbeRun {
        PdockerdRuntime.prepare(this)
        val direct = File(filesDir, "pdocker-runtime/docker-bin/pdocker-direct")
        val pb = ProcessBuilder(direct.absolutePath, arg)
            .directory(filesDir)
            .redirectErrorStream(true)
        pb.environment()["TMPDIR"] = "files"
        pb.environment()["PDOCKER_MEMORY_PAGER_POC_PAGES"] = "16"
        pb.environment()["PDOCKER_MEMORY_PAGER_POC_RESIDENT_PAGES"] = "2"
        val proc = pb.start()
        val output = proc.inputStream.bufferedReader().use { it.readText() }
        val rc = proc.waitFor()
        val ok = rc == 0 && output.contains("result=ok")
        return PagerProbeRun(if (ok) "pass" else "fail", rc, output)
    }

    private fun pagerProbeJson(schema: String, run: PagerProbeRun): JSONObject {
        val metrics = JSONObject()
        run.output.lineSequence().forEach { line ->
            val match = Regex("""pager-[a-z-]+-poc:([a-z_]+)=([0-9]+|ok|fail|yes|no)""")
                .find(line.trim()) ?: return@forEach
            val value = match.groupValues[2]
            metrics.put(match.groupValues[1], value.toLongOrNull() ?: value)
        }
        return JSONObject()
            .put("schema", schema)
            .put("created_at_epoch", System.currentTimeMillis() / 1000L)
            .put("status", run.status)
            .put("return_codes", JSONObject().put("run", run.exitCode))
            .put("metrics", metrics)
            .put("stdout", run.output)
    }

    private fun formatDurationMs(ns: Long): String =
        if (ns <= 0L) "-" else String.format("%.3f ms", ns / 1_000_000.0)

    private fun formatPagerRate(ops: Long, ns: Long): String =
        if (ops <= 0L || ns <= 0L) "-" else String.format("%.0f ops/s", ops * 1_000_000_000.0 / ns)

    private fun debugProcesses(): List<DebugProc> {
        val appUid = applicationInfo.uid.toString()
        return File("/proc").listFiles().orEmpty()
            .filter { it.name.all(Char::isDigit) }
            .mapNotNull { dir ->
                val status = readProcStatus(dir) ?: return@mapNotNull null
                val uid = status["Uid"]?.split(Regex("\\s+"))?.firstOrNull().orEmpty()
                val cmdline = procCmdline(dir)
                val name = status["Name"].orEmpty()
                val interesting = uid == appUid ||
                    cmdline.contains(packageName) ||
                    cmdline.contains("pdocker") ||
                    name.contains("pdocker", ignoreCase = true)
                if (!interesting) return@mapNotNull null
                DebugProc(
                    pid = dir.name.toIntOrNull() ?: return@mapNotNull null,
                    name = name.ifBlank { "-" },
                    state = status["State"].orEmpty().ifBlank { "-" },
                    ppid = status["PPid"].orEmpty().ifBlank { "-" },
                    threads = status["Threads"].orEmpty().ifBlank { "-" },
                    vmRss = status["VmRSS"].orEmpty().ifBlank { "-" },
                    fdCount = File(dir, "fd").list()?.size ?: -1,
                    cmdline = cmdline.ifBlank { name },
                )
            }
            .sortedBy { it.pid }
    }

    private fun debugMemorySnapshot(): String = buildString {
        appendLine("pdocker memory snapshot")
        appendLine("package=$packageName uid=${applicationInfo.uid}")
        appendLine("time=${System.currentTimeMillis()}")
        appendLine()
        appendLine("== /proc/meminfo ==")
        appendLine(readSmallProcFile(File("/proc/meminfo"), 80).ifBlank { "unavailable" })
        appendLine("== pdocker processes ==")
        debugProcesses().forEach { proc ->
            appendLine("${proc.pid} ${proc.name} rss=${proc.vmRss} threads=${proc.threads} fd=${proc.fdCount} ${proc.cmdline}")
        }
    }

    private fun debugProcessSnapshot(): String = buildString {
        appendLine("pdocker process snapshot")
        appendLine("package=$packageName uid=${applicationInfo.uid}")
        appendLine()
        appendLine("PID     PPID    STATE        THR  FD    RSS        NAME / CMDLINE")
        debugProcesses().forEach { proc ->
            appendLine(
                "%-7d %-7s %-12s %-4s %-5d %-10s %s / %s".format(
                    proc.pid,
                    proc.ppid,
                    proc.state.take(12),
                    proc.threads,
                    proc.fdCount,
                    proc.vmRss,
                    proc.name,
                    proc.cmdline,
                )
            )
        }
    }

    private fun debugHandleSnapshot(): String = buildString {
        appendLine("pdocker handle snapshot")
        appendLine("package=$packageName uid=${applicationInfo.uid}")
        debugProcesses().forEach { proc ->
            appendLine()
            appendLine("== pid ${proc.pid} ${proc.name} ==")
            val fdDir = File("/proc/${proc.pid}/fd")
            val fds = fdDir.listFiles().orEmpty().sortedBy { it.name.toIntOrNull() ?: Int.MAX_VALUE }
            if (fds.isEmpty()) {
                appendLine("(no readable fd entries)")
            } else {
                fds.take(256).forEach { fd ->
                    val target = runCatching { Os.readlink(fd.absolutePath) }.getOrDefault("unreadable")
                    appendLine("${fd.name.padStart(4)} -> $target")
                }
                if (fds.size > 256) appendLine("... ${fds.size - 256} more fd entries omitted")
            }
        }
    }

    private fun readProcStatus(dir: File): Map<String, String>? =
        runCatching {
            File(dir, "status").readLines().mapNotNull { line ->
                val key = line.substringBefore(':', "").trim()
                if (key.isBlank()) null else key to line.substringAfter(':', "").trim()
            }.toMap()
        }.getOrNull()

    private fun procCmdline(dir: File): String =
        runCatching {
            File(dir, "cmdline").readBytes()
                .toString(Charsets.UTF_8)
                .replace('\u0000', ' ')
                .trim()
        }.getOrDefault("")

    private fun readSmallProcFile(file: File, maxLines: Int): String =
        runCatching { file.readLines().take(maxLines).joinToString("\n") }.getOrDefault("")

    private fun renderStorageMetrics() {
        refreshStorageMetricsAsync()
        val metrics = storageMetrics
        if (metrics == null) {
            addWidget(
                getString(R.string.widget_storage),
                getString(R.string.storage_scanning),
                getString(R.string.detail_storage_scanning),
                detailLines = 4,
            )
            return
        }
        addWidget(
            getString(R.string.widget_storage),
            getString(R.string.storage_total_fmt, formatBytes(metrics.pdockerBytes), formatBytes(metrics.fsTotalBytes)),
            getString(
                R.string.storage_detail_fmt,
                formatBytes(metrics.layersBytes),
                formatBytes(metrics.imageViewBytes),
                formatBytes(metrics.containerPrivateBytes),
                formatBytes(metrics.fsFreeBytes),
            ),
            detailLines = 4,
        ) {
            refreshStorageMetricsAsync(force = true)
        }
    }

    private fun refreshStorageMetricsAsync(force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (storageMetricsScanning) return
        if (!force && storageMetrics != null && now - lastStorageMetricsAt < 30_000L) return
        storageMetricsScanning = true
        thread(isDaemon = true, name = "pdocker-storage-metrics") {
            val layerUsage = diskUsage(layerRoot)
            val imageUsage = diskUsage(imageRoot, excludeInodes = layerUsage.inodeKeys)
            val containerUsage = diskUsage(
                containerRoot,
                excludeInodes = layerUsage.inodeKeys + imageUsage.inodeKeys,
            )
            val pdockerUsage = diskUsage(pdockerHome)
            val metrics = StorageMetrics(
                fsTotalBytes = pdockerHome.totalSpace,
                fsFreeBytes = pdockerHome.freeSpace,
                pdockerBytes = pdockerUsage.bytes,
                layersBytes = layerUsage.bytes,
                imageViewBytes = imageUsage.bytes,
                containerPrivateBytes = containerUsage.bytes,
            )
            ui.post {
                storageMetrics = metrics
                lastStorageMetricsAt = System.currentTimeMillis()
                storageMetricsScanning = false
                if (currentTab == Tab.Overview) renderContent()
            }
        }
    }

    private fun renderDaemonOperations() {
        if (daemonOperations.isEmpty()) return
        addSection(getString(R.string.section_daemon_operations))
        val now = System.currentTimeMillis()
        daemonOperations.take(5).forEach { op ->
            val elapsed = ((now - op.startedAtMs).coerceAtLeast(0L) / 1000L)
            val idle = ((now - op.updatedAtMs).coerceAtLeast(0L) / 1000L)
            val value = getString(R.string.daemon_operation_status_fmt, op.status, elapsed, jobActivityFrame())
            val job = daemonOperationJob(op)
            val detail = listOf(
                getString(R.string.daemon_operation_kind_fmt, op.kind),
                getString(R.string.daemon_operation_not_container),
                op.detail.ifBlank { "-" },
                getString(R.string.daemon_operation_idle_fmt, idle),
                job?.let { getString(R.string.action_open_job_log_fmt, it.title) }.orEmpty(),
            ).filter { it.isNotBlank() }.joinToString("\n")
            addWidget(op.title, value, detail, detailLines = 5, onClick = job?.let { activeJob ->
                { openJobLog(activeJob) }
            })
        }
    }

    private fun daemonOperationJob(op: DaemonOperation): DockerJob? {
        val running = dockerJobs.filter { it.exitCode == null }
        val detail = op.detail.lowercase()
        return running.firstOrNull { job ->
            val haystack = "${job.title} ${job.detail} ${job.command} ${job.progress}".lowercase()
            haystack.contains(op.kind.lowercase()) ||
                detail.isNotBlank() && haystack.contains(detail.take(48))
        } ?: running.firstOrNull {
            it.command.startsWith("engine compose up:") || it.command.startsWith("engine docker build:")
        } ?: dockerJobs.firstOrNull {
            it.command.startsWith("engine compose up:") || it.command.startsWith("engine docker build:")
        }
    }

    private fun reconcileDaemonOperationJobs(ops: List<DaemonOperation>) {
        if (ops.isEmpty()) return
        var changed = false
        ops.forEach { op ->
            val job = daemonOperationJob(op) ?: return@forEach
            if (job.exitCode != null) return@forEach
            val progress = op.detail.ifBlank { op.status }
            if (progress.isNotBlank() && job.progress != progress) {
                job.progress = progress
                changed = true
            }
            val line = "[pdocker] reconnected daemon ${op.kind} ${op.status}: $progress"
            if (job.output.lastOrNull() != line) {
                job.output += line
                while (job.output.size > MAX_JOB_LINES) job.output.removeAt(0)
                appendPersistentJobLog(job.id, terminalRecordText(line))
                changed = true
            }
        }
        if (changed) {
            saveDockerJobs()
            updateLiveJobViews()
        }
    }

    private fun refreshDaemonOperationsAsync() {
        if (daemonOperationsRefreshing) return
        daemonOperationsRefreshing = true
        thread(isDaemon = true, name = "pdocker-daemon-ops") {
            val ops = runCatching {
                val arr = engine.getArray("/system/operations")
                (0 until arr.length()).mapNotNull { index ->
                    val obj = arr.optJSONObject(index) ?: return@mapNotNull null
                    DaemonOperation(
                        id = obj.optString("Id"),
                        kind = obj.optString("Kind", "operation"),
                        title = obj.optString("Title", "operation"),
                        detail = obj.optString("Detail"),
                        status = obj.optString("Status", "running"),
                        startedAtMs = (obj.optDouble("StartedAt", 0.0) * 1000.0).toLong(),
                        updatedAtMs = (obj.optDouble("UpdatedAt", 0.0) * 1000.0).toLong(),
                    )
                }
            }.getOrDefault(emptyList())
            ui.post {
                val changed = ops != daemonOperations
                daemonOperations = ops
                reconcileDaemonOperationJobs(ops)
                daemonOperationsRefreshing = false
                if (changed && currentTab == Tab.Overview) renderContent()
            }
        }
    }

    private fun renderHostEnvironment() {
        refreshHostEnvironmentAsync()
        val env = hostEnvironment
        if (env == null) {
            addWidget(
                getString(R.string.widget_host_environment),
                getString(R.string.host_environment_loading),
                getString(R.string.detail_host_environment_loading),
                detailLines = 5,
            )
            return
        }
        addWidget(
            getString(R.string.widget_host_environment),
            hostEnvironmentSummary(env),
            hostEnvironmentDetails(env),
            detailLines = 8,
        ) {
            refreshHostEnvironmentAsync(force = true)
        }
    }

    private fun refreshHostEnvironmentAsync(force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (hostEnvironmentRefreshing) return
        if (!force && hostEnvironment != null && now - lastHostEnvironmentAt < 30_000L) return
        hostEnvironmentRefreshing = true
        thread(isDaemon = true, name = "pdocker-host-environment") {
            val env = runCatching { engine.getObject("/system/host") }.getOrNull()
            ui.post {
                hostEnvironment = env ?: hostEnvironment
                lastHostEnvironmentAt = System.currentTimeMillis()
                hostEnvironmentRefreshing = false
                if (currentTab == Tab.Overview || currentTab == Tab.Sessions) renderContent()
            }
        }
    }

    private fun hostEnvironmentSummary(env: JSONObject): String {
        val host = env.optJSONObject("Host")
        val runtime = env.optJSONObject("Runtime")
        val gpu = env.optJSONObject("Gpu")
        val machine = host?.optString("Machine").orEmpty().ifBlank { "-" }
        val backend = runtime?.optString("Backend").orEmpty().ifBlank { "-" }
        val vulkan = gpu?.optString("VulkanIcdKind").orEmpty().ifBlank { "vulkan-icd" }
        val ready = if (gpu?.optBoolean("VulkanIcdReady", false) == true) "ready" else "probe"
        return getString(R.string.host_environment_summary_fmt, machine, backend, vulkan, ready)
    }

    private fun hostEnvironmentDetails(env: JSONObject): String {
        val host = env.optJSONObject("Host")
        val hardware = env.optJSONObject("Hardware")
        val software = env.optJSONObject("Software")
        val runtime = env.optJSONObject("Runtime")
        val gpu = env.optJSONObject("Gpu")
        val frameworks = env.optJSONObject("Frameworks")
        val vulkan = frameworks?.optJSONObject("Vulkan")
        val eglGles = frameworks?.optJSONObject("EglOpenGles")
        val opencl = frameworks?.optJSONObject("OpenCL")
        val nnapi = frameworks?.optJSONObject("NnApi")
        val ahb = frameworks?.optJSONObject("AndroidHardwareBuffer")
        val mediaCodec = frameworks?.optJSONObject("MediaCodec")
        val paths = env.optJSONObject("Paths")
        val helperStatus = listOf("DirectExecutor", "GpuExecutor", "GpuShim", "VulkanIcd")
            .map { key ->
                val obj = paths?.optJSONObject(key)
                "$key=${if (obj?.optBoolean("Exists", false) == true) "ok" else "missing"}"
            }
            .joinToString(" ")
        return listOf(
            getString(R.string.host_environment_kernel_fmt, host?.optString("Release").orEmpty().ifBlank { "-" }),
            getString(
                R.string.host_environment_hardware_fmt,
                hardware?.optInt("ProcessorCount", 0) ?: 0,
                formatBytes(hardware?.optLong("MemTotal", 0L) ?: 0L),
                formatBytes(hardware?.optLong("MemAvailable", 0L) ?: 0L),
            ),
            getString(R.string.host_environment_python_fmt, software?.optString("Python").orEmpty().ifBlank { "-" }),
            getString(
                R.string.host_environment_runtime_fmt,
                runtime?.optString("Driver").orEmpty().ifBlank { "-" },
                runtime?.optString("Platform").orEmpty().ifBlank { "-" },
                runtime?.optString("DockerApiVersion").orEmpty().ifBlank { "-" },
            ),
            getString(
                R.string.host_environment_gpu_fmt,
                gpu?.optString("CommandApi").orEmpty().ifBlank { "-" },
                if (gpu?.optBoolean("ExecutorAvailable", false) == true) "yes" else "no",
            ),
            getString(
                R.string.host_environment_vulkan_fmt,
                vulkan?.optString("ApiVersion").orEmpty().ifBlank { gpu?.optString("VulkanApiVersion").orEmpty().ifBlank { "-" } },
                vulkan?.optString("IcdKind").orEmpty().ifBlank { gpu?.optString("VulkanIcdKind").orEmpty().ifBlank { "-" } },
                if (vulkan?.optBoolean("IcdReady", false) == true) "ready" else "probe",
            ),
            getString(
                R.string.host_environment_gles_fmt,
                eglGles?.optString("ComputeApi").orEmpty().ifBlank { "OpenGL ES" },
                if (eglGles?.optBoolean("EglAvailable", false) == true) "yes" else "no",
                if (eglGles?.optBoolean("GlesAvailable", false) == true) "yes" else "no",
            ),
            getString(
                R.string.host_environment_opencl_fmt,
                openclStatus(opencl),
                if (opencl?.optBoolean("IcdReady", false) == true) "ready" else "probe",
            ),
            getString(
                R.string.host_environment_accel_fmt,
                if (nnapi?.optBoolean("RuntimeAvailable", false) == true) "yes" else "no",
                if (ahb?.optJSONObject("Library")?.optBoolean("Available", false) == true) "yes" else "no",
                if (mediaCodec?.optJSONObject("Library")?.optBoolean("Available", false) == true) "yes" else "no",
            ),
            helperStatus,
            documentsStorageStatusLine(),
        ).joinToString("\n")
    }

    private fun openclStatus(opencl: JSONObject?): String {
        if (opencl == null) return "missing"
        val api = opencl.optString("ApiVersion").ifBlank { "api unknown" }
        val loader = opencl.optJSONObject("Loader")
        val loaderText = if (loader?.optBoolean("Available", false) == true) {
            loader.optString("Path").ifBlank { "loader available" }
        } else {
            "loader missing"
        }
        return "$api, $loaderText"
    }

    private fun diskUsage(root: File, excludeInodes: Set<String> = emptySet()): DiskUsage {
        if (!root.exists()) return DiskUsage(0L, emptySet())
        var bytes = 0L
        val seen = HashSet<String>()
        runCatching {
            root.walkTopDown().forEach { file ->
                val stat = runCatching { Os.lstat(file.absolutePath) }.getOrNull() ?: return@forEach
                val key = "${stat.st_dev}:${stat.st_ino}"
                if (key in excludeInodes || !seen.add(key)) return@forEach
                bytes += stat.st_blocks * 512L
            }
        }
        return DiskUsage(bytes, seen)
    }

    private fun startDaemon() {
        val intent = Intent(this, PdockerdService::class.java)
            .setAction(PdockerdService.ACTION_START)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        bindPdockerdService()
        status.text = getString(R.string.status_starting)
    }

    private fun bindPdockerdService() {
        if (pdockerdServiceBound) return
        val intent = Intent(this, PdockerdService::class.java)
            .setAction(PdockerdService.ACTION_START)
        pdockerdServiceBound = runCatching {
            bindService(
                intent,
                pdockerdServiceConnection,
                Context.BIND_AUTO_CREATE or Context.BIND_IMPORTANT,
            )
        }.getOrDefault(false)
    }

    private fun unbindPdockerdService() {
        if (!pdockerdServiceBound) return
        runCatching { unbindService(pdockerdServiceConnection) }
        pdockerdServiceBound = false
    }

    private fun ensureDaemonStarted() {
        if (File(pdockerHome, "pdockerd.sock").exists()) return
        val now = System.currentTimeMillis()
        if (now - lastDaemonStartAttemptAt < 5000L) return
        lastDaemonStartAttemptAt = now
        startDaemon()
    }

    private fun runAndroidGpuBench() {
        val title = getString(R.string.action_run_gpu_bench)
        val group = getString(R.string.section_diagnostics)
        status.text = getString(R.string.status_gpu_bench_running)
        thread(isDaemon = true, name = "android-gpu-bench") {
            val output = runCatching { AndroidGpuBench.run(this) }
                .getOrElse { getString(R.string.engine_operation_failed_fmt, it.message.orEmpty()) }
            ui.post {
                status.text = getString(R.string.status_gpu_bench_done)
                openTextTool(group, title, output)
            }
        }
    }

    private fun waitForEngine(timeoutMs: Long = 90_000): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            runCatching {
                val resp = engine.request("GET", "/_ping")
                if (resp.status == 200 && resp.text.trim() == "OK") return true
            }
            Thread.sleep(300)
        }
        return false
    }

    private fun runContainerAction(group: String, title: String, block: DockerEngineClient.() -> String) {
        runEngineAction(title, group, block)
    }

    private fun runEngineAction(title: String, group: String, block: DockerEngineClient.() -> String) {
        runEngineJob(title, group, "engine action: $title") { _ -> block() }
    }

    private fun runEngineJob(
        title: String,
        group: String,
        command: String,
        block: DockerEngineClient.((String) -> Unit) -> String,
    ) {
        startDaemon()
        status.text = getString(R.string.status_starting)
        val jobId = "job-" + System.currentTimeMillis().toString(36)
        val key = "engine-job:$jobId:$group:$title"
        val job = DockerJob(
            id = jobId,
            title = title,
            detail = group,
            command = command,
            group = group,
            toolKey = key,
            status = getString(R.string.job_running),
        )
        dockerJobs.add(0, job)
        trimDockerJobs()
        appendPersistentJobLog(job.id, jobTerminalPrelude(job))
        saveDockerJobs()
        renderContent()
        openLiveJobLog(job, switchTo = true)
        thread(isDaemon = true, name = "engine-action") {
            val retainOutput = !command.startsWith("engine compose up:") &&
                !command.startsWith("engine docker build:")
            val output = StringBuilder()
            val result = runCatching {
                if (!waitForEngine()) error(getString(R.string.status_socket_absent))
                engine.block { line ->
                    if (retainOutput) output.appendLine(line)
                    appendEngineJobOutput(job.id, line)
                }
            }
            val exitCode = if (result.isSuccess) 0 else 1
            val finalOutput = result.getOrElse {
                getString(R.string.engine_operation_failed_fmt, summarizeEngineFailure(it.message.orEmpty()))
            }
            val finishOutput = if (command.startsWith("engine compose up:") || command.startsWith("engine docker build:")) {
                if (exitCode == 0) "" else finalOutput
            } else {
                appendUniqueLines(output, finalOutput)
                output.toString()
            }
            ui.post {
                finishEngineJob(job.id, exitCode, finishOutput)
                val finished = dockerJobs.firstOrNull { it.id == job.id }
                if (finished != null) handleEngineJobFinished(finished)
                refreshContainerSnapshotAsync(force = true)
                refreshStorageMetricsAsync(force = true)
                renderContent()
                refreshStatus()
            }
        }
    }

    private fun summarizeEngineFailure(message: String): String {
        val lines = message.lineSequence()
            .map { cleanTerminalLine(it) }
            .filter { it.isNotBlank() }
            .toList()
        val important = lines.lastOrNull {
            it.contains("ERROR:", ignoreCase = true) ||
                it.contains("No space left", ignoreCase = true) ||
                it.contains("failed", ignoreCase = true)
        }
        return important ?: lines.lastOrNull().orEmpty().ifBlank { message.take(500) }
    }

    private fun runImageBuild(dir: File, title: String) {
        runEngineJob(title, workspaceGroup(), "engine docker build: ${dir.absolutePath}") { emit ->
            emit("Service ${dir.name} Building")
            buildImageStreaming(dir, "local/${dir.name}:latest") { line -> emit(line) }
        }
    }

    private fun runPruneBuildCache() {
        runEngineAction(getString(R.string.action_prune_build_cache), getString(R.string.section_images)) {
            pruneBuildCache()
        }
    }

    private fun confirmDeleteImage(imageRef: String, cleanCache: Boolean) {
        val title = if (cleanCache) {
            getString(R.string.action_clean_image_cache_fmt, displayImageRef(imageRef))
        } else {
            getString(R.string.action_delete_image_fmt, displayImageRef(imageRef))
        }
        val message = if (cleanCache) {
            getString(R.string.confirm_clean_image_cache_fmt, imageRef)
        } else {
            getString(R.string.confirm_delete_image_fmt, imageRef)
        }
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(message)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                runDeleteImage(imageRef, cleanCache)
            }
            .show()
    }

    private fun runDeleteImage(imageRef: String, cleanCache: Boolean) {
        val title = if (cleanCache) {
            getString(R.string.action_clean_image_cache_fmt, displayImageRef(imageRef))
        } else {
            getString(R.string.action_delete_image_fmt, displayImageRef(imageRef))
        }
        runEngineJob(title, getString(R.string.section_images), "engine image delete: $imageRef") { emit ->
            emit("Image ${displayImageRef(imageRef)} Removing")
            val deleteResult = deleteImage(imageRef)
            emit(deleteResult.ifBlank { "Image ${displayImageRef(imageRef)} removed" })
            if (cleanCache) {
                emit("Build cache Pruning")
                emit(pruneBuildCache())
            }
            refreshStorageMetricsAsync(force = true)
            "Image ${displayImageRef(imageRef)} removed"
        }
    }

    private fun runComposeUp(dir: File, title: String) {
        runEngineJob(title, dir.name, "engine compose up: ${dir.absolutePath}") { emit ->
            val services = parseComposeServices(dir)
            if (services.isEmpty()) error("compose file has no services")
            val out = StringBuilder()
            services.forEach { service ->
                var image = service.image.ifBlank { "local/${dir.name}-${service.name}:latest" }
                val context = service.buildContext
                if (!context.isNullOrBlank()) {
                    val contextDir = File(dir, context).canonicalFile
                    val line = "Service ${service.name} Building"
                    emit(line)
                    out.appendLine(line)
                    buildImageStreaming(contextDir, image) { buildLine ->
                        emit(buildLine)
                    }
                }
                val containerName = service.containerName.ifBlank { "${dir.name}-${service.name}-1" }
                runCatching {
                    request("DELETE", "/containers/${DockerEngineClient.encodePath(containerName)}?force=1")
                }
                emit("Container $containerName Creating")
                out.appendLine("Container $containerName Creating")
                val id = createContainer(containerName, service.toContainerConfig(image, dir, projectIdFor(dir)))
                emit("Container $containerName Starting")
                out.appendLine("Container $containerName Starting")
                val started = runCatching { post("/containers/${DockerEngineClient.encodePath(id)}/start") }
                if (started.isSuccess) {
                    emit("Container $containerName Started")
                    out.appendLine("Container $containerName Started")
                    composeServiceUrls(service).forEach { (label, url) ->
                        val line = "Service URL $label $url"
                        emit(line)
                        out.appendLine(line)
                    }
                } else {
                    val message = started.exceptionOrNull()?.message.orEmpty()
                    if (!isRuntimeBackendBlocked(message)) {
                        throw started.exceptionOrNull() ?: IllegalStateException(message)
                    }
                    emit("Container $containerName Prepared (runtime blocked)")
                    out.appendLine("Container $containerName Prepared (runtime blocked)")
                }
            }
            out.append('\n').append(formatContainers(getArray("/containers/json?all=1")))
            out.toString()
        }
    }

    private fun appendUniqueLines(output: StringBuilder, text: String) {
        val existing = output.lineSequence().map { it.trim() }.filter { it.isNotBlank() }.toMutableSet()
        text.lineSequence()
            .map { it.trimEnd() }
            .filter { it.isNotBlank() }
            .forEach { line ->
                if (existing.add(line.trim())) output.appendLine(line)
            }
    }

    private fun isRuntimeBackendBlocked(message: String): Boolean {
        val lower = message.lowercase()
        return listOf(
            "android execution backend is unavailable",
            "bundled proot backend crashed",
            "no-proot/direct android execution backend",
            "cannot execute container processes yet",
            "will not start a fake listener",
            "runtime preflight failed before running",
            "run skipped because the android execution backend is unavailable",
        ).any { it in lower }
    }

    private fun parseComposeServices(dir: File): List<ComposeService> {
        val file = listOf("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
            .map { File(dir, it) }
            .firstOrNull { it.isFile }
            ?: return emptyList()
        val lines = file.readLines()
        val composeEnv = composeEnvironment(dir)
        val serviceLinks = parseComposeHeaderServiceLinks(lines)
        val services = mutableListOf<ComposeService>()
        var inServices = false
        var current: ComposeService? = null
        var blockKey: String? = null
        var deployBlockKey: String? = null
        var deployResourceKey: String? = null
        lines.forEach { raw ->
            val line = raw.substringBefore('#').trimEnd()
            if (line.isBlank()) return@forEach
            val indent = raw.takeWhile { it == ' ' }.length
            val trimmed = line.trim()
            if (indent == 0) {
                inServices = trimmed == "services:"
                current = null
                blockKey = null
                deployBlockKey = null
                deployResourceKey = null
                return@forEach
            }
            if (!inServices) return@forEach
            if (indent == 2 && trimmed.endsWith(":")) {
                current = ComposeService(
                    name = trimmed.removeSuffix(":"),
                    serviceLinks = serviceLinks.toMutableList(),
                ).also { services += it }
                blockKey = null
                deployBlockKey = null
                deployResourceKey = null
                return@forEach
            }
            val svc = current ?: return@forEach
            if (indent == 4) {
                val key = trimmed.substringBefore(':')
                val value = trimmed.substringAfter(':', "").trim()
                blockKey = if (value.isBlank()) key else null
                if (key != "deploy") {
                    deployBlockKey = null
                    deployResourceKey = null
                }
                when (key) {
                    "image" -> svc.image = composeValue(value, composeEnv)
                    "container_name" -> svc.containerName = composeValue(value, composeEnv)
                    "working_dir" -> svc.workingDir = composeValue(value, composeEnv)
                    "command" -> svc.command = composeCommand(value, composeEnv)
                    "gpus" -> svc.gpus = composeValue(value, composeEnv)
                    "mem_limit" -> svc.memLimit = composeValue(value, composeEnv)
                    "memswap_limit" -> svc.memSwapLimit = composeValue(value, composeEnv)
                    "build" -> if (value.isNotBlank()) svc.buildContext = composeValue(value, composeEnv)
                    "depends_on" -> if (value.isNotBlank()) svc.dependsOn += composeStringList(value, composeEnv)
                    "labels" -> if (value.isNotBlank()) svc.labels.putAll(composeInlineMapOrList(value, composeEnv))
                    "healthcheck" -> svc.hasHealthcheck = true
                }
                return@forEach
            }
            if (indent >= 6) {
                when (blockKey) {
                    "build" -> {
                        val key = trimmed.substringBefore(':')
                        val value = trimmed.substringAfter(':', "").trim()
                        if (key == "context") svc.buildContext = composeValue(value, composeEnv)
                    }
                    "environment" -> parseComposeMapOrList(trimmed, composeEnv)?.let { (k, v) -> svc.environment[k] = v }
                    "labels" -> parseComposeMapOrList(trimmed, composeEnv)?.let { (k, v) -> svc.labels[k] = v }
                    "ports" -> parseComposeListValue(trimmed, composeEnv)?.let { svc.ports += it }
                    "volumes" -> parseComposeListValue(trimmed, composeEnv)?.let { svc.volumes += it }
                    "depends_on" -> {
                        parseComposeListValue(trimmed, composeEnv)?.let { svc.dependsOn += it }
                        parseComposeMapOrList(trimmed, composeEnv)?.first?.let { svc.dependsOn += it }
                    }
                    "deploy" -> {
                        val key = trimmed.substringBefore(':')
                        val value = trimmed.substringAfter(':', "").trim()
                        when {
                            indent == 6 -> {
                                deployBlockKey = if (key == "resources" && value.isBlank()) "resources" else null
                                deployResourceKey = null
                            }
                            indent == 8 && deployBlockKey == "resources" -> {
                                deployResourceKey = if (key == "limits" && value.isBlank()) "limits" else null
                            }
                            indent >= 10 && deployBlockKey == "resources" && deployResourceKey == "limits" && key == "memory" -> {
                                svc.deployMemoryLimit = composeValue(value, composeEnv)
                            }
                        }
                    }
                }
            }
        }
        return services
    }

    private fun parseComposeHeaderServiceLinks(lines: List<String>): List<ComposeServiceLink> {
        val links = mutableListOf<ComposeServiceLink>()
        val autoOpenLabels = mutableSetOf<String>()
        for (raw in lines) {
            val trimmed = raw.trim()
            if (trimmed.isBlank()) continue
            if (!trimmed.startsWith("#")) break
            val comment = trimmed.removePrefix("#").trim()
            comment.removePrefix("pdocker.auto-open:")
                .takeIf { it != comment }
                ?.trim()
                ?.takeIf { it.isNotBlank() }
                ?.let { autoOpenLabels += it }
            val rest = comment
                .removePrefix("pdocker.service-url:")
                .takeIf { it != comment }
                ?.trim()
                ?: continue
            parseComposeServiceLink(rest)?.let { links += it }
        }
        return links.distinct().map { link ->
            if (link.label in autoOpenLabels) link.copy(autoOpen = true) else link
        }
    }

    private fun parseComposeServiceLink(value: String): ComposeServiceLink? {
        val left = value.substringBefore('=', "").trim()
        val right = value.substringAfter('=', "").trim()
        if (left.isBlank() || right.isBlank()) return null
        val port = left.toIntOrNull()
        return if (port != null) {
            if (port !in 1..65535) return null
            ComposeServiceLink(port, right, null)
        } else {
            if (!isServiceUri(right)) return null
            ComposeServiceLink(null, left, right)
        }
    }

    private fun projectIdFor(projectDir: File): String =
        runCatching { projectDir.canonicalFile.absolutePath }
            .getOrDefault(projectDir.absolutePath)

    private fun ComposeService.toContainerConfig(imageName: String, projectDir: File, projectId: String): JSONObject {
        val exposedPorts = JSONObject()
        val portBindings = JSONObject()
        ports.forEach { spec ->
            val parts = spec.split(":")
            val container = (parts.getOrNull(1) ?: parts.firstOrNull()).orEmpty()
            if (container.isNotBlank()) {
                val key = if (container.contains("/")) container else "$container/tcp"
                exposedPorts.put(key, JSONObject())
                if (parts.size >= 2) {
                    portBindings.put(key, JSONArray().put(JSONObject().put("HostPort", parts[0])))
                }
            }
        }
        val binds = JSONArray()
        volumes.forEach { spec ->
            val parts = spec.split(":")
            if (parts.size >= 2) {
                val hostPath = parts[0]
                val host = if (hostPath.startsWith("/")) File(hostPath).absolutePath else File(projectDir, hostPath).absolutePath
                val guest = parts[1]
                val mode = parts.getOrNull(2)?.let { ":$it" }.orEmpty()
                binds.put("$host:$guest$mode")
            }
        }
        val hostConfig = JSONObject()
            .put("Binds", binds)
            .put("PortBindings", portBindings)
        val memoryLimitBytes = composeMemoryLimitBytes()
        memoryLimitBytes?.let { hostConfig.put("Memory", it) }
        parseMemoryBytes(memSwapLimit)?.let { hostConfig.put("MemorySwap", it) }
        if (gpus.isNotBlank() && gpus != "null") {
            hostConfig.put(
                "DeviceRequests",
                JSONArray().put(
                    JSONObject()
                        .put("Driver", "pdocker-gpu")
                        .put("Count", -1)
                        .put("Capabilities", JSONArray().put(JSONArray().put("gpu")))
                        .put(
                            "Options",
                            JSONObject()
                                .put("pdocker.gpu", "vulkan")
                                .put("pdocker.cuda", "compat")
                                .put("pdocker.opencl", "opencl"),
                        ),
                ),
            )
        }
        val configLabels = JSONObject()
        labels.forEach { (key, value) -> configLabels.put(key, value) }
        val pagerMode = composeMemoryPagerMode()
        if (pagerMode.lowercase() == "managed") {
            configLabels.put("io.pdocker.memory-pager", "managed")
            memoryLimitBytes?.let { configLabels.put("io.pdocker.memory-pager.limit-bytes", it.toString()) }
            parseMemoryBytes(environment["PDOCKER_MEMORY_PAGER_MAX_BYTES"].orEmpty())
                ?.let { configLabels.put("io.pdocker.memory-pager.max-bytes", it.toString()) }
        }
        configLabels
            .put(PDOCKER_PROJECT_ID_LABEL, projectId)
            .put(PDOCKER_PROJECT_DIR_LABEL, projectDir.absolutePath)
            .put(PDOCKER_PROJECT_NAME_LABEL, projectDir.name)
            .put(PDOCKER_COMPOSE_SERVICE_LABEL, name)
            .put("com.docker.compose.project", projectDir.name)
            .put("com.docker.compose.service", name)
            .put("com.docker.compose.oneoff", "False")
        serviceLinks.forEachIndexed { index, link ->
            if (link.port != null) {
                configLabels.put("$PDOCKER_SERVICE_URL_LABEL_PREFIX${link.port}", link.label)
            } else if (!link.url.isNullOrBlank()) {
                configLabels.put("${PDOCKER_SERVICE_URL_LABEL_PREFIX}url.$index", "${link.label}=${link.url}")
            }
        }
        return JSONObject()
            .put("Image", imageName)
            .put("Cmd", JSONArray(command))
            .put("WorkingDir", workingDir)
            .put("Env", JSONArray(environment.map { (k, v) -> "$k=$v" }))
            .put("ExposedPorts", exposedPorts)
            .put("Labels", configLabels)
            .put("HostConfig", hostConfig)
    }

    private fun ComposeService.composeMemoryLimitBytes(): Long? =
        parseMemoryBytes(memLimit.ifBlank { deployMemoryLimit })

    private fun ComposeService.composeMemoryPagerMode(): String =
        labels["io.pdocker.memory-pager"]
            ?: labels["pdocker.memory-pager"]
            ?: environment["PDOCKER_MEMORY_PAGER"]
            ?: ""

    private fun parseMemoryBytes(value: String): Long? {
        val cleaned = value.trim().trim('"', '\'')
        if (cleaned.isBlank()) return null
        val match = Regex("""^([0-9]+(?:\.[0-9]+)?)([kmgt]?b?)?$""", RegexOption.IGNORE_CASE)
            .matchEntire(cleaned)
            ?: return null
        val amount = match.groupValues[1].toDoubleOrNull() ?: return null
        val multiplier = when (match.groupValues.getOrNull(2).orEmpty().lowercase()) {
            "", "b" -> 1.0
            "k", "kb" -> 1024.0
            "m", "mb" -> 1024.0 * 1024.0
            "g", "gb" -> 1024.0 * 1024.0 * 1024.0
            "t", "tb" -> 1024.0 * 1024.0 * 1024.0 * 1024.0
            else -> return null
        }
        val bytes = amount * multiplier
        if (!bytes.isFinite() || bytes <= 0.0 || bytes > Long.MAX_VALUE.toDouble()) return null
        return bytes.toLong()
    }

    private fun composeInlineMapOrList(value: String, env: Map<String, String>): Map<String, String> {
        val cleaned = composeValue(value, env)
        if (cleaned.isBlank()) return emptyMap()
        if (cleaned.startsWith("[") && cleaned.endsWith("]")) {
            return runCatching {
                val arr = JSONArray(cleaned)
                (0 until arr.length()).mapNotNull { index ->
                    parseComposeMapOrList("- ${arr.optString(index)}", env)
                }.toMap()
            }.getOrDefault(emptyMap())
        }
        if (cleaned.startsWith("{") && cleaned.endsWith("}")) {
            return runCatching {
                val obj = JSONObject(cleaned)
                obj.keys().asSequence().associateWith { key -> obj.optString(key) }
            }.getOrDefault(emptyMap())
        }
        return parseComposeMapOrList(cleaned, env)?.let { mapOf(it) }.orEmpty()
    }

    private fun parseComposeMapOrList(line: String, env: Map<String, String>): Pair<String, String>? {
        val item = line.removePrefix("-").trim()
        if ("=" in item) {
            val k = item.substringBefore('=')
            return k to composeValue(item.substringAfter('='), env)
        }
        if (":" in item) {
            val k = item.substringBefore(':').trim()
            return k to composeValue(item.substringAfter(':').trim(), env)
        }
        return null
    }

    private fun parseComposeListValue(line: String, env: Map<String, String>): String? =
        line.takeIf { it.trimStart().startsWith("-") }?.trim()?.removePrefix("-")?.trim()?.let { composeValue(it, env) }

    private fun composeStringList(value: String, env: Map<String, String>): List<String> {
        val cleaned = composeValue(value, env)
        if (cleaned.startsWith("[") && cleaned.endsWith("]")) {
            return runCatching {
                val arr = JSONArray(cleaned)
                (0 until arr.length()).mapNotNull { arr.optString(it).takeIf { item -> item.isNotBlank() } }
            }.getOrElse {
                cleaned.trim('[', ']').split(',').map { composeValue(it, env) }.filter { it.isNotBlank() }
            }
        }
        return cleaned.split(',').map { composeValue(it, env) }.filter { it.isNotBlank() }
    }

    private fun composeCommand(value: String, env: Map<String, String>): List<String> {
        val cleaned = composeValue(value, env)
        if (cleaned.startsWith("[") && cleaned.endsWith("]")) {
            return runCatching {
                val arr = JSONArray(cleaned)
                (0 until arr.length()).map { arr.getString(it) }
            }.getOrElse { emptyList() }
        }
        return if (cleaned.isBlank()) emptyList() else listOf("/bin/sh", "-lc", cleaned)
    }

    private fun composeEnvironment(projectDir: File): Map<String, String> {
        val env = linkedMapOf<String, String>()
        listOf(File(projectRoot, ".pdocker-common.env"), File(projectDir, ".env")).forEach fileLoop@ { file ->
            if (!file.isFile) return@fileLoop
            file.readLines().forEach lineLoop@ { raw ->
                val line = raw.trim()
                if (line.isBlank() || line.startsWith("#") || "=" !in line) return@lineLoop
                val key = line.substringBefore('=').trim()
                if (!key.matches(Regex("[A-Za-z_][A-Za-z0-9_]*"))) return@lineLoop
                env[key] = envFileValue(line.substringAfter('='))
            }
        }
        if ("PDOCKER_DOCUMENTS_HOST" !in env) env["PDOCKER_DOCUMENTS_HOST"] = documentsHostPath()
        if ("PDOCKER_DOCUMENTS_MOUNT" !in env) env["PDOCKER_DOCUMENTS_MOUNT"] = PDOCKER_DOCUMENTS_MOUNT
        if ("PDOCKER_DOCUMENTS_ROOT" !in env) env["PDOCKER_DOCUMENTS_ROOT"] = documentsHostPath()
        val metadata = documentsTreeMetadata()
        if ("PDOCKER_DOCUMENTS_ACCESS" !in env) env["PDOCKER_DOCUMENTS_ACCESS"] = metadata.writeAccess.envValue
        if ("PDOCKER_DOCUMENTS_MEDIATOR" !in env) {
            env["PDOCKER_DOCUMENTS_MEDIATOR"] = if (metadata.writeAccess == DocumentsWriteAccess.SafMediated) "android-saf" else "direct-path"
        }
        if ("PDOCKER_DOCUMENTS_DIRECT_HOST" !in env) env["PDOCKER_DOCUMENTS_DIRECT_HOST"] = metadata.directHostPath
        if ("PDOCKER_DOCUMENTS_MEDIATED_HOST" !in env) {
            env["PDOCKER_DOCUMENTS_MEDIATED_HOST"] = if (metadata.writeAccess == DocumentsWriteAccess.SafMediated) metadata.activeHostPath else ""
        }
        if ("PDOCKER_DOCUMENTS_SAF_MIRROR_HOST" !in env) {
            env["PDOCKER_DOCUMENTS_SAF_MIRROR_HOST"] = if (metadata.writeAccess == DocumentsWriteAccess.SafMediated) metadata.activeHostPath else ""
        }
        if ("PDOCKER_DOCUMENTS_SAF_SIDECAR_HOST" !in env) {
            env["PDOCKER_DOCUMENTS_SAF_SIDECAR_HOST"] = if (metadata.writeAccess == DocumentsWriteAccess.SafMediated) safDocumentsSidecarPath() else ""
        }
        if ("PDOCKER_PROJECTS_HOST" !in env) env["PDOCKER_PROJECTS_HOST"] = projectRoot.absolutePath
        if ("PDOCKER_VOLUME_ROOT" !in env) env["PDOCKER_VOLUME_ROOT"] = documentsVolumeRootPath()
        if ("PDOCKER_PROJECT_VOLUME_HOST" !in env) env["PDOCKER_PROJECT_VOLUME_HOST"] = projectVolumeHostPath(projectDir.name)
        if ("PDOCKER_SHARED_DOCUMENTS_HOST" !in env) env["PDOCKER_SHARED_DOCUMENTS_HOST"] = sharedDocumentsHostPath()
        if ("PDOCKER_SHARED_DOCUMENTS_MOUNT" !in env) env["PDOCKER_SHARED_DOCUMENTS_MOUNT"] = "/shared"
        if ("PDOCKER_FAST_WORKSPACE_HOST" !in env) env["PDOCKER_FAST_WORKSPACE_HOST"] = fastWorkspaceHostPath(projectDir.name)
        if ("PDOCKER_DEV_STATE_HOST" !in env) env["PDOCKER_DEV_STATE_HOST"] = fastStateHostPath(projectDir.name, "dev")
        if ("PDOCKER_MODEL_HOST" !in env) env["PDOCKER_MODEL_HOST"] = modelHostPath(projectDir.name)
        if ("PDOCKER_APP_HOME_HOST" !in env) env["PDOCKER_APP_HOME_HOST"] = pdockerHome.absolutePath
        if ("PDOCKER_PROJECT_NAME" !in env) env["PDOCKER_PROJECT_NAME"] = projectDir.name
        return env
    }

    private fun envFileValue(raw: String): String {
        val value = raw.trim()
        if (value.length >= 2 && value.first() == '"' && value.last() == '"') {
            return value.substring(1, value.lastIndex)
                .replace("\\\"", "\"")
                .replace("\\\\", "\\")
        }
        if (value.length >= 2 && value.first() == '\'' && value.last() == '\'') {
            return value.substring(1, value.lastIndex)
        }
        return value.substringBefore(" #").trim()
    }

    private fun composeValue(value: String, env: Map<String, String>): String {
        var out = value.trim().trim('"', '\'')
        out = Regex("""\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}""").replace(out) {
            env[it.groupValues[1]]?.takeIf { value -> value.isNotEmpty() } ?: it.groupValues[2]
        }
        out = Regex("""\$\{([A-Za-z_][A-Za-z0-9_]*)-([^}]*)\}""").replace(out) {
            env[it.groupValues[1]] ?: it.groupValues[2]
        }
        out = Regex("""\$\{([A-Za-z_][A-Za-z0-9_]*)\}""").replace(out) {
            env[it.groupValues[1]].orEmpty()
        }
        out = Regex("""\$([A-Za-z_][A-Za-z0-9_]*)""").replace(out) {
            env[it.groupValues[1]].orEmpty()
        }
        return out
    }

    private fun formatContainers(containers: JSONArray): String {
        val columns = listOf(
            "CONTAINER ID" to 12,
            "IMAGE" to 25,
            "STATUS" to 13,
            "PORTS" to 30,
        )
        fun cell(value: String, width: Int): String {
            val compact = value.replace('\n', ' ').replace('\r', ' ')
            val clipped = if (compact.length > width) compact.take(width - 3) + "..." else compact
            return clipped.padEnd(width)
        }
        val separator = "  "
        val header = columns.joinToString(separator) { (title, width) -> cell(title, width) } + separator + "NAMES"
        if (containers.length() == 0) return "$header\n"
        val lines = mutableListOf(header)
        val rows = (0 until containers.length())
            .mapNotNull { containers.optJSONObject(it) }
            .sortedWith(containerSnapshotComparator())
        rows.forEach { obj ->
            val names = obj.optJSONArray("Names")
            val name = names?.optString(0).orEmpty().trim('/').ifBlank { obj.optString("Id").take(12) }
            lines += listOf(
                cell(obj.optString("Id").take(12), columns[0].second),
                cell(obj.optString("Image"), columns[1].second),
                cell(obj.optString("Status"), columns[2].second),
                cell(formatPortsForPs(obj.optJSONArray("Ports")), columns[3].second),
                name,
            ).joinToString(separator)
        }
        return lines.joinToString("\n")
    }

    private fun containerSnapshotComparator(): Comparator<JSONObject> =
        compareByDescending<JSONObject> { it.optLong("Created", 0L) }
            .thenBy { it.optJSONArray("Names")?.optString(0).orEmpty().trim('/') }
            .thenBy { it.optString("Id") }

    private fun formatPortsForPs(ports: JSONArray?): String {
        if (ports == null || ports.length() == 0) return ""
        return (0 until ports.length()).mapNotNull { i ->
            val port = ports.optJSONObject(i) ?: return@mapNotNull null
            val privatePort = port.optInt("PrivatePort", -1).takeIf { it > 0 } ?: return@mapNotNull null
            val type = port.optString("Type").ifBlank { "tcp" }
            val publicPort = port.optInt("PublicPort", -1)
            val ip = port.optString("IP").ifBlank { "127.0.0.1" }
            if (publicPort > 0) "$ip:$publicPort->$privatePort/$type" else "$privatePort/$type"
        }.joinToString(", ")
    }

    private fun openTextTool(group: String, title: String, text: String) {
        val key = "engine:$group:$title"
        val viewText = truncateTextTool(text)
        val existing = toolTabs.indexOfFirst { it.key == key }
        if (existing >= 0) {
            val tab = toolTabs[existing]
            ((tab.view as? ScrollView)?.getChildAt(0) as? TextView)?.text = viewText
            switchTool(existing)
            return
        }
        val view = ScrollView(this).apply {
            addView(TextView(this@MainActivity).apply {
                this.text = viewText
                textSize = 12f
                typeface = Typeface.MONOSPACE
                setTextIsSelectable(true)
                setPadding(18, 18, 18, 18)
            })
        }
        toolTabs += ToolTab(group, title, ToolKind.Editor, view, key = key)
        switchTool(toolTabs.lastIndex)
    }

    private fun openTextToolAsync(group: String, title: String, producer: () -> String) {
        status.text = title
        thread(isDaemon = true, name = "pdocker-text-tool") {
            val text = runCatching { producer() }
                .getOrElse { getString(R.string.engine_operation_failed_fmt, it.message.orEmpty()) }
            ui.post { openTextTool(group, title, text) }
        }
    }

    private fun truncateTextTool(text: String): String {
        val bytes = text.toByteArray(Charsets.UTF_8)
        if (bytes.size <= MAX_TEXT_TOOL_VIEW_BYTES) return text
        val start = bytes.size - MAX_TEXT_TOOL_VIEW_BYTES
        return "[pdocker] text truncated to last ${MAX_TEXT_TOOL_VIEW_BYTES / 1024} KiB\n" +
            bytes.copyOfRange(start, bytes.size).toString(Charsets.UTF_8)
    }

    private fun handleAutomationIntent(intent: Intent?) {
        val action = intent?.action ?: return
        val debuggable = (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        if (!debuggable) return
        when (action) {
            ACTION_SMOKE_START -> startDaemon()
            ACTION_SMOKE_GPU_BENCH -> runAndroidGpuBench()
            ACTION_SMOKE_COMPOSE_UP -> {
                val project = intent.getStringExtra("project").orEmpty().ifBlank { "default" }
                val dir = File(projectRoot, project)
                ui.post { runComposeUp(dir, getString(R.string.terminal_compose_up_fmt, project)) }
            }
            ACTION_SMOKE_DOCUMENTS_SYNC_TO_TREE -> {
                runDocumentsMediatorAction(getString(R.string.action_documents_sync_to_tree)) {
                    safDocumentsMediator().syncToTree()
                }
            }
            ACTION_SMOKE_DOCUMENTS_SYNC_FROM_TREE -> {
                runDocumentsMediatorAction(getString(R.string.action_documents_sync_from_tree)) {
                    safDocumentsMediator().syncFromTree()
                }
            }
            ACTION_SMOKE_DOCUMENTS_WRITE_FILE -> {
                val source = intent.getStringExtra("source").orEmpty()
                val target = intent.getStringExtra("target").orEmpty()
                val mimeType = intent.getStringExtra("mimeType").orEmpty().ifBlank { "application/octet-stream" }
                thread(isDaemon = true, name = "pdocker-documents-direct-write") {
                    val result = writeDocumentsFileForAutomation(source, target, mimeType)
                    File(pdockerHome, "diagnostics/saf-write-latest.json").apply {
                        parentFile?.mkdirs()
                        writeText(result.toString(2) + "\n")
                    }
                }
            }
            ACTION_SMOKE_UI_IT_SELFTEST -> {
                val container = intent.getStringExtra("container").orEmpty()
                thread(isDaemon = true, name = "pdocker-ui-it-selftest") {
                    val result = runUiItSelfTest(container)
                    File(pdockerHome, "diagnostics/ui-it-selftest-latest.json").apply {
                        parentFile?.mkdirs()
                        writeText(result.toString(2) + "\n")
                    }
                }
            }
        }
    }

    private fun runUiItSelfTest(requestedContainer: String): JSONObject {
        val startedAt = System.currentTimeMillis()
        val result = JSONObject()
            .put("Name", "ui-engine-exec-it")
            .put("StartedAtMs", startedAt)
            .put("RequestedContainer", requestedContainer)
        val output = StringBuffer()
        var bridge: Bridge? = null
        var webView: WebView? = null
        return runCatching {
            startDaemon()
            check(waitForEngine(30_000)) { "pdockerd did not become ready" }
            val containerId = resolveUiItSelfTestContainer(requestedContainer)
            result.put("Container", containerId)

            val ready = CountDownLatch(1)
            ui.post {
                val view = WebView(this).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    alpha = 0.01f
                }
                val b = Bridge(this, view, engineExecTerminalCommand(containerId)) { bytes ->
                    output.append(bytes.toString(Charsets.UTF_8))
                }
                view.addJavascriptInterface(b, "PdockerBridge")
                view.loadUrl("file:///android_asset/xterm/index.html")
                if (::lowerHost.isInitialized) {
                    lowerHost.addView(view, FrameLayout.LayoutParams(1, 1))
                }
                webView = view
                bridge = b
                ready.countDown()
            }
            check(ready.await(5, TimeUnit.SECONDS)) { "UI bridge was not created" }

            check(waitUntil(5_000) {
                    output.toString().contains("# ")
            }) { "UI exec -it did not reach an interactive shell prompt" }
            check(!output.toString().contains("can't access tty")) {
                "UI exec -it is not attached to a controlling tty"
            }

            val script = "p=pdocker-ui-it\necho \${p}-ok\n/usr/bin/[ \"x\" = \"x\" ] && echo \${p}-bracket-ok\npwd\n[ -t 0 ] && echo \${p}-tty-ok\n[ \"\$TERM\" = \"xterm-256color\" ] && echo \${p}-term-ok\n[ -n \"\$BASH_VERSION\" ] && echo \${p}-bash-ok\ntop -b -n 1 >/dev/null && echo \${p}-top-ok\necho \${p}-arrow-seed\n"
            ui.post {
                webView?.evaluateJavascript(
                    "window.pdockerTestSendInput && window.pdockerTestSendInput(${JSONObject.quote(script)}, false)",
                    null,
                )
            }
            check(waitUntil(5_000) {
                val text = output.toString()
                text.contains("pdocker-ui-it-ok") &&
                    text.contains("pdocker-ui-it-bracket-ok") &&
                    text.contains("pdocker-ui-it-tty-ok") &&
                    text.contains("pdocker-ui-it-term-ok") &&
                    text.contains("pdocker-ui-it-bash-ok") &&
                    text.contains("pdocker-ui-it-top-ok") &&
                    text.contains("pdocker-ui-it-arrow-seed")
            }) { "UI exec -it did not echo initial expected markers" }
            ui.post {
                webView?.evaluateJavascript(
                    "window.pdockerTestSendInput && window.pdockerTestSendInput('\\u001b[A\\r', true)",
                    null,
                )
            }
            check(waitUntil(3_000) {
                Regex("pdocker-ui-it-arrow-seed").findAll(output.toString()).count() >= 2
            }) { "UI exec -it arrow key did not reach shell readline/history as an escape sequence" }
            ui.post {
                webView?.evaluateJavascript(
                    "window.pdockerTestSendInput && window.pdockerTestSendInput('top\\n', false)",
                    null,
                )
            }
            Thread.sleep(1_200)
            ui.post {
                webView?.evaluateJavascript(
                    "window.pdockerTestSendInput && window.pdockerTestSendInput('q', true)",
                    null,
                )
            }
            Thread.sleep(500)
            ui.post {
                webView?.evaluateJavascript(
                    "window.pdockerTestSendInput && window.pdockerTestSendInput('echo \${p}-topq-ok\\n', false)",
                    null,
                )
            }
            check(waitUntil(5_000) { output.toString().contains("pdocker-ui-it-topq-ok") }) {
                "UI exec -it fullscreen top did not accept q over the terminal input stream"
            }
            ui.post {
                webView?.evaluateJavascript(
                    "window.pdockerTestSendInput && window.pdockerTestSendInput('sleep 15\\n', false)",
                    null,
                )
            }
            Thread.sleep(1_200)
            ui.post {
                webView?.evaluateJavascript(
                    "window.pdockerTestCtrlInput && window.pdockerTestCtrlInput('c')",
                    null,
                )
            }
            ui.post {
                webView?.evaluateJavascript(
                    "window.pdockerTestSendInput && window.pdockerTestSendInput('echo \${p}-ctrlc-ok\\nexit\\n', false)",
                    null,
                )
            }
            val passed = waitUntil(5_000) {
                output.toString().contains("pdocker-ui-it-ctrlc-ok")
            }
            val text = output.toString()
            val bracketNoise = Regex("(/usr/bin/)?\\[: extra argument").containsMatchIn(text)
            val arrowNoise = text.contains("\u001b[A")
            check(passed) { "UI exec -it Ctrl+C did not interrupt sleep and return to the shell" }
            check(!bracketNoise) { "UI exec -it produced bracket argv noise" }
            check(!arrowNoise) { "UI exec -it printed arrow escape bytes instead of treating them as terminal input" }
            check(text.contains("pdocker-ui-it-ok\r\n") || text.contains("pdocker-ui-it-ok\n")) {
                "UI exec -it did not preserve terminal CRLF line control"
            }
            result
                .put("Success", true)
                .put("DurationMs", System.currentTimeMillis() - startedAt)
                .put("OutputTail", text.takeLast(4096))
        }.getOrElse { err ->
            result
                .put("Success", false)
                .put("DurationMs", System.currentTimeMillis() - startedAt)
                .put("Error", err.message.orEmpty())
                .put("OutputTail", output.toString().takeLast(4096))
                .put("EngineExecDiagnostics", File(pdockerHome, "diagnostics/engine-exec-input-latest.jsonl").readTextIfExists().takeLast(4096))
        }.also {
            ui.post {
                bridge?.close()
                webView?.let { view ->
                    (view.parent as? FrameLayout)?.removeView(view)
                    view.destroy()
                }
            }
        }
    }

    private fun resolveUiItSelfTestContainer(requestedContainer: String): String {
        val containers = engine.getArray("/containers/json?all=1")
        fun objAt(i: Int): JSONObject? = containers.optJSONObject(i)
        val requested = requestedContainer.trim()
        val chosen = if (requested.isNotEmpty()) {
            (0 until containers.length()).asSequence()
                .mapNotNull(::objAt)
                .firstOrNull { obj ->
                    val id = obj.optString("Id")
                    val names = obj.optJSONArray("Names")
                    id == requested || id.startsWith(requested) ||
                        (0 until (names?.length() ?: 0)).any { idx ->
                            names?.optString(idx)?.trimStart('/') == requested
                        }
                }
                ?: error("container not found: $requested")
        } else {
            (0 until containers.length()).asSequence()
                .mapNotNull(::objAt)
                .firstOrNull { it.optString("State") == "running" }
                ?: (0 until containers.length()).asSequence().mapNotNull(::objAt).firstOrNull()
                ?: error("no containers available for UI exec self-test")
        }
        val id = chosen.getString("Id")
        if (chosen.optString("State") != "running") {
            engine.post("/containers/${DockerEngineClient.encodePath(id)}/start")
            check(waitUntil(15_000) {
                val refreshed = engine.getArray("/containers/json?all=1")
                (0 until refreshed.length()).asSequence()
                    .mapNotNull { refreshed.optJSONObject(it) }
                    .any { it.optString("Id") == id && it.optString("State") == "running" }
            }) { "container did not start for UI exec self-test: $id" }
        }
        return id
    }

    private fun waitUntil(timeoutMs: Long, predicate: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (runCatching { predicate() }.getOrDefault(false)) return true
            Thread.sleep(100)
        }
        return runCatching { predicate() }.getOrDefault(false)
    }

    private fun File.readTextIfExists(): String =
        runCatching { if (isFile) readText() else "" }.getOrDefault("")

    private fun writeDocumentsFileForAutomation(sourcePath: String, targetPath: String, mimeType: String): JSONObject {
        val metadata = documentsTreeMetadata()
        val mediator = safDocumentsMediator()
        val grants = mediator.persistedGrantState()
        val source = File(sourcePath)
        val normalizedTarget = targetPath.replace('\\', '/').trimStart('/')
        val attempts = JSONArray()
        val out = JSONObject()
            .put("Source", sourcePath)
            .put("Target", normalizedTarget)
            .put("MimeType", mimeType)
            .put("Access", metadata.writeAccess.envValue)
            .put("PersistedWriteGrant", grants.write)
            .put("SelectedHostPath", metadata.selectedHostPath)
            .put("ActiveHostPath", metadata.activeHostPath)
        if (!source.isFile) {
            return out.put("Success", false).put("Error", "source file is missing")
        }
        val primary = if (metadata.writeAccess == DocumentsWriteAccess.DirectPathWritable) {
            runCatching {
                val target = File(metadata.directHostPath, normalizedTarget)
                target.parentFile?.mkdirs()
                source.inputStream().use { input ->
                    target.outputStream().use { output -> input.copyTo(output) }
                }
                JSONObject()
                    .put("Success", true)
                    .put("Mode", "direct-path")
                    .put("RelativePath", normalizedTarget)
                    .put("HostPath", target.absolutePath)
                    .put("Bytes", source.length())
            }.getOrElse {
                JSONObject()
                    .put("Success", false)
                    .put("Mode", "direct-path")
                    .put("RelativePath", normalizedTarget)
                    .put("Bytes", 0L)
                    .put("Error", it.message ?: it.toString())
            }
        } else {
            mediator.writeFile(normalizedTarget, source, mimeType)
        }
        attempts.put(primary)
        if (primary.optBoolean("Success", false)) {
            return out
                .put("Success", true)
                .put("Bytes", primary.optLong("Bytes", source.length()))
                .put("Mode", primary.optString("Mode"))
                .put("Attempts", attempts)
        }
        val fallback = mediator.writeMirrorFallbackFile(
            relativePath = normalizedTarget,
            source = source,
            mimeType = mimeType,
            reason = primary.optString("Error", "primary Documents write failed"),
        )
        attempts.put(fallback)
        return out
            .put("Success", fallback.optBoolean("Success", false))
            .put("Bytes", fallback.optLong("Bytes", 0L))
            .put("Mode", fallback.optString("Mode"))
            .put("Fallback", true)
            .put("Attempts", attempts)
    }

    private fun prefs() = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)

    private fun requestDocumentsVolumeFolder() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
            addFlags(
                Intent.FLAG_GRANT_READ_URI_PERMISSION or
                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION or
                    Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION or
                    Intent.FLAG_GRANT_PREFIX_URI_PERMISSION,
            )
        }
        startActivityForResult(intent, REQUEST_DOCUMENTS_TREE)
    }

    private fun documentsVolumeDetail(): String =
        documentsTreeMetadata().let { metadata ->
            getString(
                R.string.detail_documents_volume_fmt,
                metadata.displayName,
                metadata.activeHostPath.ifBlank { getString(R.string.documents_volume_saf_only) },
                PDOCKER_DOCUMENTS_MOUNT,
                if (metadata.writeAccess == DocumentsWriteAccess.DirectPathWritable) {
                    documentsProjectsRoot().absolutePath
                } else {
                    legacyProjectRoot.absolutePath
                },
                documentsWriteAccessLabel(metadata.writeAccess),
            )
        }

    private fun documentsHostPath(): String =
        documentsTreeMetadata().activeHostPath

    private fun selectedDocumentsHostPath(): String =
        prefs().getString(PREF_DOCUMENTS_HOST_PATH, null)
            ?.takeIf { it.isNotBlank() }
            ?: defaultDocumentsHostPath()

    private fun documentsWorkspaceRoot(): File =
        File(selectedDocumentsHostPath(), "pdocker")

    private fun documentsProjectsRoot(): File =
        File(documentsWorkspaceRoot(), "projects")

    private fun documentsProjectsRootWritable(): Boolean =
        documentsTreeMetadata().writeAccess == DocumentsWriteAccess.DirectPathWritable

    private fun selectedDocumentsProjectsRootWritable(selectedHostPath: String): Boolean =
        documentsDirectPathWritableCandidate(prefs().getString(PREF_DOCUMENTS_TREE_URI, "").orEmpty(), selectedHostPath) &&
            probeDocumentsProjectsRootWritable(selectedHostPath)

    private fun probeDocumentsProjectsRootWritable(selectedHostPath: String): Boolean =
        File(File(selectedHostPath, "pdocker"), "projects").absolutePath.let { path ->
            if (documentsProjectRootProbePath == path) return documentsProjectRootProbeWritable
            val writable = canWriteDirectoryByPath(File(path))
            documentsProjectRootProbePath = path
            documentsProjectRootProbeWritable = writable
            writable
        }

    private fun canWriteDirectoryByPath(dir: File): Boolean {
        val probe = File(dir, ".pdocker-write-probe")
        return runCatching {
            dir.mkdirs()
            probe.writeText("ok\n")
            probe.delete()
            true
        }.getOrDefault(false)
    }

    private fun documentsVolumeRootPath(): String =
        File(File(documentsHostPath(), "pdocker"), "volumes").absolutePath

    private fun sharedDocumentsHostPath(): String =
        File(File(documentsHostPath(), "pdocker"), "shared").absolutePath

    private fun documentsSharedHostPath(projectName: String): String =
        File(projectVolumeHostPath(projectName), "shared").absolutePath

    private fun projectVolumeHostPath(projectName: String): String =
        File(documentsVolumeRootPath(), projectName.ifBlank { "default" }).absolutePath

    private fun fastWorkspaceHostPath(projectName: String): String =
        File(File(pdockerHome, "workspaces"), projectName.ifBlank { "default" }).absolutePath

    private fun fastStateHostPath(projectName: String, serviceName: String): String =
        File(File(File(pdockerHome, "state"), projectName.ifBlank { "default" }), serviceName.ifBlank { "default" }).absolutePath

    private fun modelHostPath(projectName: String): String =
        File(File(pdockerHome, "models"), projectName.ifBlank { "default" }).absolutePath

    private fun safMediatedDocumentsHostPath(): String =
        File(pdockerHome, "documents-saf-mediated/mirror").absolutePath

    private fun safDocumentsSidecarPath(): String =
        File(pdockerHome, "documents-saf-mediated/sidecar").absolutePath

    private fun safDocumentsMediator(): SafDocumentsMediator =
        SafDocumentsMediator(
            context = this,
            treeUriText = prefs().getString(PREF_DOCUMENTS_TREE_URI, "").orEmpty(),
            mirrorRoot = File(safMediatedDocumentsHostPath()),
            sidecarRoot = File(safDocumentsSidecarPath()),
        )

    private fun documentsMediatorStatusJson(): JSONObject {
        val metadata = documentsTreeMetadata()
        val grants = safDocumentsMediator().persistedGrantState()
        return safDocumentsMediator().statusJson()
            .put("Mode", if (metadata.writeAccess == DocumentsWriteAccess.DirectPathWritable) "direct-path-writable" else "saf-mediated-mirror")
            .put("Access", metadata.writeAccess.envValue)
            .put("PersistedReadGrant", grants.read)
            .put("PersistedWriteGrant", grants.write)
            .put("PersistedGrantAvailable", grants.available)
            .put("DirectHostPath", metadata.directHostPath)
            .put("ActiveHostPath", metadata.activeHostPath)
            .put("SelectedHostPath", metadata.selectedHostPath)
            .put("EngineStatusPath", "/system/documents/status")
            .put("EngineSyncToTreePath", "/system/documents/sync-to-tree")
            .put("EngineSyncFromTreePath", "/system/documents/sync-from-tree")
    }

    private fun runDocumentsMediatorAction(title: String, action: () -> JSONObject) {
        openTextToolAsync(getString(R.string.section_diagnostics), title) {
            JSONObject()
                .put("Before", documentsMediatorStatusJson())
                .put("Result", action())
                .put("After", documentsMediatorStatusJson())
                .toString(2) + "\n"
        }
    }

    private fun startDocumentsMirrorSync() {
        if (documentsTreeMetadata().writeAccess != DocumentsWriteAccess.SafMediated) {
            stopDocumentsMirrorSync()
            return
        }
        val grants = safDocumentsMediator().persistedGrantState()
        if (!grants.available) return
        val root = File(safMediatedDocumentsHostPath())
        root.mkdirs()
        watchDocumentsMirrorDirectory(root)
        root.walkTopDown()
            .filter { it.isDirectory }
            .take(MAX_DOCUMENTS_MIRROR_OBSERVERS)
            .forEach { watchDocumentsMirrorDirectory(it) }
    }

    private fun stopDocumentsMirrorSync() {
        synchronized(documentsSyncLock) {
            documentsMirrorObservers.values.forEach { observer ->
                runCatching { observer.stopWatching() }
            }
            documentsMirrorObservers.clear()
            pendingDocumentsSyncPaths.clear()
            documentsSyncScheduled = false
            documentsSyncRunning = false
        }
    }

    private fun watchDocumentsMirrorDirectory(dir: File) {
        if (!dir.isDirectory) return
        val key = dir.canonicalPath
        synchronized(documentsSyncLock) {
            if (documentsMirrorObservers.containsKey(key) || documentsMirrorObservers.size >= MAX_DOCUMENTS_MIRROR_OBSERVERS) {
                return
            }
            val mask = FileObserver.CREATE or FileObserver.CLOSE_WRITE or FileObserver.MOVED_TO or FileObserver.MODIFY
            @Suppress("DEPRECATION")
            val observer = object : FileObserver(dir.absolutePath, mask) {
                override fun onEvent(event: Int, path: String?) {
                    if (path.isNullOrBlank()) return
                    val changed = File(dir, path)
                    val directory = changed.isDirectory
                    if (directory) watchDocumentsMirrorDirectory(changed)
                    val relative = runCatching {
                        changed.relativeTo(File(safMediatedDocumentsHostPath())).invariantSeparatorsPath
                    }.getOrNull().orEmpty()
                    if (relative.isNotBlank()) {
                        queueDocumentsSync(relative)
                        if (directory) {
                            ui.postDelayed({ queueDocumentsSync(relative) }, DOCUMENTS_SYNC_DEBOUNCE_MS * 2)
                        }
                    }
                }
            }
            observer.startWatching()
            documentsMirrorObservers[key] = observer
        }
    }

    private fun queueDocumentsSync(relativePath: String) {
        synchronized(documentsSyncLock) {
            pendingDocumentsSyncPaths += relativePath
            if (documentsSyncScheduled || documentsSyncRunning) return
            documentsSyncScheduled = true
        }
        ui.postDelayed({ flushPendingDocumentsSync() }, DOCUMENTS_SYNC_DEBOUNCE_MS)
    }

    private fun scanDocumentsExportMirrorForChanges() {
        if (documentsTreeMetadata().writeAccess != DocumentsWriteAccess.SafMediated) return
        val grants = safDocumentsMediator().persistedGrantState()
        if (!grants.available) return
        val exports = File(File(safMediatedDocumentsHostPath()), "pdocker-exports")
        if (!exports.exists()) return
        val root = File(safMediatedDocumentsHostPath())
        var scanned = 0
        exports.walkTopDown().forEach { file ->
            if (scanned >= MAX_DOCUMENTS_SYNC_SCAN_ENTRIES) return@forEach
            if (!file.isFile) return@forEach
            scanned += 1
            val relative = runCatching { file.relativeTo(root).invariantSeparatorsPath }.getOrNull().orEmpty()
            if (relative.isBlank()) return@forEach
            val fingerprint = "${file.length()}:${file.lastModified()}"
            val previous = synchronized(documentsSyncLock) { documentsMirrorScanState[relative] }
            if (previous != fingerprint) {
                synchronized(documentsSyncLock) { documentsMirrorScanState[relative] = fingerprint }
                queueDocumentsSync(relative)
            }
        }
    }

    private fun flushPendingDocumentsSync() {
        val paths = synchronized(documentsSyncLock) {
            if (documentsSyncRunning) return
            documentsSyncScheduled = false
            if (pendingDocumentsSyncPaths.isEmpty()) return
            documentsSyncRunning = true
            val now = System.currentTimeMillis()
            val wait = DOCUMENTS_SYNC_MIN_INTERVAL_MS - (now - lastDocumentsSyncAt)
            if (wait > 0L) {
                documentsSyncScheduled = true
                documentsSyncRunning = false
                ui.postDelayed({ flushPendingDocumentsSync() }, wait)
                return
            }
            pendingDocumentsSyncPaths.toList().also { pendingDocumentsSyncPaths.clear() }
        }
        thread(isDaemon = true, name = "pdocker-documents-buffered-sync") {
            var files = 0
            var bytes = 0L
            var errors = 0
            var evicted = 0
            paths.forEach { path ->
                val report = runCatching { safDocumentsMediator().syncPathToTree(path, evictMirrorPayload = true) }
                    .getOrElse {
                        errors += 1
                        return@forEach
                    }
                files += report.optInt("Files", 0)
                bytes += report.optLong("Bytes", 0L)
                evicted += report.optInt("EvictedMirrorFiles", 0)
                errors += report.optJSONArray("Errors")?.length() ?: 0
            }
            synchronized(documentsSyncLock) {
                lastDocumentsSyncAt = System.currentTimeMillis()
                documentsSyncRunning = false
                if (pendingDocumentsSyncPaths.isNotEmpty() && !documentsSyncScheduled) {
                    documentsSyncScheduled = true
                    ui.postDelayed({ flushPendingDocumentsSync() }, DOCUMENTS_SYNC_DEBOUNCE_MS)
                }
            }
            if (files > 0 || errors > 0) {
                ui.post {
                    status.text = "Documents SAF sync: files=$files bytes=$bytes evicted=$evicted errors=$errors"
                }
            }
        }
    }

    private fun documentsTreeMetadata(): PersistedDocumentsTreeMetadata {
        val uri = prefs().getString(PREF_DOCUMENTS_TREE_URI, "").orEmpty()
        val selectedHostPath = selectedDocumentsHostPath()
        val directHostPath = selectedHostPath
            .takeIf { it.isNotBlank() && documentsDirectPathWritableCandidate(uri, it) && probeDocumentsProjectsRootWritable(it) }
            .orEmpty()
        val writeAccess = if (directHostPath.isNotBlank()) {
            DocumentsWriteAccess.DirectPathWritable
        } else {
            DocumentsWriteAccess.SafMediated
        }
        val activeHostPath = directHostPath.ifBlank { safMediatedDocumentsHostPath() }
        val displayName = prefs().getString(PREF_DOCUMENTS_DISPLAY_NAME, null)
            ?.takeIf { it.isNotBlank() }
            ?: getString(R.string.documents_volume_default_name)
        return PersistedDocumentsTreeMetadata(
            treeUri = uri,
            displayName = displayName,
            selectedHostPath = selectedHostPath,
            directHostPath = directHostPath,
            activeHostPath = activeHostPath,
            writeAccess = writeAccess,
        )
    }

    private fun documentsDirectPathWritableCandidate(uriText: String, selectedHostPath: String): Boolean {
        if (selectedHostPath.isBlank()) return false
        // Fresh installs have no SAF grant yet. Do not infer direct access to
        // public Documents from the default display path; Android 13+ rejects
        // that write and can crash startup before the user can choose storage.
        if (uriText.isBlank()) return false
        return !documentsTreeRequiresSafMediation(uriText)
    }

    private fun documentsTreeRequiresSafMediation(uriText: String): Boolean {
        if (uriText.isBlank()) return false
        val uri = runCatching { Uri.parse(uriText) }.getOrNull() ?: return true
        if (uri.authority != "com.android.externalstorage.documents") return true
        val treeId = runCatching { DocumentsContract.getTreeDocumentId(uri) }.getOrNull().orEmpty()
        val volume = treeId.substringBefore(':', "")
        return volume !in setOf("primary", "home")
    }

    private fun documentsWriteAccessLabel(access: DocumentsWriteAccess): String =
        getString(when (access) {
            DocumentsWriteAccess.DirectPathWritable -> R.string.documents_access_direct_path_writable
            DocumentsWriteAccess.SafMediated -> R.string.documents_access_saf_mediated
        })

    private fun documentsGrantStatusLabel(): String {
        val grants = safDocumentsMediator().persistedGrantState()
        return when {
            grants.read && grants.write -> "read/write"
            grants.read -> "read-only"
            grants.write -> "write-only"
            else -> "missing"
        }
    }

    private fun documentsStorageStatusLine(): String {
        val metadata = documentsTreeMetadata()
        return getString(
            R.string.host_environment_documents_fmt,
            documentsWriteAccessLabel(metadata.writeAccess),
            metadata.activeHostPath,
            metadata.selectedHostPath.ifBlank { "-" },
            metadata.treeUri.ifBlank { "-" },
            documentsGrantStatusLabel(),
        )
    }

    private fun defaultDocumentsHostPath(): String {
        val docs = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
        return docs.absolutePath.ifBlank { "/storage/emulated/0/Documents" }
    }

    private fun documentTreeDisplayName(uri: Uri, hostPath: String): String {
        if (hostPath.isNotBlank()) return hostPath
        val treeId = runCatching { DocumentsContract.getTreeDocumentId(uri) }.getOrNull().orEmpty()
        return treeId.ifBlank { uri.toString() }
    }

    private fun documentTreeHostPath(uri: Uri): String? {
        if (uri.authority != "com.android.externalstorage.documents") return null
        val treeId = runCatching { DocumentsContract.getTreeDocumentId(uri) }.getOrNull().orEmpty()
        val volume = treeId.substringBefore(':', "")
        val rel = treeId.substringAfter(':', "").trim('/')
        val base = when (volume) {
            "primary" -> Environment.getExternalStorageDirectory().absolutePath
            "home" -> defaultDocumentsHostPath()
            else -> "/storage/$volume"
        }
        if (base.isBlank()) return null
        val path = if (rel.isBlank() || volume == "home") base else "$base/$rel"
        return path.takeIf { it.isNotBlank() }
    }

    private fun syncDocumentsVolumeEnv() {
        projectRoot.mkdirs()
        if (documentsTreeMetadata().writeAccess == DocumentsWriteAccess.SafMediated) {
            safDocumentsMediator().initializeContract()
            startDocumentsMirrorSync()
        }
        File(documentsVolumeRootPath()).mkdirs()
        File(sharedDocumentsHostPath()).mkdirs()
        writeDocumentsEnv(File(projectRoot, ".pdocker-common.env"))
        projectDirs().forEach { writeDocumentsEnv(File(it, ".env")) }
    }

    private fun ensureProjectDocumentsEnv(project: File) {
        project.mkdirs()
        writeDocumentsEnv(File(project, ".env"))
        writeDocumentsEnv(File(projectRoot, ".pdocker-common.env"))
    }

    private fun writeDocumentsEnv(file: File) {
        val metadata = documentsTreeMetadata()
        val projectName = file.parentFile
            ?.takeIf { file.name == ".env" && it.parentFile?.canonicalPath == projectRoot.canonicalPath }
            ?.name
            ?.ifBlank { "default" }
        val updates = linkedMapOf(
            "PDOCKER_DOCUMENTS_ROOT" to documentsHostPath(),
            "PDOCKER_PROJECTS_HOST" to projectRoot.absolutePath,
            "PDOCKER_VOLUME_ROOT" to documentsVolumeRootPath(),
            "PDOCKER_DOCUMENTS_HOST" to documentsHostPath(),
            "PDOCKER_DOCUMENTS_MOUNT" to PDOCKER_DOCUMENTS_MOUNT,
            "PDOCKER_SHARED_DOCUMENTS_HOST" to sharedDocumentsHostPath(),
            "PDOCKER_SHARED_DOCUMENTS_MOUNT" to "/shared",
            "PDOCKER_DOCUMENTS_MODE" to metadata.writeAccess.envValue,
            "PDOCKER_DOCUMENTS_ACCESS" to metadata.writeAccess.envValue,
            "PDOCKER_DOCUMENTS_TREE_URI" to metadata.treeUri,
            "PDOCKER_DOCUMENTS_SELECTED_HOST" to metadata.selectedHostPath,
            "PDOCKER_DOCUMENTS_DIRECT_HOST" to metadata.directHostPath,
            "PDOCKER_DOCUMENTS_MEDIATED_HOST" to if (metadata.writeAccess == DocumentsWriteAccess.SafMediated) metadata.activeHostPath else "",
            "PDOCKER_DOCUMENTS_MEDIATOR" to if (metadata.writeAccess == DocumentsWriteAccess.SafMediated) "android-saf" else "direct-path",
            "PDOCKER_DOCUMENTS_SAF_MIRROR_HOST" to if (metadata.writeAccess == DocumentsWriteAccess.SafMediated) metadata.activeHostPath else "",
            "PDOCKER_DOCUMENTS_SAF_SIDECAR_HOST" to if (metadata.writeAccess == DocumentsWriteAccess.SafMediated) safDocumentsSidecarPath() else "",
            "PDOCKER_APP_HOME_HOST" to pdockerHome.absolutePath,
        )
        if (projectName != null) {
            updates["PDOCKER_PROJECT_NAME"] = projectName
            updates["PDOCKER_PROJECT_VOLUME_HOST"] = projectVolumeHostPath(projectName)
            updates["PDOCKER_FAST_WORKSPACE_HOST"] = fastWorkspaceHostPath(projectName)
            updates["PDOCKER_DEV_STATE_HOST"] = fastStateHostPath(projectName, "dev")
            updates["PDOCKER_MODEL_HOST"] = modelHostPath(projectName)
            File(projectVolumeHostPath(projectName)).mkdirs()
            File(documentsSharedHostPath(projectName)).mkdirs()
            File(sharedDocumentsHostPath()).mkdirs()
            File(fastWorkspaceHostPath(projectName)).mkdirs()
            File(fastStateHostPath(projectName, "dev")).mkdirs()
            File(modelHostPath(projectName)).mkdirs()
        }
        val existing = if (file.isFile) file.readLines().toMutableList() else mutableListOf()
        val preserveUserOverride = setOf(
            "PDOCKER_DOCUMENTS_HOST",
            "PDOCKER_DOCUMENTS_MOUNT",
            "PDOCKER_SHARED_DOCUMENTS_HOST",
            "PDOCKER_SHARED_DOCUMENTS_MOUNT",
            "PDOCKER_FAST_WORKSPACE_HOST",
            "PDOCKER_DEV_STATE_HOST",
            "PDOCKER_MODEL_HOST",
        )
        updates.forEach { (key, value) ->
            if (key in preserveUserOverride && existing.any { it.trimStart().startsWith("$key=") }) return@forEach
            val line = "$key=${envFileQuote(value)}"
            val index = existing.indexOfFirst { it.trimStart().startsWith("$key=") }
            if (index >= 0) existing[index] = line else existing += line
        }
        file.parentFile?.mkdirs()
        file.writeText(existing.joinToString("\n").trimEnd() + "\n")
    }

    private fun envFileQuote(value: String): String =
        if (value.any { it.isWhitespace() || it == '\'' || it == '"' || it == '#' }) {
            "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
        } else {
            value
        }

    private fun migrateLegacyProjectsToDocuments() {
        val src = legacyProjectRoot
        val dst = projectRoot
        if (!src.isDirectory) return
        val srcPath = runCatching { src.canonicalPath }.getOrDefault(src.absolutePath)
        val dstPath = runCatching { dst.canonicalPath }.getOrDefault(dst.absolutePath)
        if (srcPath == dstPath) return
        val srcProjects = src.listFiles()?.filter { it.isDirectory }.orEmpty()
        if (srcProjects.isEmpty()) return
        dst.mkdirs()
        srcProjects.forEach { project ->
            val target = File(dst, project.name)
            runCatching { copyProjectDefinitionIfAbsent(project, target) }
        }
        syncDocumentsVolumeEnv()
    }

    private fun copyProjectDefinitionIfAbsent(src: File, dst: File) {
        copyDirectoryIfAbsent(
            src = src,
            dst = dst,
            relative = "",
            skipDirs = setOf(
                ".git",
                ".gradle",
                "build",
                "node_modules",
                "workspace",
                "models",
                "profiles",
                "state",
                "vscode",
                "continue",
                "documents",
            ),
            maxFileBytes = 512 * 1024,
        )
        val note = File(dst, "LEGACY_PROJECT_MIGRATION.md")
        if (!note.exists()) {
            note.writeText(
                """
                |# Legacy Project Migration
                |
                |pdocker copied lightweight project definition files from the former
                |app-private project directory into the selected Android Documents
                |workspace root.
                |
                |Large or frequently-written folders such as `workspace`, `models`,
                |`profiles`, editor state, and caches were not copied during app
                |startup. Keep hot data in app-private fast storage and copy selected
                |artifacts to `/documents` when they need to be shared.
                |""".trimMargin() + "\n",
            )
        }
    }

    private fun copyDirectoryIfAbsent(
        src: File,
        dst: File,
        relative: String,
        skipDirs: Set<String>,
        maxFileBytes: Long,
    ) {
        if (src.isDirectory) {
            if (relative.isNotBlank() && src.name in skipDirs) return
            dst.mkdirs()
            src.listFiles()?.forEach { child ->
                val childRelative = if (relative.isBlank()) child.name else "$relative/${child.name}"
                copyDirectoryIfAbsent(child, File(dst, child.name), childRelative, skipDirs, maxFileBytes)
            }
            return
        }
        if (src.length() > maxFileBytes) return
        if (dst.exists()) return
        dst.parentFile?.mkdirs()
        src.copyTo(dst, overwrite = false)
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            == PackageManager.PERMISSION_GRANTED) return
        ActivityCompat.requestPermissions(
            this,
            arrayOf(Manifest.permission.POST_NOTIFICATIONS),
            REQUEST_POST_NOTIFICATIONS,
        )
    }

    private fun requestBatteryOptimizationBypass() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        val powerManager = getSystemService(PowerManager::class.java)
        if (powerManager.isIgnoringBatteryOptimizations(packageName)) {
            status.text = getString(R.string.status_battery_ignored)
            return
        }
        startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
            data = Uri.parse("package:$packageName")
        })
    }

    private fun openTerminal(
        title: String,
        command: String,
        group: String = workspaceGroup(),
        onOutput: ((ByteArray) -> Unit)? = null,
        contextualize: Boolean = true,
        keyOverride: String? = null,
    ) {
        val launchCommand = if (contextualize) terminalSessionCommand(title, group, command) else command
        val key = keyOverride ?: "$title\n$launchCommand"
        val existing = toolTabs.indexOfFirst {
            it.kind == ToolKind.Terminal && it.group == group && it.key == key
        }
        if (existing >= 0) {
            switchTool(existing)
            return
        }
        val view = terminalView(launchCommand, onOutput)
        val bridge = view.getTag(R.id.pdocker_bridge_tag) as Bridge
        toolTabs += ToolTab(group, title, ToolKind.Terminal, view, bridge, key)
        switchTool(toolTabs.lastIndex)
    }

    private fun openDockerTerminal(title: String, command: String, group: String = workspaceGroup()) {
        startDaemon()
        val normalizedCommand = normalizeDockerCommand(command)
        val id = "job-" + System.currentTimeMillis().toString(36)
        val wrapped = stayAfterCommand(dockerCommand(normalizedCommand), id)
        val launchCommand = terminalSessionCommand(title, group, wrapped)
        val key = "$title\n$launchCommand"
        val job = DockerJob(
            id = id,
            title = title,
            detail = group,
            command = normalizedCommand,
            group = group,
            toolKey = key,
            status = getString(R.string.job_running),
        )
        dockerJobs.add(0, job)
        trimDockerJobs()
        saveDockerJobs()
        openTerminal(
            title,
            launchCommand,
            group,
            onOutput = { bytes -> handleDockerJobOutput(job.id, bytes) },
            contextualize = false,
        )
        renderContent()
    }

    private fun openDockerInteractiveTerminal(title: String, containerId: String, group: String = workspaceGroup()) {
        startDaemon()
        openTerminal(
            title,
            engineExecTerminalCommand(containerId),
            group,
            contextualize = false,
            keyOverride = "engine-exec:${containerId.trim()}:${System.nanoTime()}",
        )
    }

    private fun engineExecTerminalCommand(containerId: String): String =
        "${Bridge.ENGINE_EXEC_PREFIX}${containerId.trim()}"

    private fun openEditor(file: File, group: String = workspaceGroup()) {
        val target = resolveProjectFile(file)
        val title = editorTitle(target)
        val key = target.absolutePath
        val existing = toolTabs.indexOfFirst {
            it.kind == ToolKind.Editor && it.group == group && it.key == key
        }
        if (existing >= 0) {
            switchTool(existing)
            return
        }
        toolTabs += ToolTab(group, title, ToolKind.Editor, editorView(target), key = key)
        switchTool(toolTabs.lastIndex)
    }

    private fun openConsoleEditorSplit(title: String, command: String, file: File, group: String = workspaceGroup()) {
        val target = resolveProjectFile(file)
        val view = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val terminal = terminalView(command)
            addView(terminal, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                0.56f,
            ))
            addView(editorView(target), LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                0.44f,
            ))
        }
        val bridge = findBridge(view)
        toolTabs += ToolTab(group, title, ToolKind.Split, view, bridge, "$title\n$command\n${target.absolutePath}")
        switchTool(toolTabs.lastIndex)
    }

    private fun openImageFiles(image: File? = null) {
        startActivity(Intent(this, ImageFilesActivity::class.java).apply {
            image?.let { putExtra(ImageFilesActivity.EXTRA_IMAGE_NAME, it.name) }
        })
    }

    private fun openContainerFiles(container: File) {
        startActivity(Intent(this, ImageFilesActivity::class.java).apply {
            putExtra(ImageFilesActivity.EXTRA_CONTAINER_ID, container.name)
        })
    }

    private fun terminalView(command: String, onOutput: ((ByteArray) -> Unit)? = null): View {
        val webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
        }
        val bridge = Bridge(this, webView, command, onOutput)
        webView.addJavascriptInterface(bridge, "PdockerBridge")
        webView.loadUrl("file:///android_asset/xterm/index.html")
        webView.setTag(R.id.pdocker_bridge_tag, bridge)
        return webView
    }

    private fun terminalLogPane(): TerminalLogPane {
        lateinit var pane: TerminalLogPane
        val webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            addJavascriptInterface(TerminalLogBridge(this@MainActivity), "PdockerBridge")
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    pane.markReady()
                }
            }
        }
        pane = TerminalLogPane(webView)
        webView.loadUrl("file:///android_asset/xterm/index.html")
        return pane
    }

    private fun editorView(file: File): View {
        return CodeEditorView(this, file, MAX_INLINE_EDIT_BYTES) { name ->
            defaultEditorContent(file, name)
        }
    }

    private fun terminalSessionCommand(title: String, group: String, command: String): String {
        val label = "$group / $title"
        val prompt = "[pdocker:$label] \\w $ "
        return listOf(
            "export PDOCKER_TERMINAL_TITLE=${shellQuote(title)}",
            "export PDOCKER_TERMINAL_GROUP=${shellQuote(group)}",
            "export PS1=${shellQuote(prompt)}",
            "printf '\\n[pdocker terminal] %s\\n[pdocker group] %s\\n\\n' ${shellQuote(title)} ${shellQuote(group)}",
            command,
        ).joinToString("; ")
    }

    private fun switchTool(index: Int) {
        if (index !in toolTabs.indices) return
        currentTool = index
        currentToolGroup = toolTabs[index].group
        lowerHost.removeAllViews()
        val view = toolTabs[index].view
        if (view.parent != null) {
            (view.parent as? FrameLayout)?.removeView(view)
        }
        lowerHost.addView(view, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT,
        ))
        renderToolChrome()
    }

    private fun renderToolChrome() {
        if (!::lowerGroupRow.isInitialized || !::lowerTabRow.isInitialized || !::lowerHost.isInitialized) return
        lowerGroupRow.removeAllViews()
        lowerTabRow.removeAllViews()
        if (toolTabs.isEmpty()) {
            lowerHost.removeAllViews()
            lowerHost.addView(TextView(this).apply {
                text = getString(R.string.tool_empty)
                textSize = 14f
                alpha = 0.72f
                gravity = Gravity.CENTER
            }, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            ))
            return
        }
        val groups = toolTabs.map { it.group }.distinct()
        if (currentTool !in toolTabs.indices) currentTool = 0
        if (currentToolGroup == null || currentToolGroup !in groups) {
            currentToolGroup = toolTabs[currentTool].group
        }
        groups.forEach { group ->
            lowerGroupRow.addView(Button(this).apply {
                text = group
                isAllCaps = false
                alpha = if (group == currentToolGroup) 1f else 0.66f
                setOnClickListener {
                    currentToolGroup = group
                    switchTool(toolTabs.indexOfFirst { it.group == group })
                }
            })
        }
        toolTabs.forEachIndexed { index, tab ->
            if (tab.group == currentToolGroup) {
                lowerTabRow.addView(Button(this).apply {
                    text = tab.title
                    isAllCaps = false
                    alpha = if (index == currentTool) 1f else 0.72f
                    setOnClickListener { switchTool(index) }
                })
            }
        }
        lowerTabRow.addView(Button(this).apply {
            text = "+"
            isAllCaps = false
            setOnClickListener {
                openTerminal(
                    getString(R.string.terminal_shell_numbered, toolTabs.count { it.group == currentToolGroup } + 1),
                    "sh",
                    currentToolGroup ?: workspaceGroup(),
                )
            }
        })
    }

    private fun findBridge(view: View): Bridge? {
        (view.getTag(R.id.pdocker_bridge_tag) as? Bridge)?.let { return it }
        if (view is LinearLayout) {
            for (i in 0 until view.childCount) {
                findBridge(view.getChildAt(i))?.let { return it }
            }
        }
        return null
    }

    private fun workspaceGroup(): String = getString(R.string.tool_group_workspace)

    private fun resolveProjectFile(file: File): File {
        val projects = projectRoot.apply { mkdirs() }.canonicalFile
        val canonical = file.canonicalFile
        return if (canonical.toPath().startsWith(projects.toPath())) {
            canonical
        } else {
            File(projects, "default/${file.name.ifBlank { "Dockerfile" }}").canonicalFile
        }
    }

    private fun defaultEditorContent(file: File, name: String): String =
        when (name) {
            "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml" ->
                "services:\n  app:\n    image: ubuntu:22.04\n    command: [\"/bin/bash\", \"-lc\", \"echo hello from compose\"]\n"
            "Dockerfile" ->
                if (file.parentFile?.name == "default") {
                    assets.open("default-project/Dockerfile").bufferedReader().use { it.readText() }
                } else {
                    "FROM ubuntu:22.04\nCMD [\"/bin/bash\", \"-lc\", \"echo hello from Dockerfile\"]\n"
                }
            else -> ""
        }

    private fun editorTitle(file: File): String {
        val parent = file.parentFile?.name.orEmpty()
        return if (parent.isBlank() || parent == "default") file.name else "$parent/${file.name}"
    }

    private fun stayAfterCommand(command: String, jobId: String? = null): String {
        val marker = jobId?.let {
            "; printf '\\n__PDOCKER_JOB_EXIT:${it}:%s__\\n' \"\$status\""
        }.orEmpty()
        return "$command; status=\$?; printf '\\n[pdocker] command exited: %s\\n' \"\$status\"$marker; exec sh"
    }

    private fun dockerCommand(command: String): String {
        val normalized = normalizeDockerCommand(command)
        val quoted = shellQuote(normalized)
        val dockerConfig = shellQuote(File(filesDir, "pdocker-runtime/docker-bin").absolutePath)
        return listOf(
            "export DOCKER_CONFIG=$dockerConfig DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain COMPOSE_MENU=false",
            "i=0; until docker version >/dev/null 2>&1; do i=\$((i+1)); if [ \"\$i\" -ge 30 ]; then echo '[pdocker] pdockerd did not become ready within 30s'; break; fi; printf '[pdocker] waiting for pdockerd... %s/30\\n' \"\$i\"; sleep 1; done",
            "if printf '%s\\n' $quoted | grep -q 'docker compose' && ! docker compose version >/dev/null 2>&1; then echo '[pdocker] docker compose is unavailable in the bundled docker CLI'; false; else $normalized; fi",
        ).joinToString("; ")
    }

    private fun dockerBuildCommand(dir: File): String =
        "cd ${shellQuote(dir.absolutePath)} && docker build -t local/${dir.name}:latest ."

    private fun composeUpCommand(dir: File): String =
        "cd ${shellQuote(dir.absolutePath)} && docker compose up --detach --build && docker compose ps && docker compose logs --tail=80"

    private fun normalizeDockerCommand(command: String): String =
        command.replace(Regex("(^|[;&|]\\s*)docker-compose(?=\\s)")) {
            "${it.groupValues[1]}docker compose"
        }

    private fun renderDockerJobs(filter: ((DockerJob) -> Boolean)? = null) {
        val jobs = dockerJobs.filter { filter?.invoke(it) ?: true }
        if (jobs.isEmpty()) return
        addSection(getString(R.string.section_jobs))
        jobs.take(5).forEach { job ->
            val statusText = jobStatusText(job)
            val detail = listOf(
                job.detail,
                job.progress,
                job.command,
                job.output.takeLast(3).joinToString("\n"),
            ).filter { it.isNotBlank() }.joinToString("\n")
            addWidget(job.title, statusText, detail) {
                val index = toolTabs.indexOfFirst { it.key == job.toolKey }
                if (index >= 0) switchTool(index) else openJobLog(job)
            }
            addAction(getString(R.string.action_open_job_log_fmt, job.title), job.command) {
                openJobLog(job)
            }
            if (job.exitCode == null) {
                addAction(getString(R.string.action_stop_job_fmt, job.title), job.command) {
                    stopDockerJob(job.id)
                }
            } else {
                addAction(getString(R.string.action_retry_job_fmt, job.title), job.command) {
                    retryDockerJob(job)
                }
            }
        }
    }

    private fun retryDockerJob(job: DockerJob) {
        when {
            job.command.startsWith("engine compose up:") -> {
                val dir = File(job.command.removePrefix("engine compose up:").trim())
                runComposeUp(dir, job.title)
            }
            job.command.startsWith("engine docker build:") -> {
                val dir = File(job.command.removePrefix("engine docker build:").trim())
                runImageBuild(dir, job.title)
            }
            job.command.startsWith("engine action:") -> {
                openJobLog(job)
                appendEngineJobOutput(job.id, "Retry for this Engine API action is not wired yet; use the visible action button instead.")
            }
            else -> openDockerTerminal(job.title, job.command, job.group)
        }
    }

    private fun jobStatusText(job: DockerJob): String {
        val elapsed = ((job.endedAt ?: System.currentTimeMillis()) - job.startedAt).coerceAtLeast(0) / 1000
        val activity = if (job.exitCode == null) " ${jobActivityFrame()}" else ""
        return when {
            job.exitCode == null -> getString(R.string.job_status_running_fmt, elapsed) + activity
            job.exitCode == 0 -> getString(R.string.job_status_done_fmt, elapsed)
            job.exitCode == -129 -> getString(R.string.job_status_stopped_fmt, elapsed)
            job.exitCode == -130 -> getString(R.string.job_status_interrupted_fmt, elapsed)
            else -> getString(R.string.job_status_failed_fmt, job.exitCode ?: -1, elapsed)
        }
    }

    private fun jobProgressText(job: DockerJob): String {
        val progress = job.progress.takeIf { it.isNotBlank() }
        if (job.exitCode != null) return progress.orEmpty()
        return progress ?: getString(R.string.job_activity_fmt, jobActivityFrame())
    }

    private fun jobActivityFrame(): String {
        val frames = charArrayOf('|', '/', '-', '\\')
        return frames[((System.currentTimeMillis() / 250L) % frames.size).toInt()].toString()
    }

    private fun openJobLog(job: DockerJob) {
        val existing = toolTabs.indexOfFirst { it.key == job.toolKey }
        if (existing >= 0) {
            switchTool(existing)
            return
        }
        if (job.exitCode == null) {
            openLiveJobLog(job, switchTo = true)
            return
        }
        val log = listOf(
            job.title,
            jobStatusText(job),
            job.progress,
            job.command,
            "",
            readJobLogText(job).ifBlank { job.output.joinToString("\n") },
        ).joinToString("\n").trimEnd()
        val terminal = terminalLogPane()
        toolTabs += ToolTab(
            job.group,
            getString(R.string.terminal_job_log_fmt, job.title),
            ToolKind.Terminal,
            terminal.view,
            key = job.toolKey,
        )
        terminal.write(log + "\r\n")
        switchTool(toolTabs.lastIndex)
    }

    private fun openLiveJobLog(job: DockerJob, switchTo: Boolean) {
        val existing = toolTabs.indexOfFirst { it.key == job.toolKey }
        if (existing >= 0) {
            updateLiveJobView(job)
            if (switchTo) switchTool(existing)
            return
        }
        val header = TextView(this).apply {
            textSize = 13f
            typeface = Typeface.DEFAULT_BOLD
            setPadding(18, 14, 18, 4)
        }
        val progress = TextView(this).apply {
            textSize = 12f
            typeface = Typeface.MONOSPACE
            setPadding(18, 0, 18, 6)
        }
        val services = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(14, 2, 14, 8)
        }
        val terminal = terminalLogPane()
        val view = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(header)
            addView(progress)
            addView(services)
            addView(terminal.view, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1f,
            ))
        }
        liveJobViews[job.id] = LiveJobView(header, progress, services, terminal)
        toolTabs += ToolTab(
            job.group,
            getString(R.string.terminal_job_log_fmt, job.title),
            ToolKind.Terminal,
            view,
            key = job.toolKey,
        )
        val persistedLog = readJobLogText(job)
        if (persistedLog.isNotBlank()) {
            terminal.write(ensureJobTerminalPrelude(job, persistedLog))
        } else if (job.output.isNotEmpty()) {
            terminal.write(jobTerminalPrelude(job) + job.output.joinToString("\r\n") + "\r\n")
        } else {
            terminal.write(jobTerminalPrelude(job))
        }
        updateLiveJobView(job)
        if (switchTo) switchTool(toolTabs.lastIndex) else renderToolChrome()
    }

    private fun updateLiveJobView(job: DockerJob) {
        val live = liveJobViews[job.id] ?: return
        live.header.text = listOf(job.title, jobStatusText(job)).joinToString("  ")
        live.progress.text = listOf(jobProgressText(job), job.command)
            .filter { it.isNotBlank() }
            .joinToString("\n")
        live.services.removeAllViews()
        liveJobServiceLinks(job).forEach { (label, url) ->
            live.services.addView(Button(this).apply {
                text = serviceActionTitle(label, url)
                isAllCaps = false
                setOnClickListener { openServiceUrl(url) }
            })
        }
    }

    private fun updateLiveJobViews() {
        dockerJobs.forEach { updateLiveJobView(it) }
    }

    private fun tickRunningJobs() {
        if (dockerJobs.none { it.exitCode == null } && daemonOperations.isEmpty()) return
        updateLiveJobViews()
        if (currentTab in setOf(Tab.Overview, Tab.Compose, Tab.Dockerfiles, Tab.Images, Tab.Containers)) {
            scheduleJobRenderUpdate(0L)
        }
    }

    private fun liveJobServiceLinks(job: DockerJob): List<Pair<String, String>> {
        val composeDir = job.command
            .takeIf { it.startsWith("engine compose up:") }
            ?.removePrefix("engine compose up:")
            ?.trim()
            ?.takeIf { it.isNotBlank() }
            ?.let { File(it) }
            ?: return emptyList()
        return projectServiceUrls(parseComposeServices(composeDir)).map { it.label to it.url }
    }

    private fun stopDockerJob(jobId: String) {
        val job = dockerJobs.firstOrNull { it.id == jobId } ?: return
        if (job.exitCode != null) return
        val index = toolTabs.indexOfFirst { it.key == job.toolKey }
        if (index >= 0) {
            toolTabs[index].bridge?.close()
        }
        job.exitCode = -129
        job.status = getString(R.string.job_stopped)
        job.endedAt = System.currentTimeMillis()
        dockerJobBuffers.remove(jobId)
        dockerJobPendingCarriageReturn.remove(jobId)
        job.output += "[pdocker] job stopped from UI"
        appendPersistentJobLog(job.id, "[pdocker] job stopped from UI\n")
        job.progress = getString(R.string.job_stopped)
        while (job.output.size > MAX_JOB_LINES) job.output.removeAt(0)
        saveDockerJobs()
        updateLiveJobView(job)
        renderContent()
    }

    private fun handleDockerJobOutput(jobId: String, bytes: ByteArray) {
        val chunk = bytes.toString(Charsets.UTF_8)
        ui.post {
            val job = dockerJobs.firstOrNull { it.id == jobId } ?: return@post
            val text = terminalDisplayText(chunk)
            appendLiveJobTerminal(jobId, text)
            appendPersistentJobLog(jobId, text)
            recordJobTerminalOutput(job, jobId, text)
            scheduleDockerJobsSave()
            scheduleJobRenderUpdate()
        }
    }

    private fun scheduleJobRenderUpdate(delayMs: Long = 500L) {
        if (jobRenderScheduled) return
        jobRenderScheduled = true
        ui.postDelayed({
            jobRenderScheduled = false
            updateLiveJobViews()
            if (currentTab in setOf(Tab.Overview, Tab.Compose, Tab.Dockerfiles, Tab.Images, Tab.Containers)) {
                renderContent()
            }
        }, delayMs)
    }

    private fun appendEngineJobOutput(jobId: String, line: String) {
        ui.post {
            val job = dockerJobs.firstOrNull { it.id == jobId } ?: return@post
            val text = terminalRecordText(line)
            val displayText = terminalDisplayText(text)
            appendLiveJobTerminal(jobId, displayText)
            appendPersistentJobLog(jobId, displayText)
            recordJobTerminalOutput(job, jobId, displayText)
            scheduleDockerJobsSave()
            scheduleJobRenderUpdate()
        }
    }

    private fun terminalRecordText(text: String): String =
        normalizeTerminalNewlines(if (text.endsWith("\n") || text.endsWith("\r")) text else "$text\r\n")

    private fun jobTerminalPrelude(job: DockerJob): String =
        terminalRecordText(
            listOf(
                "[pdocker] job=${job.title} group=${job.group}",
                "[pdocker] command=${job.command}",
                "",
            ).joinToString("\n")
        )

    private fun ensureJobTerminalPrelude(job: DockerJob, text: String): String =
        if ("[pdocker] command=" in text.take(2048) || "[pdocker command]" in text.take(2048)) text else jobTerminalPrelude(job) + text

    private fun normalizeTerminalNewlines(text: String): String {
        val out = StringBuilder(text.length + 8)
        var previous = '\u0000'
        text.forEach { ch ->
            if (ch == '\n' && previous != '\r') out.append('\r')
            out.append(ch)
            previous = ch
        }
        return out.toString()
    }

    private fun terminalDisplayText(text: String): String {
        val out = StringBuilder(text.length + 16)
        text.forEachIndexed { index, ch ->
            if (ch == '\r' && text.getOrNull(index + 1) != '\n' && index < text.lastIndex) {
                out.append('\r').append("\u001B[2K")
            } else {
                out.append(ch)
            }
        }
        return out.toString()
    }

    private fun appendLiveJobTerminal(jobId: String, text: String) {
        liveJobViews[jobId]?.terminal?.write(text)
    }

    private fun recordJobTerminalOutput(job: DockerJob, jobId: String, text: String) {
        var current = dockerJobBuffers.getOrDefault(jobId, "")
        var index = 0
        if (dockerJobPendingCarriageReturn.remove(jobId) && text.firstOrNull() != '\n') {
            updateCurrentJobProgress(job, current)
            current = ""
        }
        while (index < text.length) {
            when (val ch = text[index]) {
                '\r' -> {
                    when {
                        index + 1 >= text.length -> dockerJobPendingCarriageReturn += jobId
                        text[index + 1] == '\n' -> Unit
                        else -> {
                            updateCurrentJobProgress(job, current)
                            current = ""
                        }
                    }
                }
                '\n' -> {
                    commitJobOutputLine(job, jobId, current)
                    current = ""
                }
                '\u0000' -> Unit
                else -> current += ch
            }
            index += 1
        }
        if (current.length > 4096) current = current.takeLast(4096)
        dockerJobBuffers[jobId] = current
        updateCurrentJobProgress(job, current)
    }

    private fun updateCurrentJobProgress(job: DockerJob, rawLine: String) {
        val line = cleanTerminalLine(rawLine)
        if (line.isNotBlank()) updateDockerJobProgress(job, line)
    }

    private fun commitJobOutputLine(job: DockerJob, jobId: String, rawLine: String) {
        val line = cleanTerminalLine(rawLine)
        if (line.isBlank()) return
        val marker = Regex("__PDOCKER_JOB_EXIT:${Regex.escape(jobId)}:(\\d+)__").find(line)
        if (marker != null) {
            job.exitCode = marker.groupValues[1].toIntOrNull()
            job.status = if (job.exitCode == 0) getString(R.string.job_done) else getString(R.string.job_failed)
            job.progress = if (job.exitCode == 0) getString(R.string.job_done) else getString(R.string.job_failed)
            job.endedAt = System.currentTimeMillis()
            dockerJobBuffers.remove(jobId)
            dockerJobPendingCarriageReturn.remove(jobId)
            return
        }
        updateDockerJobProgress(job, line)
        if (job.output.lastOrNull() != line) job.output += line
        while (job.output.size > MAX_JOB_LINES) job.output.removeAt(0)
    }

    private fun cleanTerminalLine(rawLine: String): String =
        ansiControlRegex.replace(rawLine, "")
            .filter { it >= ' ' || it == '\t' }
            .trim()

    private fun finishEngineJob(jobId: String, exitCode: Int, output: String) {
        val job = dockerJobs.firstOrNull { it.id == jobId } ?: return
        job.exitCode = exitCode
        job.status = if (exitCode == 0) getString(R.string.job_done) else getString(R.string.job_failed)
        job.progress = if (exitCode == 0) getString(R.string.job_done) else getString(R.string.job_failed)
        job.endedAt = System.currentTimeMillis()
        val existing = job.output.toMutableSet()
        val terminalBackfill = mutableListOf<String>()
        output.lineSequence()
            .map { cleanTerminalLine(it) }
            .filter { it.isNotBlank() }
            .forEach { line ->
                if (job.output.lastOrNull() != line) {
                    job.output += line
                    if (existing.add(line)) terminalBackfill += line
                }
                while (job.output.size > MAX_JOB_LINES) job.output.removeAt(0)
            }
        if (terminalBackfill.isNotEmpty()) {
            val text = terminalRecordText(terminalBackfill.joinToString("\n"))
            appendLiveJobTerminal(jobId, text)
            appendPersistentJobLog(jobId, text)
        }
        saveDockerJobs()
        updateLiveJobView(job)
    }

    private fun handleEngineJobFinished(job: DockerJob) {
        updateLiveJobView(job)
        if (job.exitCode != 0 || !job.command.startsWith("engine compose up:")) return
        scheduleDocumentsSyncToTree(job)
        val urls = liveJobServiceLinks(job)
        if (urls.isNotEmpty()) {
            appendEngineJobOutput(job.id, urls.joinToString("\n") { (label, url) -> "Open $label $url" })
            val autoOpen = liveJobAutoOpenService(job)
            if (autoOpen != null) openServiceWhenReady(job.id, autoOpen.first, autoOpen.second)
        }
    }

    private fun scheduleDocumentsSyncToTree(job: DockerJob) {
        val metadata = documentsTreeMetadata()
        if (metadata.writeAccess != DocumentsWriteAccess.SafMediated) return
        val grants = safDocumentsMediator().persistedGrantState()
        if (!grants.available) {
            appendEngineJobOutput(job.id, "Documents SAF sync skipped: persisted read/write grant is missing")
            return
        }
        thread(isDaemon = true, name = "pdocker-documents-sync") {
            Thread.sleep(1_200)
            val report = runCatching { safDocumentsMediator().syncToTree(evictMirrorPayload = true) }
                .getOrElse {
                    JSONObject()
                        .put("Success", false)
                        .put("Error", it.message ?: it.toString())
                }
            val success = report.optBoolean("Success", false)
            val files = report.optInt("Files", 0)
            val bytes = report.optLong("Bytes", 0L)
            val evicted = report.optInt("EvictedMirrorFiles", 0)
            val errors = report.optJSONArray("Errors")?.length() ?: 0
            val summary = if (success) {
                "Documents SAF sync complete: files=$files bytes=$bytes evicted=$evicted"
            } else {
                "Documents SAF sync failed: files=$files bytes=$bytes evicted=$evicted errors=$errors ${report.optString("Error")}".trim()
            }
            appendEngineJobOutput(job.id, summary)
            refreshStorageMetricsAsync(force = true)
        }
    }

    private fun liveJobAutoOpenService(job: DockerJob): Pair<String, String>? {
        val composeDir = job.command
            .takeIf { it.startsWith("engine compose up:") }
            ?.removePrefix("engine compose up:")
            ?.trim()
            ?.takeIf { it.isNotBlank() }
            ?.let { File(it) }
            ?: return null
        return parseComposeServices(composeDir)
            .asSequence()
            .mapNotNull { composeServiceAutoOpenUrl(it) }
            .firstOrNull()
    }

    private fun openServiceWhenReady(jobId: String, label: String, url: String) {
        if (!isHttpServiceUrl(url)) {
            appendEngineJobOutput(jobId, "$label ready: $url (external client)")
            openServiceUrl(url)
            return
        }
        thread(isDaemon = true, name = "pdocker-service-open") {
            repeat(45) { attempt ->
                val result = probeServiceUrl(url)
                if (result.startsWith("HTTP ")) {
                    ui.post {
                        serviceHealth[url] = result
                        serviceHealthCheckedAt[url] = System.currentTimeMillis()
                        appendEngineJobOutput(jobId, "$label ready: $url ($result)")
                        openServiceUrl(url)
                    }
                    return@thread
                }
                if (attempt == 0 || attempt % 5 == 4) {
                    ui.post {
                        serviceHealth[url] = result
                        serviceHealthCheckedAt[url] = System.currentTimeMillis()
                        appendEngineJobOutput(jobId, "$label waiting: $url ($result)")
                    }
                }
                Thread.sleep(1000)
            }
            ui.post { appendEngineJobOutput(jobId, "$label not reachable yet: $url") }
        }
    }

    private fun updateDockerJobProgress(job: DockerJob, line: String) {
        val progress = dockerJobProgressLine(line) ?: return
        job.progress = progress.take(180)
    }

    private fun dockerJobProgressLine(line: String): String? {
        val cleaned = line
            .removePrefix("[+] ")
            .replace(Regex("\\s+"), " ")
            .trim()
        if (cleaned.isBlank()) return null
        val buildPrefixes = listOf(
            "Step:",
            "snapshotting layer:",
            "Successfully built",
            "Successfully tagged",
            "ERROR:",
        )
        if (buildPrefixes.any { cleaned.startsWith(it) }) return cleaned
        val pullPrefixes = listOf("Pulling ", "Status: Downloaded", "Downloaded newer image", "Image is up to date")
        if (pullPrefixes.any { cleaned.startsWith(it) }) return cleaned
        val composeWords = listOf(
            "Building",
            "Pulling",
            "Creating",
            "Created",
            "Starting",
            "Started",
            "Running",
            "Recreating",
            "Removing",
            "Removed",
        )
        if (composeWords.any { Regex("(^|\\s)$it($|\\s)").containsMatchIn(cleaned) }) return cleaned
        return null
    }

    private fun trimDockerJobs() {
        while (dockerJobs.size > MAX_JOB_HISTORY) {
            val removed = dockerJobs.removeAt(dockerJobs.lastIndex)
            if (removed.exitCode != null) runCatching { jobLogFile(removed.id).delete() }
        }
    }

    private fun jobLogFile(jobId: String): File =
        File(pdockerHome, "logs/jobs/$jobId.log")

    private fun appendPersistentJobLog(jobId: String, text: String) {
        if (text.isEmpty()) return
        val bytes = text.toByteArray(Charsets.UTF_8)
        logIo.execute {
            runCatching {
                val file = jobLogFile(jobId)
                file.parentFile?.mkdirs()
                FileOutputStream(file, true).use { it.write(bytes) }
            }
        }
    }

    private fun readJobLogText(job: DockerJob): String {
        val file = jobLogFile(job.id)
        if (!file.isFile) return ""
        return runCatching {
            val bytes = file.readBytes()
            val start = (bytes.size - MAX_JOB_LOG_VIEW_BYTES).coerceAtLeast(0)
            val prefix = if (start > 0) "[pdocker] log truncated to last ${MAX_JOB_LOG_VIEW_BYTES / 1024} KiB\n" else ""
            prefix + bytes.copyOfRange(start, bytes.size).toString(Charsets.UTF_8)
        }.getOrDefault("")
    }

    private fun scheduleDockerJobsSave() {
        dockerJobsDirty = true
        if (dockerJobsSaveScheduled) return
        dockerJobsSaveScheduled = true
        ui.postDelayed({
            dockerJobsSaveScheduled = false
            flushDockerJobsSave()
        }, 1500L)
    }

    private fun flushDockerJobsSave() {
        if (!dockerJobsDirty) return
        dockerJobsDirty = false
        saveDockerJobs()
    }

    private fun loadDockerJobs() {
        val file = File(pdockerHome, "jobs.json")
        val arr = runCatching { JSONArray(file.readText()) }.getOrNull() ?: return
        dockerJobs.clear()
        var migrated = false
        for (i in 0 until arr.length()) {
            val obj = arr.optJSONObject(i) ?: continue
            val lines = obj.optJSONArray("output") ?: JSONArray()
            val command = normalizeDockerCommand(obj.optString("command"))
            val job = DockerJob(
                id = obj.optString("id"),
                title = obj.optString("title"),
                detail = obj.optString("detail"),
                command = command,
                group = obj.optString("group"),
                toolKey = obj.optString("toolKey"),
                status = obj.optString("status"),
                exitCode = if (obj.has("exitCode") && !obj.isNull("exitCode")) obj.optInt("exitCode") else null,
                startedAt = obj.optLong("startedAt", System.currentTimeMillis()),
                endedAt = if (obj.has("endedAt") && !obj.isNull("endedAt")) obj.optLong("endedAt") else null,
                progress = obj.optString("progress"),
                output = (0 until lines.length()).mapNotNull { j ->
                    lines.optString(j).takeIf { it.isNotBlank() }
                }.toMutableList(),
            )
            if (job.exitCode == null) {
                val line = "[pdocker] UI was restarted; reconnecting to daemon operation"
                if (job.output.lastOrNull() != line) job.output += line
                job.status = getString(R.string.job_running)
                job.progress = line
                migrated = true
            }
            dockerJobs += job
        }
        trimDockerJobs()
        if (migrated) saveDockerJobs()
    }

    private fun saveDockerJobs() {
        val arr = JSONArray()
        dockerJobs.forEach { job ->
            arr.put(JSONObject().apply {
                put("id", job.id)
                put("title", job.title)
                put("detail", job.detail)
                put("command", job.command)
                put("group", job.group)
                put("toolKey", job.toolKey)
                put("status", job.status)
                if (job.exitCode == null) {
                    put("exitCode", JSONObject.NULL)
                } else {
                    put("exitCode", job.exitCode)
                }
                put("startedAt", job.startedAt)
                if (job.endedAt == null) {
                    put("endedAt", JSONObject.NULL)
                } else {
                    put("endedAt", job.endedAt)
                }
                put("progress", job.progress)
                put("output", JSONArray().apply { job.output.forEach { put(it) } })
            })
        }
        File(pdockerHome, "jobs.json").apply {
            parentFile?.mkdirs()
            writeText(arr.toString(2))
        }
    }

    private fun refreshStatus() {
        val sock = File(pdockerHome, "pdockerd.sock")
        if (!sock.exists()) {
            status.text = getString(R.string.status_socket_absent)
            return
        }
        thread(isDaemon = true, name = "pdockerd-ping") {
            val msg = runCatching {
                LocalSocket().use { ls ->
                    ls.connect(LocalSocketAddress(sock.absolutePath,
                        LocalSocketAddress.Namespace.FILESYSTEM))
                    ls.soTimeout = 500
                    ls.outputStream.write(
                        "GET /_ping HTTP/1.0\r\nHost: pdocker\r\n\r\n".toByteArray()
                    )
                    ls.outputStream.flush()
                    val resp = ls.inputStream.readBytes().toString(Charsets.US_ASCII)
                    if ("200 OK" in resp && resp.trimEnd().endsWith("OK")) getString(R.string.status_running)
                    else getString(R.string.status_unexpected_response)
                }
            }.getOrElse { getString(R.string.status_ping_failed, it.message.orEmpty()) }
            ui.post { status.text = getString(R.string.status_pdocker_fmt, msg) }
        }
    }

    private fun addSection(text: String) {
        content.addView(TextView(this).apply {
            this.text = text
            textSize = 18f
            typeface = Typeface.DEFAULT_BOLD
            setPadding(0, 22, 0, 8)
        })
    }

    private fun addAction(label: String, detail: String, onClick: () -> Unit) {
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            isClickable = true
            setPadding(0, 16, 0, 16)
            setOnClickListener { onClick() }
            addView(TextView(this@MainActivity).apply {
                text = label
                textSize = 16f
                setSingleLine(true)
                ellipsize = TextUtils.TruncateAt.END
            })
            addView(TextView(this@MainActivity).apply {
                text = detail
                textSize = 12f
                alpha = 0.72f
                setSingleLine(true)
                ellipsize = TextUtils.TruncateAt.MIDDLE
            })
            content.addView(this)
            addDivider()
        }
    }

    private fun addMetric(label: String, value: String) {
        addAction(label, value) {}
    }

    private fun addWidget(title: String, value: String, detail: String, detailLines: Int = 3, onClick: (() -> Unit)? = null) {
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(18, 18, 18, 18)
            if (onClick != null) {
                isClickable = true
                setOnClickListener { onClick() }
            }
            addView(TextView(this@MainActivity).apply {
                text = title
                textSize = 13f
                alpha = 0.72f
                setSingleLine(true)
                ellipsize = TextUtils.TruncateAt.END
            })
            addView(TextView(this@MainActivity).apply {
                text = value
                textSize = 20f
                typeface = Typeface.DEFAULT_BOLD
                setSingleLine(true)
                ellipsize = TextUtils.TruncateAt.END
            })
            addView(TextView(this@MainActivity).apply {
                text = detail
                textSize = 12f
                alpha = 0.78f
                maxLines = detailLines
                ellipsize = TextUtils.TruncateAt.END
            })
            content.addView(this)
            addDivider()
        }
    }

    private fun addImageReferenceGraph(title: String, rows: List<ImageReferenceGraphRow>) {
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(18, 18, 18, 18)
            addView(TextView(this@MainActivity).apply {
                text = title
                textSize = 13f
                alpha = 0.72f
                setSingleLine(true)
                ellipsize = TextUtils.TruncateAt.END
            })
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(0, 8, 0, 12)
                addImageGraphLegend(this, ImageGraphKind.Image, getString(R.string.image_reference_legend_image))
                addImageGraphLegend(this, ImageGraphKind.Cache, getString(R.string.image_reference_legend_cache))
                addImageGraphLegend(this, ImageGraphKind.Compose, getString(R.string.image_reference_legend_reference))
            })
            addView(HorizontalScrollView(this@MainActivity).apply {
                isFillViewport = false
                addView(ImageGraphLayout(this@MainActivity).apply {
                    orientation = LinearLayout.VERTICAL
                    rows.forEach { row -> addImageGraphRow(this, row) }
                    setGraphRows(rows)
                })
            })
            content.addView(this)
            addDivider()
        }
    }

    private fun addImageGraphLegend(parent: LinearLayout, kind: ImageGraphKind, text: String) {
        parent.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, 0, dp(14), 0)
            addView(View(this@MainActivity).apply {
                background = solidRoundDrawable(kind.color)
            }, LinearLayout.LayoutParams(dp(10), dp(10)))
            addView(TextView(this@MainActivity).apply {
                this.text = text
                textSize = 11f
                alpha = 0.78f
                setPadding(dp(4), 0, 0, 0)
            })
        })
    }

    private fun addImageGraphRow(parent: LinearLayout, row: ImageReferenceGraphRow) {
        val rowHeight = when {
            row.actions.isNotEmpty() -> dp(116)
            row.detail.count { it == '\n' } >= 2 -> dp(96)
            else -> dp(58)
        }
        parent.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(View(this@MainActivity), LinearLayout.LayoutParams(dp(24 + row.depth * 18), rowHeight))
            addView(TextView(this@MainActivity).apply {
                text = row.kind.label
                textSize = 10f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(row.kind.color)
                setPadding(dp(7), dp(3), dp(7), dp(3))
                background = tintedRoundDrawable(row.kind.color)
            })
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(8), 0, 0, 0)
                addView(TextView(this@MainActivity).apply {
                    text = row.title
                    textSize = 13f
                    typeface = if (row.kind == ImageGraphKind.Image) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
                    setSingleLine(true)
                    ellipsize = TextUtils.TruncateAt.END
                })
                if (row.detail.isNotBlank()) {
                    addView(TextView(this@MainActivity).apply {
                        text = row.detail
                        textSize = 11f
                        alpha = 0.72f
                        maxLines = 5
                        ellipsize = TextUtils.TruncateAt.MIDDLE
                    })
                }
                if (row.actions.isNotEmpty()) {
                    addView(LinearLayout(this@MainActivity).apply {
                        orientation = LinearLayout.HORIZONTAL
                        setPadding(0, dp(6), 0, 0)
                        row.actions.forEach { action ->
                            addView(Button(this@MainActivity).apply {
                                text = action.label
                                textSize = 10f
                                minHeight = 0
                                minimumHeight = 0
                                minWidth = 0
                                minimumWidth = 0
                                setPadding(dp(8), 0, dp(8), 0)
                                setOnClickListener { action.run() }
                            }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(34)).apply {
                                setMargins(0, 0, dp(6), 0)
                            })
                        }
                    })
                }
            }, LinearLayout.LayoutParams(dp(360), LinearLayout.LayoutParams.WRAP_CONTENT))
        }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, rowHeight))
    }

    private class MemoryLayerView(context: Context) : View(context) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 2.5f * resources.displayMetrics.density
            color = 0x88aeeaff.toInt()
        }
        private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = 0xffe8e8e8.toInt()
            textSize = 12f * resources.displayMetrics.scaledDensity
            typeface = Typeface.DEFAULT_BOLD
        }
        private val smallTextPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = 0xffd0d0d0.toInt()
            textSize = 10f * resources.displayMetrics.scaledDensity
        }
        private var snapshot = MemoryLayerSnapshot(
            memTotal = 0,
            memAvailable = 0,
            swapTotal = 0,
            swapFree = 0,
            pdockerProcessCount = 0,
            pdockerVmSize = 0,
            pdockerRss = 0,
            pdockerSwap = 0,
            appVmSize = 0,
            appVmRss = 0,
            appVmData = 0,
            appVmStk = 0,
            appVmSwap = 0,
            javaHeapMax = 0,
            javaHeapUsed = 0,
            managedReserveBytes = 0,
            managedResidentBytes = 0,
            managedBackingBytes = 0,
            managedPageIns = 0,
            managedPageOuts = 0,
            managedBytesIn = 0,
            managedBytesOut = 0,
            managedDirtyPageOuts = 0,
            managedElapsedNs = 0,
            transparentRegistered = false,
            transparentSigsegvStops = 0,
            transparentLastMmapLen = 0,
            transparentPendingAfterEntry = 0,
            transparentMaxResidentPages = 0,
            transparentBytesIn = 0,
            transparentBytesOut = 0,
            transparentDirtyPageOuts = 0,
            artifactCreatedAtEpoch = 0,
            artifactStatus = "",
            artifactAgeSeconds = 0,
            source = "live /proc only",
        )

        init {
            setLayerType(LAYER_TYPE_SOFTWARE, null)
        }

        fun setSnapshot(snapshot: MemoryLayerSnapshot) {
            this.snapshot = snapshot
            invalidate()
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            drawBackgroundGrid(canvas)
            val density = resources.displayMetrics.density
            val pad = 16f * density
            val barLeft = pad
            val barRight = width - pad
            val globalScaleBytes = maxOf(
                snapshot.memTotal,
                snapshot.swapTotal,
                snapshot.pdockerVmSize,
                snapshot.appVmSize,
                snapshot.javaHeapMax,
                snapshot.managedReserveBytes,
                snapshot.managedBackingBytes,
                1L,
            )
            var y = pad + 18f * density
            drawHud(canvas, barLeft, barRight, y)
            y += 52f * density
            drawTitle(canvas, "OS-governed allocation", y)
            y += 8f * density
            y = drawBar(
                canvas,
                y,
                "RAM",
                snapshot.memTotal,
                listOf(
                    Segment("pdocker RSS", snapshot.pdockerRss.coerceAtMost((snapshot.memTotal - snapshot.memAvailable).coerceAtLeast(0L)), 0xff58ffd2.toInt()),
                    Segment("other used", ((snapshot.memTotal - snapshot.memAvailable).coerceAtLeast(0L) - snapshot.pdockerRss).coerceAtLeast(0L), 0xffc75b5b.toInt()),
                    Segment("available", snapshot.memAvailable, 0xff5fbf7a.toInt()),
                ),
                barLeft,
                barRight,
                globalScaleBytes,
            ) + 13f * density
            y = drawBar(
                canvas,
                y,
                "swap/zram",
                snapshot.swapTotal,
                listOf(
                    Segment("pdocker swap", snapshot.pdockerSwap.coerceAtMost((snapshot.swapTotal - snapshot.swapFree).coerceAtLeast(0L)), 0xff58ffd2.toInt()),
                    Segment("other used", ((snapshot.swapTotal - snapshot.swapFree).coerceAtLeast(0L) - snapshot.pdockerSwap).coerceAtLeast(0L), 0xffcaa24a.toInt()),
                    Segment("free", snapshot.swapFree, 0xff6aa6d9.toInt()),
                ),
                barLeft,
                barRight,
                globalScaleBytes,
            ) + 22f * density
            drawTitle(canvas, "App process view", y)
            y += 8f * density
            y = drawBar(
                canvas,
                y,
                "process",
                maxOf(snapshot.appVmSize, snapshot.javaHeapMax, 1L),
                listOf(
                    Segment("RSS", snapshot.appVmRss, 0xff8e6bd8.toInt()),
                    Segment("Java heap", snapshot.javaHeapUsed, 0xff5ba8c7.toInt()),
                    Segment("VmSwap", snapshot.appVmSwap, 0xffcc7c5a.toInt()),
                ),
                barLeft,
                barRight,
                globalScaleBytes,
            ) + 22f * density
            drawTitle(canvas, "pdocker managed virtual-memory skin", y)
            y += 8f * density
            val pagerBarTop = y
            y = drawBar(
                canvas,
                y,
                if (snapshot.transparentRegistered) "SIGSEGV pager" else "pager model",
                maxOf(snapshot.managedReserveBytes, snapshot.managedBackingBytes, snapshot.managedResidentBytes, 1L),
                listOf(
                    Segment("reserved VA", snapshot.managedReserveBytes, 0xff707070.toInt()),
                    Segment("resident", snapshot.managedResidentBytes, 0xff4cc2a0.toInt()),
                    Segment("backing", snapshot.managedBackingBytes, 0xffd6904d.toInt()),
                ),
                barLeft,
                barRight,
                globalScaleBytes,
            )
            val footerY = (height - pad - 2f * density).coerceAtLeast(y + 18f * density)
            paint.style = Paint.Style.STROKE
            paint.strokeWidth = 2.2f * density
            paint.color = if (snapshot.transparentRegistered) 0xff58ffd2.toInt() else 0xff888888.toInt()
            val midX = (barLeft + barRight) / 2f
            paint.setShadowLayer(10f, 0f, 0f, paint.color)
            canvas.drawLine(midX, (pagerBarTop - 6f * density).coerceAtLeast(pad), midX, footerY - 16f * density, paint)
            paint.clearShadowLayer()
            val sourceLabel = if (snapshot.artifactCreatedAtEpoch > 0L) {
                "past self-test: ${snapshot.source}, age ${formatAgeForChart(snapshot.artifactAgeSeconds, snapshot.artifactCreatedAtEpoch)}"
            } else {
                "source: ${snapshot.source}"
            }
            canvas.drawText(
                "$sourceLabel; ${(snapshot.managedPageIns + snapshot.managedPageOuts)} page ops / ${compactNs(snapshot.managedElapsedNs)}",
                barLeft,
                footerY - 12f * density,
                smallTextPaint,
            )
            canvas.drawText(
                "wrapper: PROT_NONE → resident window → backing file; transparent bytes in/out ${compactBytes(snapshot.transparentBytesIn)}/${compactBytes(snapshot.transparentBytesOut)}",
                barLeft,
                footerY + 2f * density,
                smallTextPaint,
            )
        }

        private data class Segment(val label: String, val bytes: Long, val color: Int)

        private fun drawBackgroundGrid(canvas: Canvas) {
            val d = resources.displayMetrics.density
            paint.style = Paint.Style.FILL
            paint.shader = LinearGradient(
                0f, 0f, width.toFloat(), height.toFloat(),
                0xff121722.toInt(), 0xff07090d.toInt(), Shader.TileMode.CLAMP,
            )
            canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), paint)
            paint.shader = null
            paint.style = Paint.Style.STROKE
            paint.strokeWidth = 1f
            paint.color = 0x2240d8ff
            val step = 28f * d
            var x = -height.toFloat()
            while (x < width + height) {
                canvas.drawLine(x, height.toFloat(), x + height, 0f, paint)
                x += step
            }
            var y = 0f
            while (y < height) {
                canvas.drawLine(0f, y, width.toFloat(), y, paint)
                y += step
            }
        }

        private fun drawTitle(canvas: Canvas, label: String, y: Float) {
            canvas.drawText(label, 16f * resources.displayMetrics.density, y, textPaint)
        }

        private fun drawHud(canvas: Canvas, left: Float, right: Float, baseline: Float) {
            val density = resources.displayMetrics.density
            val resident = snapshot.managedResidentBytes.coerceAtLeast(1L)
            val reserve = snapshot.managedReserveBytes.coerceAtLeast(0L)
            val multiplier = if (reserve > 0L) reserve.toDouble() / resident.toDouble() else 0.0
            val title = "Guest memory illusion"
            val detail = if (reserve > 0L) {
                "container sees ${compactBytes(reserve)}; Android keeps ${compactBytes(snapshot.managedResidentBytes)} hot (${String.format("%.1f×", multiplier)} headroom); pdocker RSS ${compactBytes(snapshot.pdockerRss)}"
            } else {
                "pager ready; OS share now pdocker RSS ${compactBytes(snapshot.pdockerRss)} / swap ${compactBytes(snapshot.pdockerSwap)}"
            }
            val boxTop = baseline - 20f * density
            val boxBottom = baseline + 34f * density
            paint.style = Paint.Style.FILL
            paint.shader = LinearGradient(left, boxTop, right, boxBottom, 0x6638e8ff, 0x221f5dff, Shader.TileMode.CLAMP)
            canvas.drawRoundRect(left, boxTop, right, boxBottom, 16f * density, 16f * density, paint)
            paint.shader = null
            paint.style = Paint.Style.STROKE
            paint.strokeWidth = 1.2f * density
            paint.color = if (snapshot.transparentRegistered) 0xff58ffd2.toInt() else 0xff607080.toInt()
            canvas.drawRoundRect(left, boxTop, right, boxBottom, 16f * density, 16f * density, paint)
            canvas.drawText(title, left + 12f * density, baseline - 1f * density, textPaint)
            canvas.drawText(ellipsizeForWidth(detail, right - left - 24f * density), left + 12f * density, baseline + 20f * density, smallTextPaint)
        }

        private fun drawBar(
            canvas: Canvas,
            y: Float,
            label: String,
            total: Long,
            segments: List<Segment>,
            left: Float,
            right: Float,
            scaleTotal: Long,
        ): Float {
            val density = resources.displayMetrics.density
            val top = y + 14f * density
            val bottom = top + 22f * density
            val depthX = 11f * density
            val depthY = -7f * density
            drawPrism(canvas, left, top, right, bottom, depthX, depthY, 0xff263040.toInt())
            var x = left
            val scale = scaleTotal.coerceAtLeast(1L).toDouble()
            segments.forEach { segment ->
                val width = ((right - left) * (segment.bytes.coerceAtLeast(0L).toDouble() / scale)).toFloat()
                    .coerceIn(0f, right - x)
                if (width > 0.5f) {
                    drawPrism(canvas, x, top, x + width, bottom, depthX, depthY, segment.color)
                    x += width
                }
            }
            glowPaint.color = 0x88aeeaff.toInt()
            canvas.drawLine(left, top, right + depthX, top + depthY, glowPaint)
            val totalRatio = total.coerceAtLeast(0L).toDouble() / scale
            canvas.drawText("$label  total ${compactBytes(total)} (${String.format("%.1f%%", totalRatio * 100.0)} of chart scale)", left, top - 4f, smallTextPaint)
            val detail = segments.joinToString("  ") { "${it.label}: ${compactBytes(it.bytes)}" }
            canvas.drawText(ellipsizeForWidth(detail, right - left), left, bottom + 15f * density, smallTextPaint)
            return bottom + 17f * density
        }

        private fun formatAgeForChart(ageSeconds: Long, createdAtEpoch: Long): String {
            if (createdAtEpoch <= 0L) return "unknown"
            val days = ageSeconds / 86_400L
            val hours = (ageSeconds % 86_400L) / 3_600L
            val minutes = (ageSeconds % 3_600L) / 60L
            return when {
                days > 0L -> "${days}d ${hours}h"
                hours > 0L -> "${hours}h ${minutes}m"
                minutes > 0L -> "${minutes}m"
                else -> "${ageSeconds}s"
            }
        }

        private fun ellipsizeForWidth(text: String, maxWidth: Float): String {
            if (smallTextPaint.measureText(text) <= maxWidth) return text
            val ellipsis = "…"
            var end = text.length
            while (end > 0 && smallTextPaint.measureText(text.substring(0, end) + ellipsis) > maxWidth) {
                end--
            }
            return if (end <= 0) ellipsis else text.substring(0, end) + ellipsis
        }

        private fun drawPrism(canvas: Canvas, left: Float, top: Float, right: Float, bottom: Float, dx: Float, dy: Float, color: Int) {
            if (right <= left) return
            val front = Path().apply {
                moveTo(left, top)
                lineTo(right, top)
                lineTo(right, bottom)
                lineTo(left, bottom)
                close()
            }
            val topFace = Path().apply {
                moveTo(left, top)
                lineTo(left + dx, top + dy)
                lineTo(right + dx, top + dy)
                lineTo(right, top)
                close()
            }
            val sideFace = Path().apply {
                moveTo(right, top)
                lineTo(right + dx, top + dy)
                lineTo(right + dx, bottom + dy)
                lineTo(right, bottom)
                close()
            }
            paint.style = Paint.Style.FILL
            paint.shader = LinearGradient(left, top, right, bottom, lighten(color), darken(color), Shader.TileMode.CLAMP)
            canvas.drawPath(front, paint)
            paint.shader = null
            paint.color = lighten(color)
            canvas.drawPath(topFace, paint)
            paint.color = darken(color)
            canvas.drawPath(sideFace, paint)
            paint.style = Paint.Style.STROKE
            paint.strokeWidth = 1f * resources.displayMetrics.density
            paint.color = 0x99ffffff.toInt()
            canvas.drawPath(front, paint)
        }

        private fun lighten(color: Int): Int {
            val r = ((color shr 16) and 0xff)
            val g = ((color shr 8) and 0xff)
            val b = (color and 0xff)
            return 0xff000000.toInt() or (minOf(255, r + 42) shl 16) or (minOf(255, g + 42) shl 8) or minOf(255, b + 42)
        }

        private fun darken(color: Int): Int {
            val r = ((color shr 16) and 0xff)
            val g = ((color shr 8) and 0xff)
            val b = (color and 0xff)
            return 0xff000000.toInt() or (maxOf(0, r - 52) shl 16) or (maxOf(0, g - 52) shl 8) or maxOf(0, b - 52)
        }

        private fun compactBytes(bytes: Long): String {
            val units = arrayOf("B", "KiB", "MiB", "GiB")
            var value = bytes.toDouble()
            var unit = 0
            while (value >= 1024.0 && unit < units.lastIndex) {
                value /= 1024.0
                unit++
            }
            return if (unit == 0) "$bytes B" else String.format("%.1f %s", value, units[unit])
        }

        private fun compactNs(ns: Long): String =
            if (ns <= 0L) "-" else String.format("%.3f ms", ns / 1_000_000.0)
    }

    private fun tintedRoundDrawable(color: Int): GradientDrawable =
        GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(6).toFloat()
            setColor((color and 0x00ffffff) or 0x22000000)
            setStroke(dp(1), color)
        }

    private fun solidRoundDrawable(color: Int): GradientDrawable =
        GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(3).toFloat()
            setColor(color)
        }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt().coerceAtLeast(1)

    private fun addMessage(text: String) {
        content.addView(TextView(this).apply {
            this.text = text
            textSize = 14f
            setPadding(0, 16, 0, 16)
        })
    }

    private fun addDivider() {
        content.addView(View(this).apply {
            alpha = 0.18f
            setBackgroundColor(0xff888888.toInt())
        }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 1))
    }

    private fun imageDirs(): List<File> =
        imageRoot.listFiles()
            ?.filter { File(it, "rootfs").isDirectory }
            ?.sortedBy { it.name }
            .orEmpty()

    private fun imageRef(imageDir: File): String =
        runCatching { File(imageDir, "image_ref").readText().trim() }
            .getOrDefault("")
            .ifBlank { imageDir.name }

    private fun displayImageRef(ref: String): String =
        ref.removePrefix("docker.io/library/").removePrefix("docker.io/")

    private fun imageDetail(imageDir: File, ref: String, info: ImageReferenceInfo?): String {
        val rootfs = File(imageDir, "rootfs")
        val storage = info?.let {
            getString(
                R.string.image_storage_detail_fmt,
                formatBytes(it.viewBytes),
                formatBytes(it.uniqueLayerBytes),
                formatBytes(it.sharedLayerBytes),
            )
        }
        return listOf(
            summarizeRootfs(rootfs),
            getString(R.string.image_version_detail_fmt, imageVersionDetail(ref)),
            storage,
            ref,
            imageDir.name,
        ).filterNotNull().joinToString("\n")
    }

    private fun imageVersionDetail(ref: String): String {
        if ("@" in ref) return ref.substringAfter("@").take(32)
        val last = ref.substringAfterLast("/")
        return last.substringAfterLast(":", "latest")
    }

    private fun imageReferenceInfos(images: List<File>): List<ImageReferenceInfo> {
        val diffIdsByDir = images.associateWith { readImageDiffIds(it) }
        val refCount = diffIdsByDir.values.flatten().groupingBy { it }.eachCount()
        return images.map { image ->
            val ref = imageRef(image)
            val diffIds = diffIdsByDir[image].orEmpty()
            var uniqueLayerBytes = 0L
            var sharedLayerBytes = 0L
            diffIds.distinct().forEach { diffId ->
                val bytes = layerSize(diffId)
                if ((refCount[diffId] ?: 0) > 1) sharedLayerBytes += bytes else uniqueLayerBytes += bytes
            }
            ImageReferenceInfo(
                dir = image,
                ref = ref,
                displayRef = displayImageRef(ref),
                diffIds = diffIds,
                viewBytes = uniqueLayerBytes + sharedLayerBytes,
                uniqueLayerBytes = uniqueLayerBytes,
                sharedLayerBytes = sharedLayerBytes,
                totalLayerBytes = uniqueLayerBytes + sharedLayerBytes,
            )
        }.sortedBy { it.displayRef }
    }

    private fun renderImageCacheHealth(images: List<ImageReferenceInfo>) {
        val health = imageCacheHealth(images)
        val needsMaintenance = health.staleBuildCacheCount > 0 ||
            health.unreferencedLayerCount > 0 ||
            health.tempArtifactCount > 0
        val value = if (needsMaintenance) {
            getString(
                R.string.image_cache_health_attention_fmt,
                health.staleBuildCacheCount,
                health.unreferencedLayerCount,
            )
        } else {
            getString(R.string.image_cache_health_clean)
        }
        addWidget(
            getString(R.string.widget_image_cache_health),
            value,
            getString(
                R.string.image_cache_health_detail_fmt,
                health.layerCount,
                health.referencedLayerCount,
                formatBytes(health.unreferencedLayerBytes),
                health.buildCacheCount,
                health.staleBuildCacheCount,
                health.tempArtifactCount,
            ),
            detailLines = 4,
        ) {
            runPruneBuildCache()
        }
    }

    private fun imageCacheHealth(images: List<ImageReferenceInfo>): ImageCacheHealth {
        val referencedLayers = images
            .flatMap { it.diffIds }
            .map { it.removePrefix("sha256:") }
            .filter { layerIdRegex.matches(it) }
            .toSet()
        val layerDirs = layerRoot.listFiles()
            ?.filter { it.isDirectory && layerIdRegex.matches(it.name) }
            .orEmpty()
        val layerIds = layerDirs.map { it.name }.toSet()
        val unreferencedLayerIds = layerIds - referencedLayers
        val unreferencedBytes = unreferencedLayerIds.sumOf { layerSize(it) }
        val buildCacheFiles = File(pdockerHome, "meta/build-cache")
            .listFiles { file -> file.isFile && file.extension == "json" }
            ?.toList()
            .orEmpty()
        val staleBuildCacheCount = buildCacheFiles.count { file ->
            val did = runCatching {
                JSONObject(file.readText()).optString("diff_id")
                    .removePrefix("sha256:")
            }.getOrDefault("")
            did.isBlank() || !layerIdRegex.matches(did) || did !in layerIds
        }
        val tempPrefixes = listOf(
            "pdlayer_", "pdbase_", "pdstage_", "pdblob_", "pdloadbody_",
            "pdsavebody_", "pdarchiveput_", "pdbuildctx_", "pdload_", "pdsave_",
        )
        val tmpArtifacts = File(pdockerHome, "tmp")
            .listFiles()
            ?.count { file -> tempPrefixes.any { prefix -> file.name.startsWith(prefix) } }
            ?: 0
        val stagedImages = imageRoot.listFiles()
            ?.count { file -> ".pull-" in file.name || ".old-" in file.name }
            ?: 0
        return ImageCacheHealth(
            layerCount = layerIds.size,
            referencedLayerCount = referencedLayers.count { it in layerIds },
            unreferencedLayerCount = unreferencedLayerIds.size,
            unreferencedLayerBytes = unreferencedBytes,
            buildCacheCount = buildCacheFiles.size,
            staleBuildCacheCount = staleBuildCacheCount,
            tempArtifactCount = tmpArtifacts + stagedImages,
        )
    }

    private fun readImageDiffIds(imageDir: File): List<String> =
        runCatching {
            val config = JSONObject(File(imageDir, "config.json").readText())
            val rootfs = config.optJSONObject("rootfs") ?: return@runCatching emptyList()
            val arr = rootfs.optJSONArray("diff_ids") ?: return@runCatching emptyList()
            (0 until arr.length())
                .mapNotNull { arr.optString(it).removePrefix("sha256:").takeIf { value -> value.isNotBlank() } }
        }.getOrDefault(emptyList())

    private fun layerSize(diffId: String): Long =
        runCatching {
            JSONObject(File(layerRoot, "${diffId.removePrefix("sha256:")}/meta.json").readText()).optLong("size", 0L)
        }.getOrDefault(0L)

    private val layerIdRegex = Regex("[A-Fa-f0-9]{64}")

    private fun renderImageReferenceTree(images: List<ImageReferenceInfo>) {
        if (images.isEmpty()) return
        val parentByRef = imageParentMap(images)
        val childrenByRef = images
            .filter { parentByRef.containsKey(it.ref) }
            .groupBy { parentByRef.getValue(it.ref) }
        val roots = images
            .filter { it.ref !in parentByRef }
            .sortedWith(compareBy<ImageReferenceInfo> { it.displayRef }.thenBy { it.ref })
        val rows = mutableListOf<ImageReferenceGraphRow>()
        roots.forEachIndexed { index, image ->
            appendImageReferenceGraphRows(
                image = image,
                childrenByRef = childrenByRef,
                depth = 0,
                ancestorLast = emptyList(),
                isLast = index == roots.lastIndex,
                rows = rows,
            )
        }
        addSection(getString(R.string.section_image_references))
        addImageReferenceGraph(getString(R.string.section_image_references), rows.take(128))
    }

    private fun imageParentMap(images: List<ImageReferenceInfo>): Map<String, String> {
        val parents = mutableMapOf<String, String>()
        images.forEach { child ->
            val parent = images
                .filter { candidate ->
                    candidate.ref != child.ref &&
                        candidate.diffIds.isNotEmpty() &&
                        child.diffIds.size > candidate.diffIds.size &&
                        child.diffIds.take(candidate.diffIds.size) == candidate.diffIds
                }
                .maxByOrNull { it.diffIds.size }
            if (parent != null) parents[child.ref] = parent.ref
        }
        return parents
    }

    private fun appendImageReferenceGraphRows(
        image: ImageReferenceInfo,
        childrenByRef: Map<String, List<ImageReferenceInfo>>,
        depth: Int,
        ancestorLast: List<Boolean>,
        isLast: Boolean,
        rows: MutableList<ImageReferenceGraphRow>,
    ) {
        val children = childrenByRef[image.ref].orEmpty()
            .sortedWith(compareBy<ImageReferenceInfo> { it.displayRef }.thenBy { it.ref })
        val projectRefs = imageProjectRefs(image.ref)
        val containerRefs = imageContainerRefs(image.ref)
        val attached = mutableListOf<ImageReferenceGraphRow>().apply {
            add(
                ImageReferenceGraphRow(
                    depth = depth + 1,
                    ancestorLast = ancestorLast + isLast,
                    isLast = projectRefs.isEmpty() &&
                        containerRefs.isEmpty() &&
                        children.isEmpty(),
                    title = getString(R.string.image_reference_cache_node),
                    detail = getString(
                        R.string.image_reference_cache_detail_fmt,
                        formatBytes(image.sharedLayerBytes),
                        formatBytes(image.totalLayerBytes),
                        image.diffIds.size,
                    ),
                    kind = ImageGraphKind.Cache,
                )
            )
            projectRefs.forEachIndexed { index, ref ->
                add(
                    ImageReferenceGraphRow(
                        depth = depth + 1,
                        ancestorLast = ancestorLast + isLast,
                        isLast = index == projectRefs.lastIndex && containerRefs.isEmpty() && children.isEmpty(),
                        title = ref,
                        detail = getString(R.string.image_reference_compose_detail),
                        kind = ImageGraphKind.Compose,
                    )
                )
            }
            containerRefs.forEachIndexed { index, ref ->
                add(
                    ImageReferenceGraphRow(
                        depth = depth + 1,
                        ancestorLast = ancestorLast + isLast,
                        isLast = index == containerRefs.lastIndex && children.isEmpty(),
                        title = ref,
                        detail = getString(R.string.image_reference_container_detail),
                        kind = ImageGraphKind.Container,
                    )
                )
            }
        }
        rows += ImageReferenceGraphRow(
            depth = depth,
            ancestorLast = ancestorLast,
            isLast = isLast,
            title = image.displayRef,
            detail = imageDetail(image.dir, image.ref, image),
            kind = ImageGraphKind.Image,
            actions = listOf(
                ImageGraphAction(getString(R.string.image_reference_action_files)) {
                    openImageFiles(image.dir)
                },
                ImageGraphAction(getString(R.string.image_reference_action_delete)) {
                    confirmDeleteImage(image.ref, cleanCache = false)
                },
                ImageGraphAction(getString(R.string.image_reference_action_clean)) {
                    confirmDeleteImage(image.ref, cleanCache = true)
                },
            ),
        )
        rows += attached
        children.forEachIndexed { index, child ->
            appendImageReferenceGraphRows(
                image = child,
                childrenByRef = childrenByRef,
                depth = depth + 1,
                ancestorLast = ancestorLast + isLast,
                isLast = index == children.lastIndex,
                rows = rows,
            )
        }
    }

    private fun imageProjectRefs(ref: String): List<String> {
        val display = displayImageRef(ref)
        return projectDirs().flatMap { project ->
            parseComposeServices(project).mapNotNull { service ->
                val serviceImage = service.image.ifBlank { "local/${project.name}-${service.name}:latest" }
                if (serviceImage == ref || displayImageRef(serviceImage) == display) {
                    "${project.name}/${service.name}"
                } else {
                    null
                }
            }
        }.distinct().sorted()
    }

    private fun imageContainerRefs(ref: String): List<String> {
        val display = displayImageRef(ref)
        return containerDirs().mapNotNull { dir ->
            val state = readState(dir) ?: return@mapNotNull null
            val image = state.optString("Image")
            if (image != ref && displayImageRef(image) != display) return@mapNotNull null
            val name = state.optString("Name", dir.name).trim('/').ifBlank { dir.name }
            val status = state.optJSONObject("State")?.optString("Status").orEmpty().ifBlank { "unknown" }
            "$name ($status)"
        }.distinct().sorted()
    }

    private fun containerDirs(): List<File> =
        containerRoot.listFiles()
            ?.filter { it.isDirectory }
            ?.sortedWith(
                compareByDescending<File> { readState(it)?.optString("Created").orEmpty() }
                    .thenBy { readState(it)?.optString("Name").orEmpty().trim('/').ifBlank { it.name } }
                    .thenBy { it.name }
            )
            .orEmpty()

    private fun composeFiles(): List<File> =
        projectRoot.walkSafe(MAX_UI_WALK_ENTRIES)
            .filter { it.isFile && it.name in setOf("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml") }
            .sortedBy { it.absolutePath }

    private fun dockerfiles(): List<File> =
        projectRoot.walkSafe(MAX_UI_WALK_ENTRIES)
            .filter { it.isFile && it.name == "Dockerfile" }
            .sortedBy { it.absolutePath }

    private fun renderProjectDashboard() {
        val projects = projectSummaries()
        if (projects.isEmpty()) return
        addSection(getString(R.string.section_project_dashboard))
        projects.take(8).forEach { project ->
            val serviceText = project.services
                .map { it.name }
                .distinct()
                .take(4)
                .joinToString(", ")
                .ifBlank { "-" }
            val detail = listOf(
                getString(
                    R.string.project_dashboard_counts_fmt,
                    project.compose.size,
                    project.dockerfiles.size,
                    project.editable.size,
                    project.containerStatusSummary,
                ),
                getString(R.string.project_dashboard_services_fmt, serviceText),
                getString(R.string.project_dashboard_dependencies_fmt, projectDependencySummary(project.services)),
                getString(R.string.project_dashboard_health_fmt, projectHealthSummary(project.services)),
                getString(R.string.project_dashboard_urls_fmt, project.serviceUrls.joinToString(", ") { it.label }.ifBlank { "-" }),
                getString(R.string.project_dashboard_service_health_fmt, project.serviceHealth),
                getString(R.string.project_dashboard_models_fmt, project.modelSummary),
                getString(R.string.project_dashboard_gpu_fmt, project.gpuProfileSummary),
                getString(R.string.project_dashboard_runtime_fmt, project.runtimeDiagnosticSummary),
                getString(R.string.project_dashboard_jobs_fmt, project.jobSummary),
            ).joinToString("\n")
            addWidget(project.dir.name, getString(R.string.section_project_dashboard), detail, detailLines = 10) {
                openProjectPrimaryFile(project)
            }
            project.compose.take(2).forEach { file ->
                addAction(
                    getString(R.string.action_open_project_compose_fmt, project.dir.name),
                    relativeProjectPath(project.dir, file),
                ) { openEditor(file) }
            }
            project.dockerfiles.take(2).forEach { file ->
                addAction(
                    getString(R.string.action_open_project_dockerfile_fmt, project.dir.name),
                    relativeProjectPath(project.dir, file),
                ) { openEditor(file) }
            }
            if (project.compose.isNotEmpty()) {
                addAction(getString(R.string.action_up_fmt, project.dir.name), getString(R.string.detail_compose_up)) {
                    runComposeUp(project.dir, getString(R.string.terminal_compose_up_fmt, project.dir.name))
                }
            }
            if (project.dockerfiles.isNotEmpty()) {
                addAction(getString(R.string.action_build_fmt, project.dir.name), project.dir.absolutePath) {
                    runImageBuild(project.dir, getString(R.string.terminal_docker_build_fmt, project.dir.name))
                }
            }
            project.serviceUrls.forEach { serviceUrl ->
                addAction(serviceActionTitle(serviceUrl.label, serviceUrl.url), serviceActionDetail(serviceUrl.url)) {
                    openServiceUrl(serviceUrl.url)
                }
            }
            project.gpuDiagnostics?.takeIf { it.isFile }?.let { file ->
                addAction(getString(R.string.action_open_gpu_diagnostics), relativeProjectPath(project.dir, file)) {
                    openEditor(file)
                }
            }
            project.runtimeDiagnosticLog?.takeIf { it.isFile }?.let { file ->
                addAction(getString(R.string.action_open_runtime_diagnostics), file.name) {
                    openTextToolAsync(getString(R.string.section_project_dashboard), getString(R.string.action_open_runtime_diagnostics)) {
                        readFileTailText(file, MAX_JOB_LOG_VIEW_BYTES)
                    }
                }
            }
            project.editable
                .filterNot { it in project.compose || it in project.dockerfiles }
                .take(4)
                .forEach { file ->
                    addAction(
                        getString(R.string.action_open_project_file_fmt, relativeProjectPath(project.dir, file)),
                        getString(R.string.detail_project_file),
                    ) { openEditor(file) }
                }
        }
    }

    private fun projectSummaries(): List<ProjectSummary> =
        projectDirs().take(MAX_PROJECT_DASHBOARD_PROJECTS).map { dir ->
            val files = dir.walkSafe(MAX_UI_WALK_ENTRIES).filter { it.isFile }
            val compose = files
                .filter { it.name in setOf("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml") }
                .sortedBy { it.absolutePath }
            val dockerfiles = files
                .filter { it.name == "Dockerfile" }
                .sortedBy { it.absolutePath }
            val editable = files
                .filter { it.length() <= MAX_INLINE_EDIT_BYTES && isProjectTextFile(it) }
                .sortedWith(compareBy<File> { projectFileRank(it) }.thenBy { it.absolutePath })
            val services = compose
                .mapNotNull { it.parentFile }
                .distinctBy { it.absolutePath }
                .flatMap { parseComposeServices(it) }
            val serviceUrls = projectServiceUrls(services)
            val runtimeDiagnostic = projectRuntimeDiagnostic(dir)
            ProjectSummary(
                dir = dir,
                compose = compose,
                dockerfiles = dockerfiles,
                editable = editable,
                services = services,
                serviceUrls = serviceUrls,
                serviceHealth = projectServiceHealthSummary(serviceUrls, dir),
                modelSummary = projectModelSummary(dir),
                gpuProfileSummary = projectGpuProfileSummary(dir),
                gpuDiagnostics = File(dir, "profiles/pdocker-gpu-diagnostics.json").takeIf { it.isFile },
                containerStatusSummary = projectContainerStatusSummary(dir),
                runtimeDiagnosticSummary = runtimeDiagnostic.summary,
                runtimeDiagnosticLog = runtimeDiagnostic.log,
                jobSummary = projectJobSummary(dir.name),
            )
        }.sortedWith(compareBy<ProjectSummary> {
            if (it.compose.isNotEmpty() || it.dockerfiles.isNotEmpty()) 0 else 1
        }.thenBy { it.dir.name })

    private fun projectDirs(): List<File> =
        projectRoot.listFiles()
            ?.filter { it.isDirectory && it.name !in setOf(".git", "node_modules") }
            ?.sortedBy { it.name }
            .orEmpty()

    private fun openProjectPrimaryFile(project: ProjectSummary) {
        val target = project.compose.firstOrNull()
            ?: project.dockerfiles.firstOrNull()
            ?: project.editable.firstOrNull()
        if (target != null) {
            openEditor(target)
        }
    }

    private fun relativeProjectPath(project: File, file: File): String =
        runCatching { project.toPath().relativize(file.toPath()).toString() }.getOrDefault(file.name)

    private fun isProjectTextFile(file: File): Boolean {
        val name = file.name
        val ext = name.substringAfterLast('.', "")
        return name in setOf("Dockerfile", "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml", "README.md") ||
            ext in setOf("yaml", "yml", "json", "sh", "env", "md", "txt", "toml", "conf", "properties", "gradle", "kt", "py", "js", "ts", "css", "html")
    }

    private fun projectFileRank(file: File): Int = when (file.name) {
        "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml" -> 0
        "Dockerfile" -> 1
        "README.md" -> 2
        else -> 3
    }

    private fun projectContainerStatusSummary(projectDir: File): String {
        val snapshots = projectContainerSnapshots(projectDir)
        val localStates = projectContainerStates(projectDir)
        if (snapshots.isEmpty() && localStates.isEmpty()) {
            return if (lastContainerSnapshotAt == 0L) {
                getString(R.string.container_status_syncing)
            } else {
                getString(R.string.container_inventory_fmt, 0, 0)
            }
        }
        if (snapshots.isNotEmpty()) {
            val running = snapshots.count { containerSnapshotIsRunning(it) }
            return getString(R.string.container_inventory_fmt, snapshots.size, running)
        }
        val running = localStates.count { containerStateIsRunning(it) }
        return getString(R.string.container_inventory_fmt, localStates.size, running)
    }

    private fun projectRuntimeDiagnostic(projectDir: File): ProjectRuntimeDiagnostic {
        val states = projectContainerStates(projectDir)
        if (states.isEmpty()) return ProjectRuntimeDiagnostic("-", null)
        val failed = states.firstOrNull { state ->
            val runtime = state.optJSONObject("State")
            !containerStateIsRunning(state) &&
                ((runtime?.optInt("ExitCode", 0) ?: 0) != 0 || !runtime?.optString("Error").orEmpty().isNullOrBlank())
        }
        val selected = failed ?: states.first()
        val logFile = containerLogFile(selected)
        val runtime = selected.optJSONObject("State")
        val name = selected.optString("Name").trim('/').ifBlank { selected.optString("Id").take(12) }
        if (containerStateIsRunning(selected) && failed == null) {
            val running = states.count { containerStateIsRunning(it) }
            return ProjectRuntimeDiagnostic("running $running/${states.size}: $name", logFile)
        }
        val status = runtime?.optString("Status").orEmpty().ifBlank { "unknown" }
        val exit = runtime?.optInt("ExitCode", 0) ?: 0
        val reason = runtimeFailureReason(runtime?.optString("Error").orEmpty(), logFile)
        val summary = listOf("$status $name rc=$exit", reason)
            .filter { it.isNotBlank() }
            .joinToString(": ")
        return ProjectRuntimeDiagnostic(summary.ifBlank { "-" }, logFile)
    }

    private fun projectContainerStates(projectDir: File): List<JSONObject> {
        val projectId = projectIdFor(projectDir)
        val composeNames = projectComposeContainerNames(projectDir).keys.toSet()
        return containerDirs().mapNotNull { readState(it) }
            .filter { state ->
                val labels = containerLabels(state)
                val labelledProject = labels?.optString(PDOCKER_PROJECT_ID_LABEL).orEmpty()
                when {
                    labelledProject.isNotBlank() -> labelledProject == projectId
                    else -> state.optString("Name").trim('/') in composeNames
                }
            }
            .sortedWith(
                compareByDescending<JSONObject> { if (containerStateIsRunning(it)) 1 else 0 }
                    .thenByDescending { it.optString("Created") }
                    .thenBy { it.optString("Name") },
            )
    }

    private fun containerLabels(state: JSONObject): JSONObject? =
        state.optJSONObject("Labels") ?: state.optJSONObject("Config")?.optJSONObject("Labels")

    private fun containerStateIsRunning(state: JSONObject): Boolean =
        state.optJSONObject("State")?.optBoolean("Running", false) == true

    private fun containerLogFile(state: JSONObject): File? {
        val id = state.optString("Id").takeIf { it.isNotBlank() } ?: return null
        return File(pdockerHome, "logs/$id.log")
    }

    private fun runtimeFailureReason(error: String, logFile: File?): String {
        val logTail = logFile?.takeIf { it.isFile }?.let { readFileTailText(it, 64 * 1024) }.orEmpty()
        val normalized = logTail.replace('\r', '\n')
        if ("unsafe path" in normalized && "Cross-device link" in normalized) {
            val paths = Regex("unsafe path=([^\\s]+)")
                .findAll(normalized)
                .map { it.groupValues[1] }
                .distinct()
                .take(4)
                .joinToString(", ")
            return "bind path denied ${paths.ifBlank { "container mount" }} (Cross-device link)"
        }
        val interesting = normalized.lineSequence()
            .map { it.trim() }
            .filter { line ->
                line.contains("ERROR", ignoreCase = true) ||
                    line.contains("failed", ignoreCase = true) ||
                    line.contains("cannot", ignoreCase = true) ||
                    line.contains("No devices found", ignoreCase = true) ||
                    line.contains("OOM", ignoreCase = true) ||
                    line.contains("Killed", ignoreCase = true)
            }
            .lastOrNull()
            ?.take(180)
            .orEmpty()
        return interesting.ifBlank { error.take(180) }
    }

    private fun readFileTailText(file: File, maxBytes: Int): String =
        runCatching {
            val size = file.length()
            file.inputStream().use { input ->
                if (size > maxBytes) input.skip(size - maxBytes)
                input.readBytes().toString(Charsets.UTF_8)
            }
        }.getOrElse { getString(R.string.engine_operation_failed_fmt, it.message.orEmpty()) }

    private fun projectContainerSnapshots(projectDir: File): List<JSONObject> {
        val projectId = projectIdFor(projectDir)
        val composeNames = projectComposeContainerNames(projectDir).keys
            .toSet()
        val uniqueNames = uniqueContainerSnapshotNames()
        return containerSnapshot.filter { obj ->
            val labels = obj.optJSONObject("Labels")
            val labelledProject = labels?.optString(PDOCKER_PROJECT_ID_LABEL).orEmpty()
            when {
                labelledProject.isNotBlank() -> labelledProject == projectId
                else -> {
                    val names = obj.optJSONArray("Names")
                    names != null && (0 until names.length()).any { index ->
                        val name = names.optString(index).trim('/')
                        name in composeNames && uniqueNames[name] === obj
                    }
                }
            }
        }
    }

    private fun uniqueContainerSnapshotNames(): Map<String, JSONObject> {
        val byName = mutableMapOf<String, MutableList<JSONObject>>()
        containerSnapshot.forEach { obj ->
            val names = obj.optJSONArray("Names") ?: return@forEach
            for (i in 0 until names.length()) {
                val name = names.optString(i).trim('/')
                if (name.isNotBlank()) byName.getOrPut(name) { mutableListOf() } += obj
            }
        }
        return byName.mapNotNull { (name, matches) ->
            matches.distinctBy { it.optString("Id") }.singleOrNull()?.let { name to it }
        }.toMap()
    }

    private fun projectComposeContainerNames(projectDir: File): Map<String, String> =
        parseComposeServices(projectDir).associate { service ->
            service.containerName.ifBlank { "${projectDir.name}-${service.name}-1" } to service.name
        }

    private fun containerSnapshotIsRunning(obj: JSONObject): Boolean =
        when {
            obj.optString("Status").startsWith("Exited", ignoreCase = true) -> false
            obj.optString("State").isNotBlank() -> obj.optString("State").equals("running", ignoreCase = true)
            else -> obj.optString("Status").startsWith("Up", ignoreCase = true)
        }

    private fun projectJobSummary(projectName: String): String {
        val jobs = dockerJobs.filter { it.group == projectName || projectName in it.command }
        if (jobs.isEmpty()) return "-"
        val counts = jobs.groupingBy { it.status }.eachCount()
            .entries
            .joinToString(", ") { "${it.key}:${it.value}" }
        val latestFailure = jobs
            .filter { (it.exitCode ?: 0) != 0 || it.status.contains("fail", ignoreCase = true) || it.status.contains("失敗") }
            .maxByOrNull { it.endedAt ?: it.startedAt }
            ?.let { jobFailureReason(it) }
            .orEmpty()
        return if (latestFailure.isBlank()) counts else "$counts - $latestFailure"
    }

    private fun jobFailureReason(job: DockerJob): String =
        job.output.asReversed()
            .map { it.trim() }
            .firstOrNull { line ->
                line.contains("CMake Error", ignoreCase = true) ||
                    line.contains("BLAS not found", ignoreCase = true) ||
                    line.contains("ERROR:", ignoreCase = true) ||
                    line.contains("failed", ignoreCase = true) ||
                    line.contains("cannot", ignoreCase = true) ||
                    line.contains("Connection reset", ignoreCase = true)
            }
            ?.take(180)
            .orEmpty()

    private fun projectDependencySummary(services: List<ComposeService>): String {
        val edges = services.flatMap { service ->
            service.dependsOn.distinct().map { dep -> "${service.name} -> $dep" }
        }
        return edges.take(4).joinToString(", ").ifBlank { "-" }
    }

    private fun projectHealthSummary(services: List<ComposeService>): String {
        val health = services.filter { it.hasHealthcheck }.map { it.name }.distinct()
        return health.take(4).joinToString(", ").ifBlank { "-" }
    }

    private fun projectServiceUrls(services: List<ComposeService>): List<ProjectServiceUrl> =
        services.flatMap { service ->
            composeServiceUrls(service).map { (label, url) -> ProjectServiceUrl(service.name, label, url) }
        }.distinctBy { it.url }

    private fun composeServiceUrls(service: ComposeService): List<Pair<String, String>> {
        val urls = mutableListOf<Pair<String, String>>()
        service.ports
            .mapNotNull { port -> composePortBinding(port) }
            .distinct()
            .forEach { binding ->
                val hostPort = binding.hostPort
                val link = service.serviceLinks.firstOrNull { it.port == hostPort }
                val defaultLabel = if (isVncService(link?.label, hostPort, binding.containerPort)) {
                    "VNC ${service.name}"
                } else {
                    "${service.name}:$hostPort"
                }
                val label = link?.label?.takeIf { it.isNotBlank() } ?: defaultLabel
                val url = link?.url?.takeIf { it.isNotBlank() }
                    ?: serviceUriFor(label, "127.0.0.1", hostPort, binding.containerPort)
                urls += label to url
            }
        service.serviceLinks
            .filter { it.port == null && !it.url.isNullOrBlank() }
            .forEach { link -> urls += link.label to link.url.orEmpty() }
        return urls.distinctBy { it.second }
    }

    private fun composeServiceAutoOpenUrl(service: ComposeService): Pair<String, String>? {
        service.ports
            .mapNotNull { port -> composePortBinding(port) }
            .distinct()
            .forEach { binding ->
                val hostPort = binding.hostPort
                val link = service.serviceLinks.firstOrNull { it.port == hostPort && it.autoOpen }
                if (link != null) {
                    val url = link.url?.takeIf { it.isNotBlank() }
                        ?: serviceUriFor(link.label, "127.0.0.1", hostPort, binding.containerPort)
                    return link.label to url
                }
            }
        return service.serviceLinks
            .firstOrNull { it.autoOpen && it.port == null && !it.url.isNullOrBlank() }
            ?.let { it.label to it.url.orEmpty() }
    }

    private fun projectServiceHealthSummary(urls: List<ProjectServiceUrl>, projectDir: File): String {
        if (urls.isEmpty()) return "-"
        val snapshots = projectContainerSnapshots(projectDir)
        val runningProofs = projectRunningServiceProofs(projectDir, snapshots)
        val runningServices = projectRunningServiceNames(projectDir, snapshots)
        urls.filter { it.serviceName in runningServices && isHttpServiceUrl(it.url) }
            .forEach { scheduleServiceHealthProbe(it.url) }
        val inactive = getString(R.string.service_health_inactive)
        return urls.take(4).joinToString(", ") { serviceUrl ->
            val running = serviceUrl.serviceName in runningServices
            val proof = runningProofs[serviceUrl.serviceName]
            val state = when {
                !running -> inactive
                proof == null -> getString(R.string.service_health_unknown)
                !isHttpServiceUrl(serviceUrl.url) -> getString(R.string.service_health_external_client)
                else -> serviceHealth[serviceUrl.url] ?: getString(R.string.service_health_requested)
            }
            "${serviceUrl.label}:$state"
        }
    }

    private fun projectRunningServiceNames(projectDir: File, snapshots: List<JSONObject>): Set<String> {
        val serviceByContainerName = projectComposeContainerNames(projectDir)
        return snapshots
            .filter { containerSnapshotIsRunning(it) }
            .mapNotNull { projectSnapshotServiceName(it, serviceByContainerName) }
            .toSet()
    }

    private fun projectRunningServiceProofs(projectDir: File, snapshots: List<JSONObject>): Map<String, ServiceContainerProof> {
        val serviceByContainerName = projectComposeContainerNames(projectDir)
        val proofs = snapshots
            .filter { containerSnapshotIsRunning(it) }
            .mapNotNull { obj ->
                val engineId = obj.optString("Id").takeIf { it.isNotBlank() } ?: return@mapNotNull null
                val serviceName = projectSnapshotServiceName(obj, serviceByContainerName) ?: return@mapNotNull null
                ServiceContainerProof(serviceName, engineId)
            }
        return proofs.groupBy { it.serviceName }
            .mapNotNull { (service, matches) ->
                matches.distinctBy { it.engineContainerId }.singleOrNull()?.let { service to it }
            }
            .toMap()
    }

    private fun projectSnapshotServiceName(obj: JSONObject, serviceByContainerName: Map<String, String>): String? {
        val labels = obj.optJSONObject("Labels")
        return labels?.optString(PDOCKER_COMPOSE_SERVICE_LABEL)
            ?.takeIf { it.isNotBlank() }
            ?: labels?.optString("com.docker.compose.service")?.takeIf { it.isNotBlank() }
            ?: run {
                val names = obj.optJSONArray("Names") ?: return@run null
                (0 until names.length())
                    .map { index -> names.optString(index).trim('/') }
                    .firstNotNullOfOrNull { name -> serviceByContainerName[name] }
            }
    }

    private fun scheduleServiceHealthProbe(url: String) {
        if (url in serviceHealthInFlight) return
        val checkedAt = serviceHealthCheckedAt[url] ?: 0L
        if (url in serviceHealth && System.currentTimeMillis() - checkedAt < 15_000L) return
        serviceHealthInFlight += url
        thread(isDaemon = true, name = "pdocker-service-probe") {
            val result = probeServiceUrl(url)
            ui.post {
                serviceHealth[url] = result
                serviceHealthCheckedAt[url] = System.currentTimeMillis()
                serviceHealthInFlight -= url
                if (currentTab == Tab.Overview) renderContent()
            }
        }
    }

    private fun probeServiceUrl(url: String): String =
        runCatching {
            if (!isHttpServiceUrl(url)) return "external client"
            val conn = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 900
                readTimeout = 900
                requestMethod = "GET"
            }
            try {
                val code = conn.responseCode
                val httpStatus = if (code in 200..399) "HTTP $code" else "down HTTP $code"
                if (code in 200..399) {
                    getString(R.string.service_health_requested_with_http_fmt, httpStatus)
                } else {
                    httpStatus
                }
            } finally {
                conn.disconnect()
            }
        }.getOrElse { err ->
            val reason = err.message?.take(32)?.ifBlank { err::class.java.simpleName } ?: err::class.java.simpleName
            "down $reason"
        }

    private fun composePortBinding(port: String): ComposePortBinding? {
        val cleaned = port.trim().trim('"', '\'')
        val withoutProtocol = cleaned.substringBefore('/')
        val numbers = withoutProtocol.split(':')
            .mapNotNull { part -> part.toIntOrNull()?.takeIf { it in 1..65535 } }
        val hostPort = numbers.firstOrNull() ?: return null
        val containerPort = numbers.lastOrNull() ?: hostPort
        return ComposePortBinding(hostPort, containerPort)
    }

    private fun isHttpServiceUrl(url: String): Boolean =
        url.startsWith("http://", ignoreCase = true) || url.startsWith("https://", ignoreCase = true)

    private fun isVncServiceUrl(url: String): Boolean =
        url.startsWith("vnc://", ignoreCase = true)

    private fun isServiceUri(url: String): Boolean =
        isHttpServiceUrl(url) || isVncServiceUrl(url)

    private fun serviceUriFor(label: String, host: String, hostPort: Int, containerPort: Int): String =
        if (isVncService(label, hostPort, containerPort)) {
            "vnc://$host:$hostPort"
        } else {
            "http://$host:$hostPort/"
        }

    private fun isVncService(label: String?, hostPort: Int?, containerPort: Int?): Boolean {
        val lower = label.orEmpty().lowercase()
        if ("novnc" in lower) return false
        if ("vnc" in lower) return true
        return containerPort in 5900..5999 || hostPort in 5900..5999
    }

    private fun serviceActionTitle(label: String, url: String): String =
        if (isVncServiceUrl(url)) {
            getString(R.string.action_open_vnc_service_fmt, label)
        } else {
            getString(R.string.action_open_service_fmt, label)
        }

    private fun serviceActionDetail(url: String): String =
        if (isVncServiceUrl(url)) {
            getString(R.string.detail_open_vnc_client_fmt, url)
        } else {
            getString(R.string.detail_open_at_fmt, url)
        }

    private fun openServiceUrl(url: String) {
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    }

    private fun projectModelSummary(project: File): String {
        val modelDir = File(project, "models")
        val models = modelDir.walkSafe(128)
            .filter { it.isFile && it.name.endsWith(".gguf", ignoreCase = true) }
        val partials = modelDir.walkSafe(128)
            .filter { it.isFile && it.name.endsWith(".gguf.part", ignoreCase = true) }
        return when {
            models.isNotEmpty() -> "${models.size} GGUF / ${formatBytes(models.sumOf { it.length() })}"
            partials.isNotEmpty() -> "partial ${partials.size} / ${formatBytes(partials.sumOf { it.length() })}"
            else -> "-"
        }
    }

    private fun projectGpuProfileSummary(project: File): String {
        val diagnostics = File(project, "profiles/pdocker-gpu-diagnostics.json")
        if (diagnostics.isFile) {
            val obj = runCatching { JSONObject(diagnostics.readText()) }.getOrNull()
            if (obj != null) {
                val backend = obj.optString("backend", "unknown").ifBlank { "unknown" }
                val reason = obj.optString("reason", "").ifBlank { "diagnostics ready" }
                return "$backend: $reason"
            }
            return getString(R.string.project_dashboard_gpu_invalid)
        }
        val env = File(project, "profiles/pdocker-gpu.env")
        if (env.isFile) {
            val backend = env.readLines()
                .firstOrNull { it.startsWith("LLAMA_GPU_BACKEND=") }
                ?.substringAfter("=")
                ?.ifBlank { "unknown" }
                ?: "unknown"
            return "$backend: env profile"
        }
        return "-"
    }

    private fun formatBytes(bytes: Long): String {
        val units = arrayOf("B", "KiB", "MiB", "GiB")
        var value = bytes.toDouble()
        var unit = 0
        while (value >= 1024.0 && unit < units.lastIndex) {
            value /= 1024.0
            unit += 1
        }
        return if (unit == 0) "${bytes} ${units[unit]}" else String.format("%.1f %s", value, units[unit])
    }

    private fun renderProjectFileShortcuts() {
        val files = projectRoot.walkSafe(MAX_UI_WALK_ENTRIES)
            .filter { it.isFile && it.length() <= MAX_INLINE_EDIT_BYTES }
            .sortedByDescending { it.lastModified() }
            .take(8)
        if (files.isEmpty()) return
        addSection(getString(R.string.section_project_files))
        files.forEach { file ->
            addWidget(editorTitle(file), getString(R.string.detail_project_file), file.absolutePath) {
                openEditor(file)
            }
        }
    }

    private fun projectTemplates(): List<ProjectTemplate> =
        runCatching {
            val root = JSONObject(assets.open("project-library/library.json").bufferedReader().use { it.readText() })
            val arr = root.optJSONArray("templates") ?: JSONArray()
            val libraryVersion = root.optInt("version", 1)
            (0 until arr.length()).mapNotNull { i ->
                arr.optJSONObject(i)?.let { obj ->
                    val features = obj.optJSONArray("features") ?: JSONArray()
                    ProjectTemplate(
                        id = obj.optString("id"),
                        name = obj.optString("name"),
                        category = obj.optString("category"),
                        description = obj.optString("description"),
                        assetPath = obj.optString("assetPath"),
                        projectDir = obj.optString("projectDir"),
                        compose = obj.optString("compose", "compose.yaml"),
                        dockerfile = obj.optString("dockerfile", "Dockerfile"),
                        gpu = obj.optString("gpu", "none"),
                        version = obj.optInt("version", libraryVersion),
                        features = (0 until features.length()).mapNotNull { j -> features.optString(j).takeIf { it.isNotBlank() } },
                    )
                }
            }.filter { it.id.isNotBlank() && it.assetPath.isNotBlank() && it.projectDir.isNotBlank() }
        }.getOrElse {
            status.text = getString(R.string.status_library_failed, it.message.orEmpty())
            emptyList()
        }

    private fun installTemplate(template: ProjectTemplate) {
        val target = File(projectRoot, template.projectDir)
        val report = copyAssetTreeMissing(template.assetPath, target)
        File(target, ".pdocker-template-id").writeText(template.id + "\n")
        File(target, ".pdocker-template-version").writeText(template.version.toString() + "\n")
        migrateProjectPorts(target)
        ensureProjectDocumentsEnv(target)
        if (template.id == "dev-workspace") migrateDefaultDevWorkspace(target)
        status.text = getString(
            R.string.status_library_install_report_fmt,
            template.name,
            report.copied,
            report.kept,
            template.version,
        )
    }

    private fun copyAssetTreeMissing(assetPath: String, dest: File): TemplateInstallReport {
        val report = TemplateInstallReport()
        fun copyNode(src: String, target: File) {
            val children = assets.list(src).orEmpty()
            if (children.isEmpty()) {
                if (target.exists()) {
                    report.kept += 1
                    return
                }
                target.parentFile?.mkdirs()
                assets.open(src).use { input ->
                    target.outputStream().use { output -> input.copyTo(output) }
                }
                report.copied += 1
                return
            }
            target.mkdirs()
            children.forEach { child -> copyNode("$src/$child", File(target, child)) }
        }
        copyNode(assetPath.trim('/'), dest)
        return report
    }

    private fun File.walkSafe(maxEntries: Int = MAX_UI_WALK_ENTRIES): List<File> {
        if (!exists() || maxEntries <= 0) return emptyList()
        val result = ArrayList<File>(minOf(maxEntries, 128))
        val skip = setOf(".git", "node_modules", ".gradle", "build")
        runCatching {
            for (file in walkTopDown().onEnter { it.name !in skip }) {
                result += file
                if (result.size >= maxEntries) break
            }
        }
        return result
    }

    private fun readState(dir: File): JSONObject? =
        runCatching { JSONObject(File(dir, "state.json").readText()) }.getOrNull()

    private fun containerNetworkSummary(state: JSONObject?): String {
        val dockerNetwork = state?.optJSONObject("NetworkSettings")
        val pdockerNetwork = state?.optJSONObject("PdockerNetwork")
        val bridgeNetwork = dockerNetwork
            ?.optJSONObject("Networks")
            ?.optJSONObject("bridge")
        val ip = listOf(
            pdockerNetwork?.optString("IPAddress"),
            dockerNetwork?.optString("IPAddress"),
            bridgeNetwork?.optString("IPAddress"),
        ).firstOrNull { !it.isNullOrBlank() }.orEmpty()
        val ports = pdockerNetwork?.optJSONObject("Ports")
            ?: dockerNetwork?.optJSONObject("Ports")
        val rewriteCount = pdockerNetwork?.optJSONArray("PortRewrite")?.length() ?: 0
        val lines = mutableListOf(
            getString(R.string.container_ip_fmt, ip.ifBlank { "-" }),
            getString(R.string.container_ports_fmt, summarizePorts(ports)),
        )
        if (rewriteCount > 0) {
            lines += getString(R.string.container_hook_plan_fmt, rewriteCount)
        }
        containerWarningSummary(state, pdockerNetwork).takeIf { it.isNotBlank() }?.let {
            lines += it
        }
        return lines.joinToString("\n")
    }

    private fun containerWarningSummary(state: JSONObject?, pdockerNetwork: JSONObject?): String {
        val warnings = mutableListOf<String>()
        fun appendWarnings(arr: JSONArray?) {
            if (arr == null) return
            for (i in 0 until arr.length()) {
                arr.optString(i).takeIf { it.isNotBlank() }?.let { warnings += it }
            }
        }
        appendWarnings(state?.optJSONArray("Warnings"))
        appendWarnings(pdockerNetwork?.optJSONArray("Warnings"))
        val unique = warnings.distinct()
        if (unique.isEmpty()) return ""
        val text = unique.joinToString(" / ") { warning ->
            when {
                "not active yet" in warning -> getString(R.string.container_warning_ports_metadata)
                "host-network stub" in warning -> getString(R.string.container_warning_network_stub)
                else -> warning
            }
        }
        return getString(R.string.container_warnings_fmt, text)
    }

    private fun containerServiceUrls(state: JSONObject?): List<Pair<String, String>> {
        val dockerNetwork = state?.optJSONObject("NetworkSettings")
        val pdockerNetwork = state?.optJSONObject("PdockerNetwork")
        val ports = pdockerNetwork?.optJSONObject("Ports")
            ?: dockerNetwork?.optJSONObject("Ports")
            ?: return emptyList()
        val labels = containerServiceLabels(state)
        val urls = mutableListOf<Pair<String, String>>()
        val iter = ports.keys()
        while (iter.hasNext()) {
            val key = iter.next()
            val value = ports.opt(key)
            val port = _splitPortKey(key)
            val label = port?.let { labels[it] } ?: key
            if (value is JSONArray && value.length() > 0) {
                for (i in 0 until value.length()) {
                    val binding = value.optJSONObject(i) ?: continue
                    val host = browserHost(binding.optString("HostIp"))
                    val hostPort = binding.optString("HostPort")
                    if (hostPort.isBlank()) continue
                    val hostPortInt = hostPort.toIntOrNull()
                    if (hostPortInt == null) continue
                    val actionLabel = labels[hostPortInt] ?: label
                    urls += actionLabel to serviceUriFor(actionLabel, host, hostPortInt, port ?: hostPortInt)
                }
            } else {
                _splitPortKey(key)?.let { exposedPort ->
                    urls += label to serviceUriFor(label, "127.0.0.1", exposedPort, exposedPort)
                }
            }
        }
        containerExplicitServiceUrls(state).forEach { urls += it }
        return urls.distinctBy { it.second }
    }

    private fun containerServiceLabels(state: JSONObject?): Map<Int, String> {
        val labels = state?.optJSONObject("Labels") ?: return emptyMap()
        val out = mutableMapOf<Int, String>()
        val iter = labels.keys()
        while (iter.hasNext()) {
            val key = iter.next()
            if (!key.startsWith(PDOCKER_SERVICE_URL_LABEL_PREFIX)) continue
            val suffix = key.removePrefix(PDOCKER_SERVICE_URL_LABEL_PREFIX)
            val port = suffix.toIntOrNull() ?: continue
            val label = labels.optString(key).takeIf { it.isNotBlank() } ?: continue
            out[port] = label
        }
        return out
    }

    private fun containerExplicitServiceUrls(state: JSONObject?): List<Pair<String, String>> {
        val labels = state?.optJSONObject("Labels") ?: return emptyList()
        val urls = mutableListOf<Pair<String, String>>()
        val iter = labels.keys()
        while (iter.hasNext()) {
            val key = iter.next()
            if (!key.startsWith(PDOCKER_SERVICE_URL_LABEL_PREFIX)) continue
            if (key.removePrefix(PDOCKER_SERVICE_URL_LABEL_PREFIX).toIntOrNull() != null) continue
            val raw = labels.optString(key)
            val label = raw.substringBefore('=', "").trim()
            val url = raw.substringAfter('=', "").trim()
            if (label.isNotBlank() && isServiceUri(url)) {
                urls += label to url
            }
        }
        return urls
    }

    private fun browserHost(host: String): String =
        if (host.isBlank() || host == "0.0.0.0" || host == "::") "127.0.0.1" else host

    private fun _splitPortKey(key: String): Int? =
        key.substringBefore('/').toIntOrNull()?.takeIf { it in 1..65535 }

    private fun summarizePorts(ports: JSONObject?): String {
        if (ports == null || ports.length() == 0) {
            return getString(R.string.container_ports_none)
        }
        val keys = mutableListOf<String>()
        val iter = ports.keys()
        while (iter.hasNext()) keys += iter.next()
        return keys.sorted().flatMap { key ->
            val value = ports.opt(key)
            if (value is JSONArray && value.length() > 0) {
                (0 until value.length()).mapNotNull { i ->
                    value.optJSONObject(i)?.let { binding ->
                        getString(
                            R.string.container_port_binding_fmt,
                            binding.optString("HostIp").ifBlank { "127.0.0.1" },
                            binding.optString("HostPort").ifBlank { "?" },
                            key,
                        )
                    }
                }
            } else {
                listOf(getString(R.string.container_port_exposed_fmt, key))
            }
        }.joinToString(", ")
    }

    private fun summarizeRootfs(rootfs: File): String {
        val count = rootfs.list()?.size ?: 0
        return getString(R.string.summary_rootfs_fmt, count)
    }

    private fun containerLogPreview(dir: File): String {
        val candidates = listOf(
            File(pdockerHome, "logs/${dir.name}.log"),
            File(dir, "log"),
            File(dir, "logs.txt"),
        )
        val log = candidates.firstOrNull { it.isFile } ?: return getString(R.string.log_no_preview)
        return runCatching {
            log.readLines().takeLast(3).joinToString("\n").ifBlank { getString(R.string.log_empty) }
        }.getOrDefault(getString(R.string.log_unavailable))
    }

    private fun shellQuote(s: String): String =
        "'" + s.replace("'", "'\"'\"'") + "'"

    private fun seedDefaultProject() {
        val stamp = File(projectRoot, "default/.pdocker-template-version")
        val target = File(projectRoot, "default")
        if (!stamp.exists()) {
            copyAssetTree("default-project", target)
        }
        migrateProjectPorts(target)
        migrateDefaultDevWorkspace(target)
        ensureProjectDocumentsEnv(target)
        stamp.parentFile?.mkdirs()
        stamp.writeText("5\n")
    }

    private fun migrateInstalledProjects() {
        projectDirs().forEach { project ->
            migrateProjectPorts(project)
            ensureProjectDocumentsEnv(project)
            migrateLlamaCppGpuWorkspace(project)
        }
    }

    private fun migrateLlamaCppGpuWorkspace(project: File) {
        if (project.name != "llama-cpp-gpu") return
        val dockerfile = File(project, "Dockerfile")
        val compose = File(project, "compose.yaml")
        val startScript = File(project, "scripts/start-llama-server.sh")
        val versionStamp = File(project, ".pdocker-template-version")
        val templateVersion = if (versionStamp.isFile) {
            runCatching { versionStamp.readText().trim().toIntOrNull() ?: 0 }.getOrDefault(0)
        } else {
            0
        }
        val dockerfileText = if (dockerfile.isFile) runCatching { dockerfile.readText() }.getOrDefault("") else ""
        val composeText = if (compose.isFile) runCatching { compose.readText() }.getOrDefault("") else ""
        val startText = if (startScript.isFile) runCatching { startScript.readText() }.getOrDefault("") else ""
        val stalePdockerShaderTuning =
            "LLAMA_CPP_VULKAN_SHADER_PROFILE" in dockerfileText ||
                "pdocker-bridge-safe-glslc" in dockerfileText ||
                "LLAMA_CPP_VULKAN_SHADER_PROFILE" in composeText ||
                "ARG LLAMA_CPP_BUILD_TYPE=MinSizeRel" in dockerfileText ||
                "CMAKE_CXX_FLAGS_MINSIZEREL" in dockerfileText ||
                "LLAMA_CPP_BUILD_TYPE:-MinSizeRel" in composeText
        val staleCheckout =
            "git checkout \"\$LLAMA_CPP_REF\"" in dockerfileText &&
                "git checkout --detach FETCH_HEAD" !in dockerfileText
        val staleKvOffloadGuard =
            templateVersion < 6 ||
                "PDOCKER_VULKAN_ALLOW_KV_OFFLOAD" !in composeText ||
                "PDOCKER_VULKAN_ALLOW_KV_OFFLOAD" !in startText ||
                "--no-kv-offload" !in startText
        val staleGpuLayerDefault =
            templateVersion < 7 ||
                "LLAMA_ARG_N_GPU_LAYERS: \"\${LLAMA_ARG_N_GPU_LAYERS:-2}\"" in composeText
        val stalePipelineOptimizationDefault =
            templateVersion < 8 ||
                "PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION" !in composeText
        val staleLlamaWebUi =
            templateVersion < 9 ||
                "-DLLAMA_BUILD_WEBUI=OFF" in dockerfileText ||
                "-DLLAMA_BUILD_WEBUI=ON" !in dockerfileText
        val staleLlamaStaticPath =
            templateVersion < 10 ||
                "LLAMA_EXTRA_ARGS: \"\${LLAMA_EXTRA_ARGS:---path /opt/llama.cpp/tools/server/public --jinja}\"" !in composeText
        val staleLlamaBridgeClamps =
            templateVersion < 11 ||
                "PDOCKER_VULKAN_MAX_BUFFER_BYTES" !in composeText ||
                "GGML_VK_FORCE_MAX_BUFFER_SIZE" !in composeText ||
                "GGML_VK_FORCE_MAX_ALLOCATION_SIZE" !in composeText ||
                "GGML_VK_SUBALLOCATION_BLOCK_SIZE" !in composeText
        val staleLlamaCorrectnessProbe =
            templateVersion < 11 ||
                !File(project, "scripts/pdocker-llama-correctness.sh").isFile
        if (!stalePdockerShaderTuning && !staleCheckout && !staleKvOffloadGuard &&
            !staleGpuLayerDefault && !stalePipelineOptimizationDefault && !staleLlamaWebUi &&
            !staleLlamaStaticPath && !staleLlamaBridgeClamps && !staleLlamaCorrectnessProbe) return
        val backupDir = File(project, ".pdocker-template-backups/llama-cpp-gpu-${System.currentTimeMillis()}")
        backupDir.mkdirs()
        listOf(
            "Dockerfile",
            "compose.yaml",
            "README.md",
            "scripts/pdocker-gpu-profile.sh",
            "scripts/pdocker-llama-correctness.sh",
            "scripts/start-llama-server.sh",
            ".dockerignore",
        ).forEach { relative ->
            val dest = File(project, relative)
            if (dest.exists()) dest.copyTo(File(backupDir, relative).also { it.parentFile?.mkdirs() }, overwrite = true)
            if (relative == ".dockerignore") {
                if (!copyAssetFileIfPresent("project-library/llama-cpp-gpu/$relative", dest)) {
                    dest.parentFile?.mkdirs()
                    dest.writeText("models/\nworkspace/\nprofiles/\nlogs/\n*.gguf\n*.gguf.*\n")
                }
            } else {
                copyAssetFile("project-library/llama-cpp-gpu/$relative", dest)
            }
            if (relative.startsWith("scripts/")) dest.setExecutable(true, false)
        }
        File(project, ".pdocker-template-id").writeText("llama-cpp-gpu\n")
        File(project, ".pdocker-template-version").writeText("11\n")
        ensureProjectDocumentsEnv(project)
    }

    private fun copyAssetFile(assetPath: String, dest: File) {
        dest.parentFile?.mkdirs()
        assets.open(assetPath).use { input ->
            dest.outputStream().use { output -> input.copyTo(output) }
        }
    }

    private fun copyAssetFileIfPresent(assetPath: String, dest: File): Boolean {
        return runCatching {
            copyAssetFile(assetPath, dest)
            true
        }.getOrDefault(false)
    }

    private fun migrateDefaultDevWorkspace(project: File) {
        repairDefaultDevWorkspaceDockerfile(project)
        val dockerfile = File(project, "Dockerfile")
        if (dockerfile.isFile) {
            var text = dockerfile.readText()
            if (!text.contains("CLAUDE_CODE_NPM_PACKAGE")) {
                text = text.replace(
                    "ARG CODEX_NPM_PACKAGE=@openai/codex\n",
                    "ARG CODEX_NPM_PACKAGE=@openai/codex\nARG CLAUDE_CODE_NPM_PACKAGE=@anthropic-ai/claude-code\n",
                )
                text = text.replace(
                    "RUN npm install -g \"\$CODEX_NPM_PACKAGE\"",
                    "RUN npm install -g \"\$CODEX_NPM_PACKAGE\" \"\$CLAUDE_CODE_NPM_PACKAGE\"",
                )
            }
            if (!text.contains("Anthropic.claude-code")) {
                text = text.replace(
                    "RUN mkdir -p /workspace \"\$CODE_SERVER_USER_DATA_DIR/User\" \"\$CODE_SERVER_EXTENSIONS_DIR\" \\\n" +
                        "    && code-server --extensions-dir \"\$CODE_SERVER_EXTENSIONS_DIR\" --install-extension Continue.continue || true \\\n" +
                        "    && code-server --extensions-dir \"\$CODE_SERVER_EXTENSIONS_DIR\" --install-extension redhat.vscode-yaml || true \\\n" +
                        "    && code-server --extensions-dir \"\$CODE_SERVER_EXTENSIONS_DIR\" --install-extension ms-azuretools.vscode-docker || true",
                    "RUN mkdir -p /workspace \"\$CODE_SERVER_USER_DATA_DIR/User\" \"\$CODE_SERVER_EXTENSIONS_DIR\" \\\n" +
                        "    && for ext in Continue.continue OpenAI.chatgpt Anthropic.claude-code; do \\\n" +
                        "         code-server --extensions-dir \"\$CODE_SERVER_EXTENSIONS_DIR\" --install-extension \"\$ext\"; \\\n" +
                        "       done \\\n" +
                        "    && for ext in redhat.vscode-yaml ms-azuretools.vscode-docker; do \\\n" +
                        "         code-server --extensions-dir \"\$CODE_SERVER_EXTENSIONS_DIR\" --install-extension \"\$ext\" || true; \\\n" +
                        "       done",
                )
            }
            if (!text.contains("CODE_SERVER_IMAGE_EXTENSIONS_DIR")) {
                text = text.replace(
                    "CODE_SERVER_EXTENSIONS_DIR=/workspace/.vscode-server/extensions",
                    "CODE_SERVER_EXTENSIONS_DIR=/workspace/.vscode-server/extensions \\\n    CODE_SERVER_IMAGE_EXTENSIONS_DIR=/opt/pdocker/code-server/extensions",
                )
            }
            text = text.replace(
                "RUN mkdir -p /workspace \"\$CODE_SERVER_USER_DATA_DIR/User\" \"\$CODE_SERVER_EXTENSIONS_DIR\" \\",
                "RUN mkdir -p /workspace \"\$CODE_SERVER_USER_DATA_DIR/User\" \"\$CODE_SERVER_EXTENSIONS_DIR\" \"\$CODE_SERVER_IMAGE_EXTENSIONS_DIR\" \\",
            )
            text = text.replace(
                "code-server --extensions-dir \"\$CODE_SERVER_EXTENSIONS_DIR\" --install-extension \"\$ext\";",
                "code-server --extensions-dir \"\$CODE_SERVER_IMAGE_EXTENSIONS_DIR\" --install-extension \"\$ext\";",
            )
            text = text.replace(
                "code-server --extensions-dir \"\$CODE_SERVER_EXTENSIONS_DIR\" --install-extension \"\$ext\" || true;",
                "code-server --extensions-dir \"\$CODE_SERVER_IMAGE_EXTENSIONS_DIR\" --install-extension \"\$ext\" || true;",
            )
            text = text.replace("openai.chatgpt", "OpenAI.chatgpt")
            dockerfile.writeText(text)
        }

        val compose = File(project, "compose.yaml")
        if (compose.isFile) {
            var text = compose.readText()
            if (!text.contains("CLAUDE_CODE_NPM_PACKAGE")) {
                text = text.replace(
                    "        CODEX_NPM_PACKAGE: \"@openai/codex\"\n",
                    "        CODEX_NPM_PACKAGE: \"@openai/codex\"\n        CLAUDE_CODE_NPM_PACKAGE: \"@anthropic-ai/claude-code\"\n",
                )
            }
            if (!text.contains("ANTHROPIC_API_KEY")) {
                text = text.replace(
                    "      OPENAI_API_KEY: \"\${OPENAI_API_KEY:-}\"\n",
                    "      OPENAI_API_KEY: \"\${OPENAI_API_KEY:-}\"\n      ANTHROPIC_API_KEY: \"\${ANTHROPIC_API_KEY:-}\"\n",
                )
            }
            compose.writeText(text)
        }

        val dockerignore = File(project, ".dockerignore")
        val dockerignoreText = if (dockerignore.isFile) runCatching { dockerignore.readText() }.getOrDefault("") else ""
        val requiredDockerignore = listOf("workspace/", "documents/", "logs/", "profiles/", "models/")
        if (requiredDockerignore.any { it !in dockerignoreText }) {
            dockerignore.parentFile?.mkdirs()
            val merged = (dockerignoreText.lineSequence().map { it.trimEnd() } + requiredDockerignore.asSequence())
                .filter { it.isNotBlank() }
                .distinct()
                .joinToString("\n")
            dockerignore.writeText("$merged\n")
        }

        val startScript = File(project, "scripts/start-code-server.sh")
        val startScriptText = if (startScript.isFile) runCatching { startScript.readText() }.getOrDefault("") else ""
        if (!startScriptText.contains("install_extension_if_missing")) {
            startScript.parentFile?.mkdirs()
            assets.open("default-project/scripts/start-code-server.sh").use { input ->
                startScript.outputStream().use { output -> input.copyTo(output) }
            }
            startScript.setExecutable(true, false)
        }

        val extensions = File(project, "workspace/.vscode/extensions.json")
        if (!extensions.exists()) {
            extensions.parentFile?.mkdirs()
            extensions.writeText(
                """
                {
                  "recommendations": [
                    "Continue.continue",
                    "OpenAI.chatgpt",
                    "Anthropic.claude-code",
                    "redhat.vscode-yaml",
                    "ms-azuretools.vscode-docker"
                  ]
                }
                """.trimIndent() + "\n",
            )
        } else {
            val original = extensions.readText()
            val migrated = original.replace("openai.chatgpt", "OpenAI.chatgpt")
            if (migrated != original) extensions.writeText(migrated)
        }

        val tasks = File(project, "workspace/.vscode/tasks.json")
        if (!tasks.exists()) {
            tasks.parentFile?.mkdirs()
            tasks.writeText(
                """
                {
                  "version": "2.0.0",
                  "tasks": [
                    {
                      "label": "Codex: start",
                      "type": "shell",
                      "command": "codex",
                      "options": {
                        "cwd": "/workspace"
                      },
                      "problemMatcher": [],
                      "presentation": {
                        "reveal": "always",
                        "panel": "new",
                        "focus": true
                      }
                    },
                    {
                      "label": "Codex: version",
                      "type": "shell",
                      "command": "codex --version",
                      "options": {
                        "cwd": "/workspace"
                      },
                      "problemMatcher": [],
                      "presentation": {
                        "reveal": "always",
                        "panel": "shared"
                      }
                    }
                  ]
                }
                """.trimIndent() + "\n",
            )
        }
    }

    private fun repairDefaultDevWorkspaceDockerfile(project: File) {
        val dockerfile = File(project, "Dockerfile")
        val compose = File(project, "compose.yaml")
        if (!dockerfile.isFile || !compose.isFile) return
        val dockerfileText = runCatching { dockerfile.readText() }.getOrDefault("")
        val composeText = runCatching { compose.readText() }.getOrDefault("")
        val composeNeedsCodeServer = "/usr/local/bin/start-code-server" in composeText
        val dockerfileProvidesCodeServer =
            "COPY scripts/start-code-server.sh /usr/local/bin/start-code-server" in dockerfileText ||
                "start-code-server" in dockerfileText && "code-server.dev/install.sh" in dockerfileText
        val knownPlaceholder =
            dockerfileText.lineSequence().map { it.trim() }.filter { it.isNotBlank() }.toList() ==
                listOf("FROM ubuntu:22.04", "CMD [\"/bin/bash\", \"-lc\", \"echo hello from Dockerfile\"]")
        if (!composeNeedsCodeServer || dockerfileProvidesCodeServer && !knownPlaceholder) return
        val backup = File(project, "Dockerfile.pdocker-broken-backup")
        if (!backup.exists()) dockerfile.copyTo(backup, overwrite = false)
        assets.open("default-project/Dockerfile").use { input ->
            dockerfile.outputStream().use { output -> input.copyTo(output) }
        }
    }

    private fun migrateProjectPorts(project: File) {
        val replacements = mapOf(
            "0.0.0.0:8080" to "0.0.0.0:18080",
            "8080:8080" to "18080:18080",
            "CODE_SERVER_PORT:-8080" to "CODE_SERVER_PORT:-18080",
            "0.0.0.0:8081" to "0.0.0.0:18081",
            "8081:8081" to "18081:18081",
            "LLAMA_ARG_PORT:-8081" to "LLAMA_ARG_PORT:-18081",
            "- ./workspace:/workspace" to "- \${PDOCKER_FAST_WORKSPACE_HOST:-./workspace}:/workspace",
            "- ./models:/models" to "- \${PDOCKER_MODEL_HOST:-./models}:/models",
            "- ./continue:/workspace/.continue" to "- \${PDOCKER_DEV_STATE_HOST:-./state/dev}/continue:/workspace/.continue",
            "- ./vscode:/workspace/.vscode-server/data/User" to "- \${PDOCKER_DEV_STATE_HOST:-./state/dev}/vscode:/workspace/.vscode-server/data/User",
        )
        project.walkSafe(maxEntries = 2048)
            .filter { it.isFile && it.length() <= 512 * 1024 }
            .forEach { file ->
                val original = runCatching { file.readText() }.getOrNull() ?: return@forEach
                val migrated = replacements.entries.fold(original) { text, (from, to) ->
                    text.replace(from, to)
                }.let { text -> migrateComposeHeaderServiceLinks(file, text) }
                    .let { text -> migrateComposeDocuments(file, text) }
                if (migrated != original) file.writeText(migrated)
            }
    }

    private fun migrateComposeDocuments(file: File, text: String): String {
        if (file.name !in setOf("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")) {
            return text
        }
        var out = text
        if ("PDOCKER_DOCUMENTS_MOUNT:" !in out && "\n    environment:\n" in out) {
            out = out.replace(
                "\n    environment:\n",
                "\n    environment:\n" +
                    "      PDOCKER_DOCUMENTS_MOUNT: \"\${PDOCKER_DOCUMENTS_MOUNT:-/documents}\"\n" +
                    "      PDOCKER_SHARED_DOCUMENTS_MOUNT: \"\${PDOCKER_SHARED_DOCUMENTS_MOUNT:-/shared}\"\n" +
                    "      PDOCKER_EXPORT_DIR: \"\${PDOCKER_EXPORT_DIR:-/documents/pdocker-exports}\"\n" +
                    "      PDOCKER_FAST_WORKDIR: \"\${PDOCKER_FAST_WORKDIR:-/workspace}\"\n",
            )
        }
        if ("PDOCKER_DOCUMENTS_HOST" !in out && "\n    volumes:\n" in out) {
            out = out.replace(
                "\n    volumes:\n",
                "\n    volumes:\n" +
                    "      - \${PDOCKER_DOCUMENTS_HOST:-./documents}:\${PDOCKER_DOCUMENTS_MOUNT:-/documents}\n" +
                    "      - \${PDOCKER_SHARED_DOCUMENTS_HOST:-./shared-documents}:\${PDOCKER_SHARED_DOCUMENTS_MOUNT:-/shared}\n",
            )
        } else if ("PDOCKER_SHARED_DOCUMENTS_HOST" !in out && "\n    volumes:\n" in out) {
            out = out.replace(
                "\n    volumes:\n",
                "\n    volumes:\n" +
                    "      - \${PDOCKER_SHARED_DOCUMENTS_HOST:-./shared-documents}:\${PDOCKER_SHARED_DOCUMENTS_MOUNT:-/shared}\n",
            )
        }
        return out
    }

    private fun migrateComposeHeaderServiceLinks(file: File, text: String): String {
        if (file.name !in setOf("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")) {
            return text
        }
        val additions = mutableListOf<String>()
        if ("18080:18080" in text && "pdocker.service-url: 18080=" !in text) {
            additions += "# pdocker.service-url: 18080=VS Code"
        }
        if ("18080:18080" in text && "pdocker.auto-open: VS Code" !in text) {
            additions += "# pdocker.auto-open: VS Code"
        }
        if ("18081:18081" in text && "pdocker.service-url: 18081=" !in text) {
            additions += "# pdocker.service-url: 18081=llama.cpp"
        }
        if (additions.isEmpty()) return text
        return additions.joinToString("\n", postfix = "\n") + text
    }

    private fun copyAssetTree(assetPath: String, dest: File) {
        val children = assets.list(assetPath).orEmpty()
        if (children.isEmpty()) {
            if (!dest.exists()) {
                dest.parentFile?.mkdirs()
                assets.open(assetPath).use { input ->
                    dest.outputStream().use { output -> input.copyTo(output) }
                }
            }
            return
        }
        dest.mkdirs()
        children.forEach { child ->
            copyAssetTree("$assetPath/$child", File(dest, child))
        }
    }
}
