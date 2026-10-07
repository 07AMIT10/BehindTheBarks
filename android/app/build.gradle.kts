import org.jetbrains.kotlin.gradle.dsl.JvmTarget

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

        val defaultIngestUrl = project.findProperty("btbDefaultIngestUrl") as? String
            ?: System.getenv("BTB_DEFAULT_INGEST_URL")
            ?: "ws://10.0.2.2:8000/ingest-events"
        buildConfigField("String", "DEFAULT_INGEST_URL", "\"$defaultIngestUrl\"")
    }

    signingConfigs {
        create("release") {
            val keystorePath = System.getenv("BTB_KEYSTORE_PATH")
                ?: project.findProperty("btbKeystorePath") as? String
                ?: "release.jks"
            val keystoreFile = file(keystorePath).let { if (it.isAbsolute) it else rootProject.file(keystorePath) }
            if (keystoreFile.exists()) {
                storeFile = keystoreFile
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
            signingConfig = if (releaseSigning?.storeFile?.exists() == true) {
                releaseSigning
            } else {
                signingConfigs.getByName("debug")
            }
        }
        debug {
            isMinifyEnabled = false
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
}