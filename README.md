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
  (contains `compose-libs.zip`), or
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

## Configuring versions

Trigger the workflow manually and set `compose_ui_version` /
`material3_version` inputs, or edit the defaults directly in
`scripts/build_bundle.py`.
