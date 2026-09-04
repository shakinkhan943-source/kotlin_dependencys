// Top-level build file.
//
// `compose-catalog` is a real (never shipped) Android library module whose
// only job is to let AGP resolve the Jetpack Compose dependency graph the
// same way a real app's does. That gets us correct Kotlin Multiplatform
// variant selection (androidJvm) for free -- no more hand-rolled Gradle
// configuration + manual attribute hacking, which was silently dropping
// artifacts (ui-text-android, ui-graphics-android, lifecycle-runtime-compose-android,
// etc. were all resolving to jvmstubs/desktop variants and getting filtered out).
//
// Toolchain (all overridable through env vars, see README):
//   Gradle 9.6.0   -- minimum *and* default Gradle for AGP 9.4 (JDK 17).
//   AGP    9.4.0   -- max supported API level 37, SDK build-tools 36.0.0.
//   Kotlin 2.4.10  -- matches the kotlin-stdlib the host app (Sketchware-Pro)
//                     ships, so nothing in the generated bundle is compiled
//                     against a newer stdlib/metadata than the runtime has.

buildscript {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
    dependencies {
        // AGP 9 has *built-in* Kotlin support, so `org.jetbrains.kotlin.android`
        // must no longer be applied (applying it fails with
        // "The 'org.jetbrains.kotlin.android' plugin is no longer required for
        // Kotlin support since AGP 9.0").
        // The documented way to make the build use a specific Kotlin version is
        // to put that Kotlin Gradle plugin on the build classpath *without*
        // applying it -- built-in Kotlin then picks it up.
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:${System.getenv("KOTLIN_VERSION") ?: "2.4.10"}")
    }
}

plugins {
    id("com.android.library") version (System.getenv("AGP_VERSION") ?: "9.4.0") apply false
}
