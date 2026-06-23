plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

val gitCommit: String = runCatching {
    providers.exec { commandLine("git", "rev-parse", "--short", "HEAD") }
        .standardOutput.asText.get().trim()
}.getOrDefault("unknown")

android {
    namespace = "com.lordmuffin.jarvisvoice"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.lordmuffin.jarvisvoice"
        minSdk = 26
        targetSdk = 34
        versionCode = 57
        versionName = "1.1.55"
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
    buildFeatures {
        buildConfig = true
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
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.work.runtime.ktx)
    implementation(libs.litertlm.android)
    implementation(libs.androidx.documentfile)
    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)
    implementation(libs.lifecycle.viewmodel.ktx)
    implementation(libs.mlkit.genai.prompt)
}
