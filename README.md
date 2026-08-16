# kotlin_dependencys

Generates the built-in Jetpack Compose dependency bundle for
[Sketchware-Pro](https://github.com/shakinkhan943-source/Sketchware-Pro):
resolves the essential Compose Maven artifacts (not a huge guessed list —
just the roots each feature needs; Gradle resolves the rest transitively),
dexes each artifact **separately** (never merged into one blob), and
packages the result as two files.

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

## Where these go in Sketchware-Pro

Copy both files into:
```
app/src/main/assets/libs/compose-libs.zip
app/src/main/assets/libs/compose-libraries.json
```

## What's inside compose-libs.zip

```
classes/<artifact-id>.jar   # one jar per Maven artifact, never merged
dex/<artifact-id>.dex       # that artifact's separately-dexed classes
res/<artifact-id>/...       # extracted AAR resources, if the artifact has any
```

## Configuring versions

Trigger the workflow manually and set `compose_ui_version` /
`material3_version` inputs, or edit the defaults directly in
`scripts/build_bundle.py`.
