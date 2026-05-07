import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val syncPdockerdAsset by tasks.registering(Copy::class) {
    from(rootProject.file("docker-proot-setup/bin/pdockerd"))
    into(layout.projectDirectory.dir("src/main/assets/pdockerd"))
    rename { "pdockerd" }
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
        val abiDir = project.file("src/main/jniLibs/arm64-v8a")
        val appCppDir = project.file("src/main/cpp")
        val gpuSrcDir = rootProject.file("docker-proot-setup/src/gpu")
        val backendLibDir = rootProject.file("docker-proot-setup/lib")
        val nativeHint = "bash scripts/build-native-termux.sh"
        val gpuHint = "bash scripts/build-gpu-shim.sh"
        val stageHint = "bash scripts/copy-native.sh"

        requireFresh(abiDir.resolve("libpdockerpty.so"), appCppDir.resolve("pty.c"), nativeHint)
        requireFresh(abiDir.resolve("libpdockerdirect.so"), appCppDir.resolve("pdocker_direct_exec.c"), nativeHint)
        requireFresh(abiDir.resolve("libpdockergpuexecutor.so"), appCppDir.resolve("pdocker_gpu_executor.c"), nativeHint)
        requireFresh(abiDir.resolve("libpdockermediaexecutor.so"), appCppDir.resolve("pdocker_media_executor.c"), nativeHint)

        requireFresh(abiDir.resolve("libpdockergpushim.so"), gpuSrcDir.resolve("pdocker_gpu_shim.c"), gpuHint)
        requireFresh(abiDir.resolve("libpdockervulkanicd.so"), gpuSrcDir.resolve("pdocker_vulkan_icd.c"), gpuHint)
        requireFresh(abiDir.resolve("libpdockeropenclicd.so"), gpuSrcDir.resolve("pdocker_opencl_icd.c"), gpuHint)

        requireFresh(abiDir.resolve("libcrane.so"), rootProject.file("docker-proot-setup/docker-bin/crane"), stageHint)
        requireFresh(abiDir.resolve("libcow.so"), backendLibDir.resolve("libcow.so"), stageHint)
        val rootfsShim = backendLibDir.resolve("pdocker-rootfs-shim.so")
        if (rootfsShim.isFile) {
            requireFresh(abiDir.resolve("libpdocker-rootfs-shim.so"), rootfsShim, stageHint)
        }
        val glibcLoader = System.getenv("PDOCKER_GLIBC_LOADER")?.takeIf { it.isNotBlank() }?.let(::File)
        if (glibcLoader?.isFile == true) {
            requireFresh(abiDir.resolve("libpdocker-ld-linux-aarch64.so"), glibcLoader, stageHint)
        }

        requireSameBytes(
            project.file("src/main/assets/pdockerd/pdockerd"),
            rootProject.file("docker-proot-setup/bin/pdockerd")
        )
    }
}

val pdockerVersionProps = Properties().apply {
    rootProject.file("version.properties").inputStream().use(::load)
}

fun pdockerVersionValue(name: String): String =
    pdockerVersionProps.getProperty(name)
        ?: error("version.properties is missing required key '$name'")

fun buildConfigString(value: String): String =
    "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

android {
    namespace = "io.github.ryo100794.pdocker"
    compileSdk = 34
    ndkVersion = "26.3.11579264"

    defaultConfig {
        applicationId = "io.github.ryo100794.pdocker"
        minSdk = 26
        targetSdk = 34
        versionCode = pdockerVersionValue("versionCode").toInt()
        versionName = pdockerVersionValue("versionName")
        buildConfigField("String", "BUILD_TIME_UTC", buildConfigString(pdockerVersionValue("buildTimeUtc")))
        buildConfigField("String", "BUILD_GIT_COMMIT", buildConfigString(pdockerVersionValue("buildCommit")))
        buildConfigField("String", "BUILD_NUMBER", buildConfigString(pdockerVersionValue("buildNumber")))
        manifestPlaceholders["pdockerDebugReceiverExported"] = "false"

        ndk {
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
        providers.environmentVariable("PDOCKER_${name}").orNull
            ?: releaseSigningProps.getProperty(name.lowercase().replace('_', '.'))

    signingConfigs {
        val storeFilePath = signingValue("SIGNING_STORE_FILE")
        if (!storeFilePath.isNullOrBlank()) {
            create("pdockerRelease") {
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
            signingConfigs.findByName("pdockerRelease")?.let { signingConfig = it }
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
