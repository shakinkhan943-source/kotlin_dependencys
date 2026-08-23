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
// AGP_VERSION / KOTLIN_VERSION default to what the reference app
// (Flux/currencypay) actually builds with -- override via env if your CI
// needs a different pin.
plugins {
  id("com.android.library") version (System.getenv("AGP_VERSION") ?: "9.1.1") apply false
  id("org.jetbrains.kotlin.android") version (System.getenv("KOTLIN_VERSION") ?: "2.2.10") apply false
}
