plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.paparazzi)
}

val gitCommit: String = runCatching {
    providers.exec { commandLine("git", "rev-parse", "--short", "HEAD") }
        .standardOutput.asText.get().trim()
}.getOrDefault("unknown")

// release-please bumps android/version.txt on every Android release; the APK's
// versionName and versionCode follow it so the installed app reports its release.
val appVersion: String = providers
    .fileContents(rootProject.layout.projectDirectory.file("version.txt"))
    .asText.get().trim()
val appVersionCode: Int = appVersion.split(".").map(String::toInt)
    .let { (major, minor, patch) -> major * 10_000 + minor * 100 + patch }

android {
    namespace = "com.lordmuffin.jarvisvoice"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.lordmuffin.jarvisvoice"
        minSdk = 26
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersion
        buildConfigField("String", "GIT_COMMIT", "\"$gitCommit\"")
    }

    signingConfigs {
        create("release") {
            storeFile     = System.getenv("KEYSTORE_PATH")?.let { file(it) }
            storePassword = System.getenv("KEYSTORE_PASSWORD")
            keyAlias      = System.getenv("KEY_ALIAS")
            // PKCS12 keystores (Java 9+ default) have no separate key password —
            // the key is protected by the store password. Use KEYSTORE_PASSWORD for both.
            keyPassword   = System.getenv("KEYSTORE_PASSWORD")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = if (System.getenv("KEYSTORE_PATH") != null) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }
    // Instrument design-system resources, vendored (managed, never edit) by the Instrument sync PR.
    sourceSets["main"].res.srcDir("src/instrument/android/res")
    sourceSets["main"].kotlin.srcDir("src/instrument/android/compose")

    buildFeatures {
        buildConfig = true
        compose = true
    }
    testOptions {
        // android.jar stubs (Log, etc.) return defaults instead of throwing in JVM unit tests.
        unitTests.isReturnDefaultValues = true
    }
    // Don't attempt to compress ONNX model files — they're already binary
    // and compression wastes build memory on large model assets.
    androidResources {
        noCompress += listOf("onnx", "bin")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlin {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }
}

dependencies {
    implementation(libs.commons.compress)
    implementation(libs.androidx.recyclerview)
    implementation(libs.androidx.browser)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.work.runtime.ktx)
    implementation(libs.litertlm.android)
    implementation(libs.androidx.documentfile)
    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)
    implementation(libs.lifecycle.viewmodel.ktx)
    implementation(libs.lifecycle.common.java8)
    implementation(libs.mlkit.genai.prompt)
    implementation(libs.androidx.car.app)
    implementation(libs.androidx.media3.common)
    implementation(libs.kotlinx.coroutines.android)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.activity.compose)
    debugImplementation(libs.androidx.compose.ui.tooling)

    testImplementation(libs.junit)
    // Android's org.json is a stub in JVM tests; use the real one.
    testImplementation(libs.org.json)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
}
