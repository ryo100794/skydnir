package io.github.ryo100794.pdocker

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.os.Build
import java.io.File
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

class PdockerdDebugReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val debuggable = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        if (!debuggable) return
        when (intent.action) {
            ACTION_SMOKE_START -> {
                val service = Intent(context, PdockerdService::class.java)
                    .setAction(PdockerdService.ACTION_START)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(service)
                } else {
                    context.startService(service)
                }
            }
            ACTION_SMOKE_GPU_BENCH -> {
                val pending = goAsync()
                val benchDir = File(context.filesDir, "pdocker/bench").apply { mkdirs() }
                File(benchDir, "android-gpu-bench-receiver.txt").writeText("received\n")
                thread(isDaemon = true, name = "android-gpu-bench-broadcast") {
                    runCatching { AndroidGpuBench.run(context.applicationContext) }
                        .onSuccess { File(benchDir, "android-gpu-bench-receiver.txt").writeText("complete\n") }
                        .onFailure { File(benchDir, "android-gpu-bench-error.txt").writeText(it.stackTraceToString()) }
                    pending.finish()
                }
            }
            ACTION_SMOKE_DIRECT_EXEC -> {
                val pending = goAsync()
                thread(isDaemon = true, name = "skydnir-direct-exec-broadcast") {
                    runCatching { runDirectExecProbe(context.applicationContext) }
                        .onFailure {
                            File(context.filesDir, "pdocker/direct-exec-probe.txt").apply {
                                parentFile?.mkdirs()
                                writeText("exception\n${it.stackTraceToString()}")
                            }
                        }
                    pending.finish()
                }
            }
        }
    }

    private fun runDirectExecProbe(context: Context) {
        val runtime = PdockerdRuntime.prepare(context)
        val home = File(context.filesDir, "pdocker")
        val rootfs = firstRootfs(File(home, "containers"))
        val out = File(home, "direct-exec-probe.txt").apply { parentFile?.mkdirs() }
        if (rootfs == null) {
            out.writeText("rootfs-missing\n")
            return
        }
        val cmd = listOf(
            File(runtime, "docker-bin/pdocker-direct").absolutePath,
            "run",
            "--mode", "debug-receiver",
            "--rootfs", rootfs.absolutePath,
            "--workdir", "/",
            "--env", "HOME=/root",
            "--", "/bin/sh", "-c", "echo java_process_builder_ok; ls / | head -5",
        )
        val env = mapOf(
            "PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC" to "1",
            "PDOCKER_DIRECT_TRACE_SYSCALLS" to "1",
        )
        val proc = ProcessBuilder(cmd)
            .redirectErrorStream(true)
            .apply { environment().putAll(env) }
            .start()
        val finished = proc.waitFor(10, TimeUnit.SECONDS)
        if (!finished) proc.destroyForcibly()
        val text = proc.inputStream.bufferedReader().readText()
        val rc = if (finished) proc.exitValue() else -124
        out.writeText("rc=$rc\nrootfs=${rootfs.absolutePath}\n$text")
    }

    private fun firstRootfs(root: File): File? {
        if (!root.exists()) return null
        var visited = 0
        return root.walkTopDown()
            .onEnter { visited < 512 }
            .firstOrNull {
                visited += 1
                it.isDirectory && it.name == "rootfs"
            }
    }

    companion object {
        const val ACTION_SMOKE_START = "io.github.ryo100794.pdocker.action.SMOKE_START"
        const val ACTION_SMOKE_GPU_BENCH = "io.github.ryo100794.pdocker.action.SMOKE_GPU_BENCH"
        const val ACTION_SMOKE_DIRECT_EXEC = "io.github.ryo100794.pdocker.action.SMOKE_DIRECT_EXEC"
    }
}
