plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// Versions follow the phone app (release-please bumps android/version.txt for both).
val appVersion: String = providers
    .fileContents(rootProject.layout.projectDirectory.file("version.txt"))
    .asText.get().trim()
val appVersionCode: Int = appVersion.split(".").map(String::toInt)
    .let { (major, minor, patch) -> major * 10_000 + minor * 100 + patch }

android {
    namespace = "com.lordmuffin.jarvisvoice.wear"
    compileSdk = 34

    defaultConfig {
        // Same id as the phone app on purpose: the Wearable Data Layer routes messages, channels
        // and data items between the watch and phone apps that share a package name and signature.
        applicationId = "com.lordmuffin.jarvisvoice"
        minSdk = 26
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersion
    }

    signingConfigs {
        create("release") {
            // The release job decodes the keystore into android/app/, next to the phone app.
            storeFile     = System.getenv("KEYSTORE_PATH")?.let { project(":app").file(it) }
            storePassword = System.getenv("KEYSTORE_PASSWORD")
            keyAlias      = System.getenv("KEY_ALIAS")
            keyPassword   = System.getenv("KEYSTORE_PASSWORD")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Must be the phone app's key too, or the Data Layer will not connect the two.
            signingConfig = if (System.getenv("KEYSTORE_PATH") != null) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }

    sourceSets["main"].res.srcDir("src/instrument/android/res")

    testOptions {
        unitTests.isReturnDefaultValues = true
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
    implementation(project(":wearcommon"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.wear)
    implementation(libs.androidx.wear.input)
    implementation(libs.androidx.work.runtime.ktx)
    implementation(libs.lifecycle.runtime.ktx)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.coroutines.play.services)
    implementation(libs.play.services.wearable)
    implementation(libs.androidx.wear.tiles)
    implementation(libs.androidx.wear.protolayout)
    implementation(libs.androidx.wear.protolayout.expression)
    implementation(libs.androidx.wear.complications.datasource)
    implementation(libs.guava)

    testImplementation(libs.junit)
    testImplementation(libs.org.json)
}
