plugins {
    id("com.android.library")
}

android {
    namespace = "com.aistudio.flux.composecatalog"
    compileSdk = (System.getenv("ANDROID_COMPILE_SDK_LEVEL") ?: "36").toInt()

    defaultConfig {
        minSdk = 26
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}

val composeUi = System.getenv("COMPOSE_UI_VERSION") ?: "1.7.8"
val composeMaterial3 = System.getenv("COMPOSE_MATERIAL3_VERSION") ?: "1.3.1"
val activityCompose = System.getenv("ACTIVITY_COMPOSE_VERSION") ?: "1.9.3"
val navigationCompose = System.getenv("NAVIGATION_COMPOSE_VERSION") ?: "2.8.5"
val lifecycleCompose = System.getenv("LIFECYCLE_COMPOSE_VERSION") ?: "2.8.7"

// One implementation(...) line per FEATURES root in scripts/build_bundle.py.
// KEEP THESE TWO FILES IN SYNC. This module is the single source of truth for
// WHAT gets resolved (a real AGP runtime classpath); the Python script then
// packages each resolved artifact into its own <id>/ folder with classes.jar,
// classes.dex and (if present) res/.
dependencies {
    // --- core (IMPORTANT) ---
    implementation("androidx.compose.runtime:runtime:$composeUi")
    implementation("androidx.compose.runtime:runtime-saveable:$composeUi")
    implementation("androidx.compose.ui:ui:$composeUi")
    implementation("androidx.compose.foundation:foundation:$composeUi")

    // --- material3 (IMPORTANT) ---
    implementation("androidx.compose.material3:material3:$composeMaterial3")

    // --- activity-compose (IMPORTANT) ---
    implementation("androidx.activity:activity-compose:$activityCompose")

    // --- ui-tooling-preview (IMPORTANT) ---
    implementation("androidx.compose.ui:ui-tooling-preview:$composeUi")

    // --- animation (OPTIONAL) ---
    implementation("androidx.compose.animation:animation:$composeUi")

    // --- material-icons (OPTIONAL) ---
    implementation("androidx.compose.material:material-icons-extended:$composeUi")

    // --- navigation-compose (OPTIONAL) ---
    implementation("androidx.navigation:navigation-compose:$navigationCompose")

    // --- lifecycle-compose (OPTIONAL) ---
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:$lifecycleCompose")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:$lifecycleCompose")
}

// Dumps the fully resolved RELEASE runtime classpath -- the exact classpath a
// real app would ship -- to build/resolved.json, as a list of {file, module}
// entries. scripts/build_bundle.py then stages each artifact into its own
// <id>/ folder (classes.jar, classes.dex, res/) and zips the result.
tasks.register("dumpArtifacts") {
    doLast {
        val runtimeClasspath = configurations.getByName("releaseRuntimeClasspath")
        val resolvedConfiguration = runtimeClasspath.resolvedConfiguration
        val artifacts = resolvedConfiguration.resolvedArtifacts

        fun jsonString(value: String): String =
            "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

        val json = StringBuilder("[\n")
        val list = artifacts.toList()
        list.forEachIndexed { index, artifact ->
            val id = artifact.moduleVersion.id
            val module = "${id.group}:${id.name}:${id.version}"
            val file = artifact.file.absolutePath
            json.append("  {\n")
            json.append("    \"file\": ${jsonString(file)},\n")
            json.append("    \"module\": ${jsonString(module)}\n")
            json.append(if (index == list.size - 1) "  }\n" else "  },\n")
        }
        json.append("]\n")

        val outFile = layout.buildDirectory.file("resolved.json").get().asFile
        outFile.parentFile.mkdirs()
        outFile.writeText(json.toString())
        println("Wrote ${list.size} resolved artifacts to $outFile")
    }
}
