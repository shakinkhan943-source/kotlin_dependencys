import org.gradle.api.artifacts.component.ModuleComponentIdentifier

plugins {
    // NOTE: no `org.jetbrains.kotlin.android` here -- AGP 9 has built-in Kotlin
    // and applying KGP on top of it is a hard error. The Kotlin version used by
    // the build is pinned from the root build file's buildscript classpath.
    id("com.android.library")
}

// ---------------------------------------------------------------------------
// Version pins. Everything is env-overridable so CI can build a different set
// without touching the file, but the defaults are the coherent set:
//
//   Kotlin stdlib      2.4.10   <- what the host app (Sketchware-Pro) ships
//   Coroutines         1.11.0   <- newest stable, built against Kotlin 2.x
//   Compose BOM     2026.06.01  -> compose-ui/runtime/foundation 1.11.4,
//                                  material3 1.4.0, material-icons 1.7.8
//   activity-compose   1.13.0   (needs compose >= 1.7.0)
//   navigation-compose 2.10.0   (needs compose >= 1.10.5)
//   lifecycle          2.11.0   (needs compose >= 1.11.0)
//
// The BOM is what guarantees the androidx.compose.* artifacts can never drift
// apart from each other; the constraints below guarantee the Kotlin runtime the
// bundle is resolved against is exactly the one the app already has on device.
//
// Heads-up before bumping the BOM: Compose 1.12.x (BOM 2026.08.00+) requires
// compileSdk 37 and AGP >= 9.2. If you move to it, also set
// ANDROID_COMPILE_SDK_LEVEL=37 (and install platforms;android-37 in CI).
// ---------------------------------------------------------------------------
val kotlinVersion = System.getenv("KOTLIN_VERSION") ?: "2.4.10"
val coroutinesVersion = System.getenv("COROUTINES_VERSION") ?: "1.11.0"
val composeBomVersion = System.getenv("COMPOSE_BOM_VERSION") ?: "2026.06.01"
val activityComposeVersion = System.getenv("ACTIVITY_COMPOSE_VERSION") ?: "1.13.0"
val navigationComposeVersion = System.getenv("NAVIGATION_COMPOSE_VERSION") ?: "2.10.0"
val lifecycleVersion = System.getenv("LIFECYCLE_COMPOSE_VERSION") ?: "2.11.0"

