import org.gradle.api.execution.TaskExecutionGraph
import org.gradle.kotlin.dsl.closureOf
import java.io.File as PlainFile
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// The server URL baked into the APK (BuildConfig.DEFAULT_INGEST_URL). Resolved once: the BuildConfig field and the
// release guard below must agree on it.
val defaultIngestUrl: String = (project.findProperty("btbDefaultIngestUrl") as? String)
    ?: System.getenv("BTB_DEFAULT_INGEST_URL")
    ?: "ws://10.0.2.2:8000/ingest-events"

// The release keystore (T2.8). Resolved at the top level so the release guard below can name the same path.
// NOTE: Project.file() always returns an absolute path, so the isAbsolute test must run on the *raw* argument,
// otherwise "release.jks" would silently resolve to app/release.jks and the release build would quietly fall
// back to the debug key.
val releaseKeystorePath: String = System.getenv("BTB_KEYSTORE_PATH")
    ?: project.findProperty("btbKeystorePath") as? String
    ?: "release.jks"
val releaseKeystoreFile: PlainFile = PlainFile(releaseKeystorePath).let {
    if (it.isAbsolute) it else rootProject.file(releaseKeystorePath)
}

// UNSAFE, local testing only: lets assembleRelease/bundleRelease build with a non-wss default URL (H2).
val allowCleartextRelease = (project.findProperty("btbAllowCleartextRelease") as? String)
    ?.equals("true", ignoreCase = true) == true

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.btb.ondevice"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.btb.ondevice"
        // 26 = Android 8. CameraX supports it; the thermal-status listener used later is guarded
        // at API 29. Phase 0 targets 3 GB Android Go-ish handsets, which are all >= 26.
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-scaffold"

        // Keep the v7a slice: some of the 3 GB Android Go-ish handsets in Phase 0 are 32-bit only.
        // Drop "armeabi-v7a" if the Phase 0 devices turn out to be arm64 across the board.
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }

        buildConfigField("String", "DEFAULT_INGEST_URL", "\"$defaultIngestUrl\"")

        // On-device tests (src/androidTest), run with `make android-test-device`.
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        create("release") {
            if (releaseKeystoreFile.exists()) {
                storeFile = releaseKeystoreFile
                storePassword = System.getenv("BTB_KEYSTORE_PASSWORD")
                    ?: project.findProperty("btbKeystorePassword") as? String
                    ?: "behindthebarks"
                keyAlias = System.getenv("BTB_KEY_ALIAS")
                    ?: project.findProperty("btbKeyAlias") as? String
                    ?: "btb_release"
                keyPassword = System.getenv("BTB_KEY_PASSWORD")
                    ?: project.findProperty("btbKeyPassword") as? String
                    ?: "behindthebarks"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            val releaseSigning = signingConfigs.findByName("release")
            // Unsigned when the keystore is absent. That is never shipped: the release guard below fails the
            // release tasks first, with the keystore message. Configuration itself must not fail, or debug
            // builds, unit tests and CI's debug steps would need the release key too.
            signingConfig = releaseSigning?.takeIf { releaseKeystoreFile.exists() }
        }
        debug {
            isMinifyEnabled = false
        }
    }

    // Never ship a tester build signed with the debug key by accident: a silent fallback means the APK
    // cannot be updated in place and looks "broken" for no visible reason. This runs only when a release
    // artifact is requested, so unit tests and debug builds (and CI, which has no keystore) are unaffected.
    project.gradle.taskGraph.whenReady {
        val releaseRequested = allTasks.any { it.name == "assembleRelease" || it.name == "bundleRelease" }
        if (releaseRequested && !releaseKeystoreFile.exists()) {
            throw GradleException(
                "release signing key not found (BTB_KEYSTORE_PATH=$releaseKeystorePath). " +
                    "Run scripts/generate_release_keystore.sh, or pass -PbtbKeystorePath, " +
                    "or build the debug APK instead.",
            )
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        jniLibs {
            // Store .so uncompressed so they stay page-aligned: 16 KB devices cannot map
            // compressed or 4 KB-aligned libraries. Verified in CI by `make android-build`.
            useLegacyPackaging = false
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
        unitTests.all {
            it.useJUnitPlatform()
        }
    }

    // NewApi and MissingClass fail the build: unit tests happily compile a theme that only exists
    // from API 29 while minSdk is 26, and only lint sees that the app would crash on launch.
    lint {
        abortOnError = true
    }

    sourceSets {
        getByName("main") {
            assets.srcDirs("src/main/assets")
        }
        getByName("test") {
            resources.srcDirs("src/test/resources", "../../tests/android/fixtures", "src/main/assets")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

// H2 build guard: a release APK must default to a wss:// server. The release app refuses ws:// and http:// at
// runtime anyway, so a cleartext default would ship a build that cannot connect. Checked before any task runs,
// and only when a release bundle or APK is in the graph: debug builds are unaffected.
gradle.taskGraph.whenReady(closureOf<TaskExecutionGraph> {
    val releaseTasks = allTasks.filter {
        it.project == project && (it.name == "assembleRelease" || it.name == "bundleRelease")
    }
    // Never sign a tester build with the debug key by accident: a silent fallback means the APK cannot be
    // updated in place and looks "broken" for no visible reason (T2.8).
    if (releaseTasks.isNotEmpty() && !releaseKeystoreFile.exists()) {
        throw GradleException(
            "release signing key not found (BTB_KEYSTORE_PATH=$releaseKeystorePath). " +
                "Run scripts/generate_release_keystore.sh, or pass -PbtbKeystorePath, " +
                "or build the debug APK instead.",
        )
    }
    if (releaseTasks.isNotEmpty() && !allowCleartextRelease &&
        !defaultIngestUrl.startsWith("wss://", ignoreCase = true)
    ) {
        throw GradleException(
            "Refusing to build the release variant: the default server URL is '$defaultIngestUrl', " +
                "but release builds must default to a wss:// server (the release app refuses ws:// and http://).\n" +
                "Fix: pass -PbtbDefaultIngestUrl=wss://<host>/ingest-events (or set BTB_DEFAULT_INGEST_URL).\n" +
                "UNSAFE, local testing only: -PbtbAllowCleartextRelease=true skips this check. The APK then ships a " +
                "cleartext default that the release app will refuse; never distribute it.",
        )
    }
})

dependencies {
    implementation(libs.litert)

    implementation(libs.camerax.core)
    implementation(libs.camerax.camera2)
    implementation(libs.camerax.lifecycle)
    implementation(libs.camerax.view)

    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)
    implementation(libs.zxing.core)

    testImplementation(libs.junit.jupiter)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.truth)
    testRuntimeOnly(libs.junit.platform.launcher)

    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.core)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.junit4)
}