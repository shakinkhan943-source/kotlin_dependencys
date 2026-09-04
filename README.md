# kotlin_dependencys

Generates the built-in Jetpack Compose dependency bundle for
[Sketchware-Pro](https://github.com/shakinkhan943-source/Sketchware-Pro):
resolves the essential Compose Maven artifacts (not a huge guessed list —
just the roots each feature needs; Gradle resolves the rest transitively),
dexes each artifact **separately** (never merged into one blob), and
packages the result as a single zip.

## Run it

**GitHub UI:** Actions tab -> "Build Compose dependency bundle" -> Run workflow.

**gh CLI:**
```bash
gh workflow run build-bundle.yml -R shakinkhan943-source/kotlin_dependencys
```

## Get the output

Either:
- Actions tab -> the run -> "Artifacts" section -> download `compose-bundle.zip`
  (contains `compose-libs.zip` + `compose-libraries.json`), or
- the latest GitHub Release on this repo (updated every run), or via CLI:
  ```bash
  gh release download --pattern '*' -R shakinkhan943-source/kotlin_dependencys
  ```

## Where this goes in Sketchware-Pro

Copy the zip into:
```
app/src/main/assets/libs/compose-libs.zip
```

## What's inside compose-libs.zip

Each Maven artifact gets its own top-level folder named by its artifact id
(`<group>_<name>`, with `.` and `-` replaced by `_`):

```
<artifact-id>/classes.jar         # compile-time jar for that artifact
<artifact-id>/classes.dex         # pre-dexed runtime bytecode (classes2.dex, ... if multi-dex)
<artifact-id>/res/...             # extracted AAR resources (resource merger / aapt2)
<artifact-id>/assets/...          # extracted AAR assets (packaged into the APK)
<artifact-id>/AndroidManifest.xml # AAR manifest (manifest merger; only if the AAR ships one)
<artifact-id>/proguard.txt        # consumer ProGuard/R8 rules (only if the AAR ships one)
```

Plain `.jar` artifacts (no Android bits) only contain `classes.jar` + `classes.dex`.

Example:
```
androidx_compose_runtime_runtime/classes.jar
androidx_compose_runtime_runtime/classes.dex
androidx_compose_ui_ui/classes.jar
androidx_compose_ui_ui/classes.dex
androidx_compose_ui_ui/AndroidManifest.xml
androidx_compose_ui_ui/res/values/values.xml
...
```

## Toolchain / versions

| Piece | Version | Why |
| --- | --- | --- |
| Gradle | **9.6.0** | Minimum *and* default Gradle for AGP 9.4 |
| Android Gradle plugin | **9.4.0** | Max API level 37, SDK build-tools 36.0.0, JDK 17 |
| JDK | 17 | AGP 9.4 minimum/default |
| Kotlin | **2.4.10** | Same `kotlin-stdlib` the host app (Sketchware-Pro) ships |
| kotlinx-coroutines | **1.11.0** | Newest stable, built against Kotlin 2.x |
| Compose BOM | **2026.06.01** | compose ui/runtime/foundation 1.11.4, material3 1.4.0, material-icons 1.7.8 |
| activity-compose | 1.13.0 | needs Compose >= 1.7.0 |
| navigation-compose | 2.10.0 | needs Compose >= 1.10.5 |
| lifecycle-*-compose | 2.11.0 | needs Compose >= 1.11.0 |
| compileSdk / minSdk | 36 / 26 | |

Two things keep the graph mismatch-free:

1. **A BOM per ecosystem** (`compose-bom`, `kotlin-bom`, `kotlinx-coroutines-bom`)
   so the androidx.compose.* artifacts can never drift apart from each other.
2. **`resolutionStrategy.force(...)`** on `kotlin-stdlib` / `kotlin-reflect` /
   `kotlinx-coroutines-*`, so nothing transitively drags in a Kotlin runtime
   newer than the 2.4.10 the app actually has on device.
   `scripts/build_bundle.py` re-checks the resolved versions and **fails the
   build** if anything slipped through.

AGP 9 has built-in Kotlin, so the `org.jetbrains.kotlin.android` plugin is gone
(applying it on AGP 9 is a hard error). The Kotlin version is pinned by putting
KGP 2.4.10 on the root buildscript classpath *without applying it* — the
documented way to override built-in Kotlin's version.

### What the app must provide itself

`kotlin-stdlib`, `kotlin-reflect` and `kotlinx-coroutines-*` are **deliberately
not packaged** into `compose-libs.zip` (Sketchware-Pro already ships them —
shipping a second copy is exactly what causes duplicate-class / `NoSuchMethodError`
mismatches). The versions the bundle was resolved against are recorded in
`output/compose-libraries.json` under `hostProvided`; the app has to ship those
versions or newer:

```
org.jetbrains.kotlin:kotlin-stdlib:2.4.10
org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0
org.jetbrains.kotlinx:kotlinx-coroutines-core:1.11.0
```

## Configuring versions

Run the workflow manually (Actions -> "Build Compose dependency bundle" -> Run
workflow) and set `compose_bom_version`, `kotlin_version`, `coroutines_version`,
`agp_version`, `gradle_version` or `compile_sdk`. Locally the same knobs are
plain env vars:

```bash
COMPOSE_BOM_VERSION=2026.06.01 KOTLIN_VERSION=2.4.10 COROUTINES_VERSION=1.11.0 \
AGP_VERSION=9.4.0 ANDROID_COMPILE_SDK_LEVEL=36 python3 scripts/build_bundle.py
```

> **Bumping to Compose 1.12 (BOM `2026.08.00`+):** those artifacts require
> `compileSdk 37` and AGP >= 9.2, so also pass `ANDROID_COMPILE_SDK_LEVEL=37`
> (`compile_sdk: 37` in the workflow) and make sure the consuming app compiles
> against API 37 as well. Compose 1.11.4 is the newest release that still works
> with a compileSdk-36 pipeline, which is why it's the default here.

