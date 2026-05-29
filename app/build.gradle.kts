import java.util.Properties
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val syncPdockerdAsset by tasks.registering(Copy::class) {
    into(layout.projectDirectory.dir("src/main/assets/pdockerd"))
    from(rootProject.file("docker-proot-setup/bin/pdockerd")) {
        rename { "pdockerd" }
    }
    from(rootProject.file("scripts/llama-gpu-env-manifest.json"))
}

val verifyPackagedPayloadFresh by tasks.registering {
    group = "verification"
    description = "Fail APK builds when generated native/backend payloads are older than their sources."
    dependsOn(syncPdockerdAsset)

    fun requireFresh(output: File, input: File, rebuildHint: String) {
        if (!input.isFile) {
            throw GradleException("Packaged payload freshness check failed: missing source $input")
        }
        if (!output.isFile) {
            throw GradleException(
                "Packaged payload freshness check failed: missing output $output\n" +
                    "Run: $rebuildHint"
            )
        }
        if (output.lastModified() < input.lastModified()) {
            throw GradleException(
                "Packaged payload freshness check failed: $output is older than $input\n" +
                    "Run: $rebuildHint"
            )
        }
    }

    fun requireSameBytes(output: File, input: File) {
        if (!input.isFile) {
            throw GradleException("Packaged payload freshness check failed: missing source $input")
        }
        if (!output.isFile) {
            throw GradleException("Packaged payload freshness check failed: missing output $output")
        }
        if (!output.readBytes().contentEquals(input.readBytes())) {
            throw GradleException(
                "Packaged payload freshness check failed: $output differs from $input\n" +
                    "Run: ./gradlew :app:syncPdockerdAsset"
            )
        }
    }

    doLast {
        val appCppDir = project.file("src/main/cpp")
        val gpuSrcDir = rootProject.file("docker-proot-setup/src/gpu")
        val backendLibDir = rootProject.file("docker-proot-setup/lib")
        val nativeHint = "bash scripts/build-native-android-ndk.sh"
        val gpuHint = "bash scripts/build-gpu-shim.sh"
        val stageHint = "bash scripts/copy-native.sh"

        val androidNativeAbis = listOf("arm64-v8a", "armeabi-v7a")
        androidNativeAbis.forEach { abi ->
            val abiDir = project.file("src/main/jniLibs/$abi")
            val directSource = if (abi == "armeabi-v7a") {
                appCppDir.resolve("pdocker_direct_unsupported.c")
            } else {
                appCppDir.resolve("pdocker_direct_exec.c")
            }
            requireFresh(abiDir.resolve("libpdockerpty.so"), appCppDir.resolve("pty.c"), nativeHint)
            requireFresh(abiDir.resolve("libpdockerdirect.so"), directSource, nativeHint)
            requireFresh(abiDir.resolve("libpdockergpuexecutor.so"), appCppDir.resolve("pdocker_gpu_executor.c"), nativeHint)
            requireFresh(abiDir.resolve("libpdockermediaexecutor.so"), appCppDir.resolve("pdocker_media_executor.c"), nativeHint)
        }
        androidNativeAbis.forEach { abi ->
            val glibcPayloadDir = project.file("src/main/jniLibs/$abi")
            requireFresh(glibcPayloadDir.resolve("libpdockergpushim.so"), gpuSrcDir.resolve("pdocker_gpu_shim.c"), gpuHint)
            requireFresh(glibcPayloadDir.resolve("libpdockervulkanicd.so"), gpuSrcDir.resolve("pdocker_vulkan_icd.c"), gpuHint)
            requireFresh(glibcPayloadDir.resolve("libpdockeropenclicd.so"), gpuSrcDir.resolve("pdocker_opencl_icd.c"), gpuHint)
        }

        val abiDir = project.file("src/main/jniLibs/arm64-v8a")

        val fdroidNoCrane = (
            System.getenv("SKYDNIR_FDROID_NO_CRANE") ?: System.getenv("PDOCKER_FDROID_NO_CRANE")
        )?.let {
            it.isNotBlank() && it != "0" && !it.equals("false", ignoreCase = true)
        } ?: false
        if (fdroidNoCrane) {
            if (abiDir.resolve("libcrane.so").exists()) {
                throw GradleException(
                    "F-Droid no-crane build must not stage libcrane.so; run SKYDNIR_FDROID_NO_CRANE=1 bash scripts/copy-native.sh"
                )
            }
        } else {
            requireFresh(abiDir.resolve("libcrane.so"), rootProject.file("docker-proot-setup/docker-bin/crane"), stageHint)
        }
        requireFresh(abiDir.resolve("libcow.so"), backendLibDir.resolve("libcow.so"), stageHint)
        val rootfsShim = backendLibDir.resolve("pdocker-rootfs-shim.so")
        if (rootfsShim.isFile) {
            requireFresh(abiDir.resolve("libpdocker-rootfs-shim.so"), rootfsShim, stageHint)
        }
        val glibcLoader = (
            System.getenv("SKYDNIR_GLIBC_LOADER") ?: System.getenv("PDOCKER_GLIBC_LOADER")
        )?.takeIf { it.isNotBlank() }?.let(::File)
        if (glibcLoader?.isFile == true) {
            requireFresh(abiDir.resolve("libpdocker-ld-linux-aarch64.so"), glibcLoader, stageHint)
        }

        requireSameBytes(
            project.file("src/main/assets/pdockerd/pdockerd"),
            rootProject.file("docker-proot-setup/bin/pdockerd")
        )
        requireSameBytes(
            project.file("src/main/assets/pdockerd/llama-gpu-env-manifest.json"),
            rootProject.file("scripts/llama-gpu-env-manifest.json")
        )
    }
}

