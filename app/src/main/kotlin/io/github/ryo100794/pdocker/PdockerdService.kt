package io.github.ryo100794.pdocker

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Binder
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * ForegroundService hosting pdockerd (Python via Chaquopy).
 *
 * Lifecycle:
 *  - START_STICKY so Android restarts us after OOM.
 *  - Chaquopy platform initialised once per process.
 *  - pdockerd runs on a background thread; stop flag drives clean shutdown.
 */
class PdockerdService : Service() {

    inner class LocalBinder : Binder()

    private val binder = LocalBinder()
    private var pdockerThread: Thread? = null
    private var gpuExecutorProcess: Process? = null
    private var mediaExecutorProcess: Process? = null
    private var startWakeLock: PowerManager.WakeLock? = null
    @Volatile private var stopFlag = false
    @Volatile private var userStopped = false

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            userStopped = true
            stopFlag = true
            pdockerThread?.interrupt()
            stopGpuExecutor()
            stopMediaExecutor()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }

        userStopped = false
        holdStartWakeLock()
        startInForeground()
        if (pdockerThread == null || !pdockerThread!!.isAlive) {
            startPdockerd()
        }
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (!userStopped) {
            scheduleRestart()
        }
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        stopFlag = true
        pdockerThread?.interrupt()
        stopGpuExecutor()
        stopMediaExecutor()
        releaseStartWakeLock()
        if (!userStopped) {
            scheduleRestart()
        }
        super.onDestroy()
    }

    private fun startInForeground() {
        val channelId = "pdockerd"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(channelId) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(channelId, getString(R.string.pdockerd_notification_channel),
                        NotificationManager.IMPORTANCE_LOW)
                )
            }
        }
        val pendingFlags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val openIntent = PendingIntent.getActivity(
            this,
            10,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            pendingFlags,
        )
        val stopIntent = PendingIntent.getService(
            this,
            11,
            Intent(this, PdockerdService::class.java).setAction(ACTION_STOP),
            pendingFlags,
        )
        val notif: Notification = Notification.Builder(this, channelId)
            .setContentTitle(getString(R.string.pdockerd_notification_title))
            .setContentText(getString(R.string.pdockerd_notification_text))
            .setSmallIcon(android.R.drawable.ic_menu_manage)
            .setContentIntent(openIntent)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, getString(R.string.action_stop), stopIntent)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    private fun startPdockerd() {
        stopFlag = false

        pdockerThread = Thread({
            try {
                val appContext = applicationContext
                val runtime = PdockerdRuntime.prepare(appContext)
                startGpuExecutor(runtime)
                startMediaExecutor(runtime)
                val home = File(filesDir, "pdocker").apply { mkdirs() }
                val sock = File(home, "pdockerd.sock")
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(appContext))
                }
                val py = Python.getInstance()
                val mod = py.getModule("pdockerd_bridge")
                mod.callAttr(
                    "run_daemon",
                    sock.absolutePath,
                    home.absolutePath,
                    runtime.absolutePath,
                    BuildConfig.PDOCKER_RUNTIME_BACKEND,
                    BuildConfig.PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC,
                )
            } catch (t: Throwable) {
                Log.e(TAG, "pdockerd crashed", t)
            } finally {
                if (!stopFlag && !userStopped) {
                    pdockerThread = null
                    scheduleRestart()
                    stopSelf()
                }
            }
        }, "pdockerd").also { it.start() }
    }

    private fun startGpuExecutor(runtime: File) {
        if (gpuExecutorProcess?.isAlive == true) return
        val executor = File(runtime, "gpu/pdocker-gpu-executor")
        if (!executor.canExecute()) {
            Log.i(TAG, "GPU executor unavailable at $executor")
            return
        }
        val socket = File(runtime, "gpu/pdocker-gpu.sock")
        runCatching {
            socket.delete()
            val process = ProcessBuilder(executor.absolutePath, "--serve-socket", socket.absolutePath)
                .redirectErrorStream(true)
                .start()
            gpuExecutorProcess = process
            Thread({
                process.inputStream.bufferedReader().useLines { lines ->
                    lines.forEach { Log.i(TAG, "gpu-executor: $it") }
                }
            }, "pdocker-gpu-executor-log").apply {
                isDaemon = true
                start()
            }
        }.onFailure {
            Log.w(TAG, "failed to start GPU executor", it)
        }
    }

    private fun stopGpuExecutor() {
        gpuExecutorProcess?.destroy()
        gpuExecutorProcess = null
    }

    private fun startMediaExecutor(runtime: File) {
        if (mediaExecutorProcess?.isAlive == true) return
        val mediaDir = File(runtime, "media").apply { mkdirs() }
        writeMediaDescriptor(mediaDir)
        val executor = File(mediaDir, "pdocker-media-executor")
        if (!executor.canExecute()) {
            Log.i(TAG, "media bridge phase-1 control plane only; executor unavailable at $executor")
            return
        }
        val socket = File(mediaDir, "pdocker-media.sock")
        runCatching {
            socket.delete()
            val descriptor = File(mediaDir, "pdocker-media-capabilities.json")
            val process = ProcessBuilder(
                executor.absolutePath,
                "--serve-socket",
                socket.absolutePath,
                "--descriptor",
                descriptor.absolutePath,
            )
                .redirectErrorStream(true)
                .start()
            mediaExecutorProcess = process
            Thread({
                process.inputStream.bufferedReader().useLines { lines ->
                    lines.forEach { Log.i(TAG, "media-executor: $it") }
                }
            }, "pdocker-media-executor-log").apply {
                isDaemon = true
                start()
            }
        }.onFailure {
            Log.w(TAG, "failed to start media executor", it)
        }
    }

    private fun stopMediaExecutor() {
        mediaExecutorProcess?.destroy()
        mediaExecutorProcess = null
    }

    private fun writeMediaDescriptor(mediaDir: File) {
        val descriptor = JSONObject()
            .put("Kind", "android-media-bridge-phase1")
            .put("Contract", "linux-like-socket-env-v1")
            .put("CommandApi", "pdocker-media-command-v1")
            .put("AbiVersion", "0.1")
            .put("RawDevicePassthrough", false)
            .put("CaptureReady", false)
            .put("CameraReady", false)
            .put("AudioReady", false)
            .put("AndroidPublicApis", JSONArray(listOf("Camera2", "AudioRecord", "AudioTrack", "AudioManager")))
            .put("Video", mediaVideoDescriptor())
            .put("Audio", mediaAudioDescriptor())
        File(mediaDir, "pdocker-media-capabilities.json").writeText(descriptor.toString(2))
    }

    private fun mediaVideoDescriptor(): JSONObject {
        val cameras = JSONArray()
        val errors = JSONArray()
        val cameraPermission = checkSelfPermission(android.Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        runCatching {
            val manager = getSystemService(CameraManager::class.java) ?: return@runCatching
            for (id in manager.cameraIdList) {
                val chars = manager.getCameraCharacteristics(id)
                cameras.put(
                    JSONObject()
                        .put("Id", id)
                        .put("Facing", lensFacingName(chars.get(CameraCharacteristics.LENS_FACING)))
                        .put("HardwareLevel", hardwareLevelName(chars.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL)))
                )
            }
        }.onFailure {
            errors.put(it.javaClass.simpleName + ": " + (it.message ?: "camera probe failed"))
        }
        return JSONObject()
            .put("TargetApi", "Camera2")
            .put("Targets", JSONArray(listOf("camera.front", "camera.rear", "camera.external")))
            .put("RuntimePermissionGranted", cameraPermission)
            .put("Devices", cameras)
            .put("Errors", errors)
    }

    private fun mediaAudioDescriptor(): JSONObject {
        val inputs = JSONArray()
        val outputs = JSONArray()
        val errors = JSONArray()
        val audioPermission = checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        runCatching {
            val manager = getSystemService(AudioManager::class.java) ?: return@runCatching
            manager.getDevices(AudioManager.GET_DEVICES_INPUTS).forEach { inputs.put(audioDeviceJson(it)) }
            manager.getDevices(AudioManager.GET_DEVICES_OUTPUTS).forEach { outputs.put(audioDeviceJson(it)) }
        }.onFailure {
            errors.put(it.javaClass.simpleName + ": " + (it.message ?: "audio device probe failed"))
        }
        return JSONObject()
            .put("TargetApis", JSONArray(listOf("AudioRecord", "AudioTrack", "AudioManager")))
            .put("Targets", JSONArray(listOf("audio.capture", "audio.playback", "audio.usb.multichannel")))
            .put("RuntimePermissionGranted", audioPermission)
            .put("Inputs", inputs)
            .put("Outputs", outputs)
            .put("UsbMultichannelPresent", hasUsbMultichannel(inputs) || hasUsbMultichannel(outputs))
            .put("Errors", errors)
    }

    private fun audioDeviceJson(device: AudioDeviceInfo): JSONObject =
        JSONObject()
            .put("Id", device.id)
            .put("Type", audioDeviceTypeName(device.type))
            .put("ProductName", device.productName?.toString() ?: "")
            .put("IsSource", device.isSource)
            .put("IsSink", device.isSink)
            .put("ChannelCounts", intArrayJson(device.channelCounts))
            .put("SampleRates", intArrayJson(device.sampleRates))

    private fun intArrayJson(values: IntArray): JSONArray =
        JSONArray().also { arr -> values.forEach { arr.put(it) } }

    private fun hasUsbMultichannel(devices: JSONArray): Boolean {
        for (i in 0 until devices.length()) {
            val device = devices.optJSONObject(i) ?: continue
            if (!device.optString("Type").startsWith("usb.")) continue
            val counts = device.optJSONArray("ChannelCounts") ?: continue
            for (j in 0 until counts.length()) {
                if (counts.optInt(j) > 2) return true
            }
        }
        return false
    }

    private fun lensFacingName(value: Int?): String = when (value) {
        CameraCharacteristics.LENS_FACING_FRONT -> "front"
        CameraCharacteristics.LENS_FACING_BACK -> "rear"
        CameraCharacteristics.LENS_FACING_EXTERNAL -> "external"
        else -> "unknown"
    }

    private fun hardwareLevelName(value: Int?): String = when (value) {
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LEGACY -> "legacy"
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LIMITED -> "limited"
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_FULL -> "full"
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_3 -> "level3"
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_EXTERNAL -> "external"
        else -> "unknown"
    }

    private fun audioDeviceTypeName(type: Int): String = when (type) {
        AudioDeviceInfo.TYPE_BUILTIN_MIC -> "builtin.microphone"
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "builtin.speaker"
        AudioDeviceInfo.TYPE_USB_DEVICE -> "usb.device"
        AudioDeviceInfo.TYPE_USB_HEADSET -> "usb.headset"
        AudioDeviceInfo.TYPE_WIRED_HEADSET -> "wired.headset"
        AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "wired.headphones"
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "bluetooth.sco"
        AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "bluetooth.a2dp"
        AudioDeviceInfo.TYPE_HDMI -> "hdmi"
        AudioDeviceInfo.TYPE_HDMI_ARC -> "hdmi.arc"
        AudioDeviceInfo.TYPE_HDMI_EARC -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) "hdmi.earc" else "hdmi.earc"
        else -> "type.$type"
    }

    private fun holdStartWakeLock() {
        runCatching {
            val power = getSystemService(PowerManager::class.java) ?: return
            val lock = startWakeLock ?: power.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "$packageName:pdockerd-start",
            ).also { startWakeLock = it }
            if (!lock.isHeld) {
                lock.acquire(START_WAKELOCK_MS)
            }
        }.onFailure {
            Log.w(TAG, "failed to acquire start wakelock", it)
        }
    }

    private fun releaseStartWakeLock() {
        runCatching {
            startWakeLock?.takeIf { it.isHeld }?.release()
        }
    }

    private fun scheduleRestart() {
        val intent = Intent(applicationContext, PdockerdService::class.java)
            .setAction(ACTION_START)
        val pendingFlags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val pendingIntent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            PendingIntent.getForegroundService(applicationContext, 12, intent, pendingFlags)
        } else {
            PendingIntent.getService(applicationContext, 12, intent, pendingFlags)
        }
        val alarm = getSystemService(Context.ALARM_SERVICE) as android.app.AlarmManager
        val at = System.currentTimeMillis() + RESTART_DELAY_MS
        runCatching {
            when {
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ->
                    alarm.setExactAndAllowWhileIdle(android.app.AlarmManager.RTC_WAKEUP, at, pendingIntent)
                else ->
                    alarm.set(android.app.AlarmManager.RTC_WAKEUP, at, pendingIntent)
            }
        }.onFailure {
            Log.w(TAG, "exact pdockerd restart alarm rejected; falling back to inexact alarm", it)
            alarm.set(android.app.AlarmManager.RTC_WAKEUP, at, pendingIntent)
        }
    }

    companion object {
        const val ACTION_START = "io.github.ryo100794.pdocker.action.START"
        const val ACTION_STOP = "io.github.ryo100794.pdocker.action.STOP"
        private const val NOTIF_ID = 1
        private const val RESTART_DELAY_MS = 2_000L
        private const val START_WAKELOCK_MS = 30_000L
        private const val TAG = "pdockerd"
    }
}
