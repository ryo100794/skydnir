package io.github.ryo100794.pdocker

import android.content.Context
import android.net.ConnectivityManager
import android.os.Build
import android.util.Log
import java.io.File

/**
 * Assembles a pdockerd runtime layout under filesDir/pdocker-runtime/
 * that matches what pdockerd expects at import time:
 *
 *   runtime/
 *   ├── bin/pdockerd
 *   ├── docker-bin/
 *   │   ├── crane          (-> nativeLibraryDir/libcrane.so)
 *   │   ├── pdocker-direct (-> nativeLibraryDir/libpdockerdirect.so)
 *   │   ├── pdocker-ld-linux-aarch64 (-> nativeLibraryDir/libpdocker-ld-linux-aarch64.so)
 *   ├── gpu/
 *   │   └── pdocker-gpu-executor (-> nativeLibraryDir/libpdockergpuexecutor.so)
 *   ├── media/
 *   │   └── pdocker-media-executor (-> nativeLibraryDir/libpdockermediaexecutor.so)
 *   ├── etc/resolv.conf    (DNS nameservers discovered from Android networks)
 *   └── lib/
 *       ├── libcow.so      (-> nativeLibraryDir/libcow.so)
 *       ├── pdocker-gpu-shim (-> nativeLibraryDir/libpdockergpushim.so)
 *       ├── pdocker-vulkan-icd.so (-> nativeLibraryDir/libpdockervulkanicd.so)
 *       ├── pdocker-opencl-icd.so (-> nativeLibraryDir/libpdockeropenclicd.so)
 *       ├── libOpenCL.so   (-> nativeLibraryDir/libpdockeropenclicd.so)
 *       └── pdocker-rootfs-shim.so (-> nativeLibraryDir/libpdocker-rootfs-shim.so)
 *
 * pdockerd derives _PROJECT_DIR = dirname(dirname(__file__)), so running
 * runtime/bin/pdockerd makes it find everything via the expected relative
 * paths. Symlinks point at real ELFs in nativeLibraryDir — the only
 * location where Android allows execve on API 29+.
 *
 * DNS: container processes use glibc and read /etc/resolv.conf directly.
 * Android does not expose a normal host /etc/resolv.conf to apps, so we
 * discover DNS servers from ConnectivityManager and pdockerd injects this
 * file into build and runtime rootfs state.
 */
object PdockerdRuntime {
    private const val TAG = "skydnird-runtime"

    private const val FALLBACK_RESOLV_CONF = """nameserver 8.8.8.8
nameserver 1.1.1.1
"""

    fun prepare(ctx: Context): File {
        val root = File(ctx.filesDir, "pdocker-runtime")
        val bin = File(root, "bin").apply { mkdirs() }
        val dockerBin = File(root, "docker-bin").apply { mkdirs() }
        val dockerCliPlugins = File(dockerBin, "cli-plugins").apply { mkdirs() }
        val gpuBin = File(root, "gpu").apply { mkdirs() }
        val mediaBin = File(root, "media").apply { mkdirs() }
        val lib = File(root, "lib").apply { mkdirs() }
        val etc = File(root, "etc").apply { mkdirs() }
        // Android app sandboxes have no writable /tmp, so keep runtime temp
        // files inside the app's data dir.
        File(root, "tmp").apply { mkdirs() }

        val nativeDir = File(ctx.applicationInfo.nativeLibraryDir)
        val versionStamp = File(root, ".apk-version")
        val currentVersion = longVersionCode(ctx).toString()

        val versionChanged = versionStamp.readTextOrNull() != currentVersion
        // Debug/dev installs often reuse the same versionCode. Always refresh
        // pdockerd so Engine API fixes are not hidden behind a stale extracted
        // daemon after `adb install -r`.
        extractAsset(ctx, "pdockerd/pdockerd", File(bin, "pdockerd"), force = true)
        extractAsset(ctx, "pdockerd/llama-gpu-env-manifest.json", File(bin, "llama-gpu-env-manifest.json"), force = true)

        optionalLinkTo(File(nativeDir, "libcrane.so"), File(dockerBin, "crane"))
        optionalLinkTo(File(nativeDir, "libpdockerdirect.so"), File(dockerBin, "pdocker-direct"))
        optionalLinkTo(File(nativeDir, "libpdocker-ld-linux-aarch64.so"), File(dockerBin, "pdocker-ld-linux-aarch64"))
        optionalLinkTo(File(nativeDir, "libpdockergpuexecutor.so"), File(gpuBin, "pdocker-gpu-executor"))
        optionalLinkTo(File(nativeDir, "libpdockermediaexecutor.so"), File(mediaBin, "pdocker-media-executor"))
        java.nio.file.Files.deleteIfExists(File(dockerBin, "proot").toPath())
        java.nio.file.Files.deleteIfExists(File(dockerBin, "proot-loader").toPath())
        java.nio.file.Files.deleteIfExists(File(dockerBin, "pl").toPath())
        // The product APK intentionally does not bundle upstream Docker CLI or
        // Compose plugin binaries. Host/device tests may stage those tools
        // separately, but normal app UI must use pdockerd's Engine API and
        // native orchestrator paths.
        java.nio.file.Files.deleteIfExists(File(dockerBin, "docker").toPath())
        java.nio.file.Files.deleteIfExists(File(dockerCliPlugins, "docker-compose").toPath())
        java.nio.file.Files.deleteIfExists(File(dockerBin, "docker-compose").toPath())
        linkTo(File(nativeDir, "libcow.so"),           File(lib, "libcow.so"))
        optionalLinkTo(File(nativeDir, "libpdockergpushim.so"), File(lib, "pdocker-gpu-shim"))
        optionalLinkTo(File(nativeDir, "libpdockervulkanicd.so"), File(lib, "pdocker-vulkan-icd.so"))
        optionalLinkTo(File(nativeDir, "libpdockeropenclicd.so"), File(lib, "pdocker-opencl-icd.so"))
        optionalLinkTo(File(nativeDir, "libpdockeropenclicd.so"), File(lib, "libOpenCL.so"))
        optionalLinkTo(File(nativeDir, "libpdockeropenclicd.so"), File(lib, "libOpenCL.so.1"))
        optionalLinkTo(File(nativeDir, "libpdocker-rootfs-shim.so"), File(lib, "pdocker-rootfs-shim.so"))
        java.nio.file.Files.deleteIfExists(File(lib, "libtalloc.so").toPath())

        writeIfChanged(File(etc, "resolv.conf"), androidResolvConf(ctx))

        if (versionChanged) versionStamp.writeText(currentVersion)

        return root
    }