val skydnirVersionProps = Properties().apply {
    rootProject.file("version.properties").inputStream().use(::load)
}

fun skydnirVersionValue(name: String): String =
    skydnirVersionProps.getProperty(name)
        ?: error("version.properties is missing required key '$name'")

fun buildConfigString(value: String): String =
    "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

fun nonBlankEnv(vararg names: String): String? =
    names.firstNotNullOfOrNull { name ->
        System.getenv(name)?.takeIf { it.isNotBlank() }
    }

fun gitOutput(vararg args: String): String? {
    return try {
        val process = ProcessBuilder(*args)
            .directory(rootProject.projectDir)
            .redirectErrorStream(true)
            .start()
        val output = process.inputStream.bufferedReader().readText().trim()
        if (process.waitFor() == 0 && output.isNotBlank()) output else null
    } catch (_: Exception) {
        null
    }
}

val skydnirBuildInstant = Instant.now()
val skydnirBuildTimeUtc =
    nonBlankEnv("SKYDNIR_BUILD_TIME_UTC", "PDOCKER_BUILD_TIME_UTC")
        ?: DateTimeFormatter.ISO_INSTANT.format(skydnirBuildInstant)
val skydnirBuildCommit =
    nonBlankEnv("SKYDNIR_BUILD_COMMIT", "PDOCKER_BUILD_COMMIT")
        ?: gitOutput("git", "rev-parse", "--short=12", "HEAD")
        ?: skydnirVersionValue("buildCommit")
val skydnirBuildNumber =
    nonBlankEnv("SKYDNIR_BUILD_NUMBER", "PDOCKER_BUILD_NUMBER")
        ?: DateTimeFormatter.ofPattern("yyyyMMdd.HHmmss")
            .withZone(ZoneOffset.UTC)
            .format(skydnirBuildInstant)

