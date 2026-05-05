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

tasks.matching { it.name == "preBuild" || (it.name.startsWith("merge") && it.name.endsWith("Assets")) }
    .configureEach {
        dependsOn(syncPdockerdAsset)
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
