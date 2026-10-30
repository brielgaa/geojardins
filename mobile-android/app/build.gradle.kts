plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

val buildPythonPath = providers.gradleProperty("buildPython").orElse("python").get()
val stageCore by tasks.registering(Exec::class) {
    workingDir(rootDir)
    commandLine(buildPythonPath, "tools/stage_core.py")
    inputs.dir(rootDir.resolve("../src/geojardins"))
    inputs.dir(rootDir.resolve("../data/processed"))
    inputs.dir(rootDir.resolve("../data/config"))
    inputs.dir(rootDir.resolve("../config"))
    inputs.file(rootDir.resolve("tools/stage_core.py"))
    inputs.file(rootDir.resolve("platform_runtime.py"))
    outputs.dir(layout.buildDirectory.dir("generated/core"))
}

android {
    namespace = "br.com.geojardins.mobile"
    compileSdk = 35
    defaultConfig {
        applicationId = "br.com.geojardins.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.2.0-android.1"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    signingConfigs {
        create("distribution") {
            val path = System.getenv("GEOJARDINS_SIGNING_STORE")
            if (!path.isNullOrBlank()) {
                storeFile = file(path)
                storePassword = System.getenv("GEOJARDINS_SIGNING_STORE_PASSWORD")
                keyAlias = System.getenv("GEOJARDINS_SIGNING_ALIAS")
                keyPassword = System.getenv("GEOJARDINS_SIGNING_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        debug { applicationIdSuffix = ".debug"; versionNameSuffix = "-debug" }
        release {
            isMinifyEnabled = false
            if (!System.getenv("GEOJARDINS_SIGNING_STORE").isNullOrBlank()) {
                signingConfig = signingConfigs.getByName("distribution")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true; buildConfig = true }
    sourceSets.getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/core/assets"))
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}

chaquopy {
    defaultConfig {
        version = "3.10"
        buildPython(buildPythonPath)
        pip {
            install("shapely==1.8.5")
            install("pyproj==3.4.1")
            install("openpyxl==3.1.5")
        }
    }
    sourceSets.getByName("main") {
        srcDir(layout.buildDirectory.dir("generated/core/python"))
    }
}
tasks.configureEach {
    if (name == "preBuild" || name.contains("PythonSources") || name.endsWith("Assets")) dependsOn(stageCore)
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.04.01"))
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation(platform("androidx.compose:compose-bom:2025.04.01"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