android {
    namespace = "com.aistudio.flux.composecatalog"
    compileSdk = (System.getenv("ANDROID_COMPILE_SDK_LEVEL") ?: "36").toInt()

    defaultConfig {
        minSdk = (System.getenv("ANDROID_MIN_SDK_LEVEL") ?: "26").toInt()
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}

// Hard-pin the Kotlin runtime. kotlin-stdlib / kotlinx-coroutines are *not*
// packaged into compose-libs.zip (the host app already ships them), so the
// graph must be resolved against exactly the versions the app provides --
// otherwise an artifact could be selected that expects a newer stdlib than the
// device runtime has. force() also protects against a transitive dependency
// dragging in a newer stdlib than 2.4.10.
configurations.configureEach {
    resolutionStrategy {
        force(
            "org.jetbrains.kotlin:kotlin-stdlib:$kotlinVersion",
            "org.jetbrains.kotlin:kotlin-stdlib-jdk7:$kotlinVersion",
            "org.jetbrains.kotlin:kotlin-stdlib-jdk8:$kotlinVersion",
            "org.jetbrains.kotlin:kotlin-reflect:$kotlinVersion",
            "org.jetbrains.kotlinx:kotlinx-coroutines-core:$coroutinesVersion",
            "org.jetbrains.kotlinx:kotlinx-coroutines-core-jvm:$coroutinesVersion",
            "org.jetbrains.kotlinx:kotlinx-coroutines-android:$coroutinesVersion",
        )
    }
}

// One implementation(...) line per FEATURES root in scripts/build_bundle.py.
// KEEP THESE TWO FILES IN SYNC. This module is the single source of truth for
// WHAT gets resolved (a real AGP runtime classpath); the Python script then
// packages each resolved artifact into its own <id>/ folder with classes.jar,
// classes.dex and (if present) res/.
dependencies {
    // --- BOMs: single source of truth for the version of every module below ---
    implementation(platform("androidx.compose:compose-bom:$composeBomVersion"))
    implementation(platform("org.jetbrains.kotlin:kotlin-bom:$kotlinVersion"))
    implementation(platform("org.jetbrains.kotlinx:kotlinx-coroutines-bom:$coroutinesVersion"))

    // --- kotlin runtime (NOT bundled; the host app provides it) ---
    implementation("org.jetbrains.kotlin:kotlin-stdlib")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android")

    // --- core (IMPORTANT) ---
    implementation("androidx.compose.runtime:runtime")
    implementation("androidx.compose.runtime:runtime-saveable")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")

    // --- material3 (IMPORTANT) ---
    implementation("androidx.compose.material3:material3")

    // --- activity-compose (IMPORTANT) ---
    implementation("androidx.activity:activity-compose:$activityComposeVersion")

    // --- ui-tooling-preview (IMPORTANT) ---
    implementation("androidx.compose.ui:ui-tooling-preview")

    // --- animation (OPTIONAL) ---
    implementation("androidx.compose.animation:animation")

    // --- material-icons (OPTIONAL) ---
    // Frozen upstream at 1.7.8; the BOM pins it there on purpose, so never give
    // this one an explicit Compose-UI version.
    implementation("androidx.compose.material:material-icons-extended")

    // --- navigation-compose (OPTIONAL) ---
    implementation("androidx.navigation:navigation-compose:$navigationComposeVersion")

    // --- lifecycle-compose (OPTIONAL) ---
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:$lifecycleVersion")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:$lifecycleVersion")
}

// Dumps the fully resolved RELEASE runtime classpath -- the exact classpath a
// real app would ship -- to build/resolved.json, as a list of {file, module}
// entries. scripts/build_bundle.py then stages each artifact into its own
// <id>/ folder (classes.jar, classes.dex, res/) and zips the result.
//
// Uses the AGP variant API + Gradle's ArtifactCollection (the old
// `configurations.getByName(...).resolvedConfiguration` API is deprecated and
// incompatible with Gradle 9's configuration cache).
androidComponents {
    onVariants(selector().withBuildType("release")) { variant ->
        val resolvedArtifacts = variant.runtimeConfiguration.incoming.artifacts.resolvedArtifacts
        val outputFile = layout.buildDirectory.file("resolved.json")

        tasks.register("dumpArtifacts") {
            group = "build"
            description = "Writes the resolved release runtime classpath to build/resolved.json"
            outputs.file(outputFile)
            outputs.upToDateWhen { false }

            doLast {
                fun jsonString(value: String): String =
                    "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

                val entries = resolvedArtifacts.get()
                    .mapNotNull { artifact ->
                        val id = artifact.id.componentIdentifier
                        if (id is ModuleComponentIdentifier) {
                            "${id.group}:${id.module}:${id.version}" to artifact.file.absolutePath
                        } else {
                            null
                        }
                    }
                    .sortedBy { it.first }

                val json = StringBuilder("[\n")
                entries.forEachIndexed { index, (module, file) ->
                    json.append("  {\n")
                    json.append("    \"file\": ${jsonString(file)},\n")
                    json.append("    \"module\": ${jsonString(module)}\n")
                    json.append(if (index == entries.size - 1) "  }\n" else "  },\n")
                }
                json.append("]\n")

                val outFile = outputFile.get().asFile
                outFile.parentFile.mkdirs()
                outFile.writeText(json.toString())
                println("Wrote ${entries.size} resolved artifacts to $outFile")
            }
        }
    }
}