    private fun writeIfChanged(dst: File, content: String) {
        if (!dst.exists() || dst.readText() != content) {
            dst.writeText(content)
            Log.i(TAG, "wrote ${content.length} bytes to $dst")
        }
    }

    private fun androidResolvConf(ctx: Context): String {
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return FALLBACK_RESOLV_CONF
        val networks = buildList {
            cm.activeNetwork?.let { add(it) }
            cm.allNetworks.forEach { if (!contains(it)) add(it) }
        }
        val servers = LinkedHashSet<String>()
        for (network in networks) {
            val props = cm.getLinkProperties(network) ?: continue
            for (addr in props.dnsServers) {
                val host = addr.hostAddress?.substringBefore('%') ?: continue
                if (host.isNotBlank() && host != "::1" && host != "127.0.0.1") {
                    servers.add(host)
                }
            }
        }
        if (servers.isEmpty()) return FALLBACK_RESOLV_CONF
        return servers.joinToString(separator = "") { "nameserver $it\n" }
    }

    private fun extractAsset(ctx: Context, assetPath: String, dst: File, force: Boolean) {
        val assetBytes = ctx.assets.open(assetPath).use { input ->
            input.readBytes()
        }
        if (!force && dst.exists()) {
            val existing = runCatching { dst.readBytes() }.getOrNull()
            if (existing != null && existing.contentEquals(assetBytes)) return
        }
        dst.outputStream().use { output -> output.write(assetBytes) }
        Log.i(TAG, "extracted $assetPath -> $dst (${dst.length()} bytes)")
    }

    private fun longVersionCode(ctx: Context): Long {
        val pi = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            pi.longVersionCode
        } else {
            @Suppress("DEPRECATION") pi.versionCode.toLong()
        }
    }

    private fun File.readTextOrNull(): String? =
        if (exists()) runCatching { readText() }.getOrNull() else null

    private fun linkTo(target: File, link: File) {
        // Unconditionally delete the existing entry. link.exists() returns
        // false for a dangling symlink (canonicalPath to a now-deleted APK
        // install dir), and if we skip the delete, createSymbolicLink hits
        // EEXIST and the copyTo fallback follows the dead symlink into
        // nowhere with ENOENT. Files.deleteIfExists acts on the link itself,
        // not the target.
        java.nio.file.Files.deleteIfExists(link.toPath())
        try {
            java.nio.file.Files.createSymbolicLink(link.toPath(), target.toPath())
        } catch (e: Exception) {
            // Rare filesystems lack symlink support — fall back to a hard copy.
            target.copyTo(link, overwrite = true)
            link.setExecutable(true, false)
            Log.w(TAG, "symlink failed for $link, copied instead: ${e.message}")
        }
    }

    private fun optionalLinkTo(target: File, link: File) {
        if (!target.exists()) {
            java.nio.file.Files.deleteIfExists(link.toPath())
            Log.i(TAG, "optional runtime binary absent, skipped $link")
            return
        }
        linkTo(target, link)
    }
}