android {
    namespace = "io.github.ryo100794.pdocker"
    compileSdk = 34
    ndkVersion = "26.3.11579264"

    defaultConfig {
        applicationId = "io.github.ryo100794.pdocker"
        minSdk = 26
        targetSdk = 34
        versionCode = skydnirVersionValue("versionCode").toInt()
        versionName = skydnirVersionValue("versionName")
        buildConfigField("String", "BUILD_TIME_UTC", buildConfigString(skydnirBuildTimeUtc))
        buildConfigField("String", "BUILD_GIT_COMMIT", buildConfigString(skydnirBuildCommit))
        buildConfigField("String", "BUILD_NUMBER", buildConfigString(skydnirBuildNumber))
        manifestPlaceholders["pdockerDebugReceiverExported"] = "false"

        ndk {
            // Product APK currently promotes only the complete arm64 runtime.
            // armeabi-v7a helper/glibc payloads are built as evidence artifacts,
            // but must not be packaged until crane/libcow/direct-exec have a
            // complete 32-bit runtime gate.
            abiFilters += listOf("arm64-v8a")
        }
    }

    flavorDimensions += "runtime"
    productFlavors {
        create("modern") {
            dimension = "runtime"
            targetSdk = 34
            buildConfigField("String", "PDOCKER_RUNTIME_BACKEND", "\"no-proot\"")
            buildConfigField("Boolean", "PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC", "false")
            buildConfigField("String", "PDOCKER_RUNTIME_LABEL", "\"API34 direct/metadata\"")
        }
        create("compat") {
            dimension = "runtime"
            applicationIdSuffix = ".compat"
            versionNameSuffix = "-compat"
            targetSdk = 28
            buildConfigField("String", "PDOCKER_RUNTIME_BACKEND", "\"direct\"")
            buildConfigField("Boolean", "PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC", "true")
            buildConfigField("String", "PDOCKER_RUNTIME_LABEL", "\"SDK28 direct compat\"")
        }
    }

    sourceSets {
        getByName("main") {
            java.srcDirs("src/main/kotlin")
            jniLibs.srcDirs("src/main/jniLibs")
        }
        getByName("compat") {
            jniLibs.srcDirs("src/compat/jniLibs")
        }
    }

    val releaseSigningProps = Properties().apply {
        val propsFile = rootProject.file("release-signing.properties")
        if (propsFile.isFile) {
            propsFile.inputStream().use(::load)
        }
    }
    fun signingValue(name: String): String? =
        providers.environmentVariable("SKYDNIR_${name}").orNull
            ?: providers.environmentVariable("PDOCKER_${name}").orNull
            ?: releaseSigningProps.getProperty(name.lowercase().replace('_', '.'))

    signingConfigs {
        val storeFilePath = signingValue("SIGNING_STORE_FILE")
        if (!storeFilePath.isNullOrBlank()) {
            create("skydnirRelease") {
                storeFile = file(storeFilePath)
                storePassword = signingValue("SIGNING_STORE_PASSWORD")
                keyAlias = signingValue("SIGNING_KEY_ALIAS")
                keyPassword = signingValue("SIGNING_KEY_PASSWORD")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            signingConfigs.findByName("skydnirRelease")?.let { signingConfig = it }
        }
        getByName("debug") {
            isDebuggable = true
            manifestPlaceholders["pdockerDebugReceiverExported"] = "true"
        }
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
            // AGP's strip task invokes NDK's llvm-strip which is an
            // x86_64 ELF. On aarch64 hosts it fails to exec. Our .so
            // files are tiny (<20 KB) so skip stripping entirely.
            keepDebugSymbols += listOf("**/*.so")
        }
    }

    buildFeatures {
        buildConfig = true
    }
}

tasks.matching {
    it.name == "preBuild" ||
        (it.name.startsWith("merge") && (it.name.endsWith("Assets") || it.name.endsWith("NativeLibs")))
}
    .configureEach {
        dependsOn(verifyPackagedPayloadFresh)
    }

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            // pdockerd uses stdlib only
        }
        pyc {
            src = false
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("com.google.android.material:material:1.12.0")
}
