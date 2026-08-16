#!/usr/bin/env python3
"""Build an Android-only Jetpack Compose dependency bundle.

Compose roots use concrete Android-published artifacts. Gradle resolves their
normal Android/JVM transitive graph; the consumer deliberately does not force
Kotlin's androidJvm platform attribute because many valid AndroidX/JVM
libraries publish ordinary JVM/runtime variants.
"""
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
WORK = ROOT / "build" / "bundle"

COMPOSE_UI = os.environ.get("COMPOSE_UI_VERSION", "1.7.8")
COMPOSE_MATERIAL3 = os.environ.get("COMPOSE_MATERIAL3_VERSION", "1.3.1")
ACTIVITY_COMPOSE = os.environ.get("ACTIVITY_COMPOSE_VERSION", "1.9.3")
NAVIGATION_COMPOSE = os.environ.get("NAVIGATION_COMPOSE_VERSION", "2.8.5")
LIFECYCLE_COMPOSE = os.environ.get("LIFECYCLE_COMPOSE_VERSION", "2.8.7")
ANDROID_PLATFORM = os.environ.get("ANDROID_COMPILE_SDK", "android-36")

FEATURES = {
    "core": {"name": "Compose Core", "description": "Required Compose runtime, UI and foundation APIs.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.compose.runtime:runtime-android:{COMPOSE_UI}", f"androidx.compose.ui:ui-android:{COMPOSE_UI}", f"androidx.compose.foundation:foundation-android:{COMPOSE_UI}"]},
    "material3": {"name": "Material 3", "description": "Material 3 components and theming for Compose.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.compose.material3:material3-android:{COMPOSE_MATERIAL3}"]},
    "activity-compose": {"name": "Activity Compose", "description": "Integrates Compose content with Android activities.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.activity:activity-compose:{ACTIVITY_COMPOSE}"]},
    "animation": {"name": "Compose Animation", "description": "Animation APIs beyond the core foundation set.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.compose.animation:animation-android:{COMPOSE_UI}"]},
    "material-icons": {"name": "Material Icons Extended", "description": "The full Material icon set for Compose.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.compose.material:material-icons-extended-android:{COMPOSE_UI}"]},
    "navigation-compose": {"name": "Navigation Compose", "description": "Navigate between composables with a NavHost/NavController.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.navigation:navigation-compose:{NAVIGATION_COMPOSE}"]},
    "lifecycle-compose": {"name": "Lifecycle ViewModel Compose", "description": "ViewModel + lifecycle-aware state collection for Compose.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.lifecycle:lifecycle-viewmodel-compose:{LIFECYCLE_COMPOSE}"]},
}

UNSUPPORTED_SUFFIXES = ("-desktop", "-windows", "-linux", "-macos", "-macosx", "-ios", "-tvos", "-watchos", "-wasm", "-js", "-mingw", "-swing", "-awt")
UNSUPPORTED_GROUPS = {"org.jetbrains.compose.desktop"}


def is_android_artifact(coordinate):
    parts = coordinate.split(":", 2)
    if len(parts) != 3:
        return False
    group, name, _version = parts
    return group.lower() not in UNSUPPORTED_GROUPS and not name.lower().endswith(UNSUPPORTED_SUFFIXES)


def run(*args):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)

    configurations = []
    dependency_lines = []
    for fid, feature in FEATURES.items():
        cname = "compose_" + fid.replace("-", "_")
        configurations.append((fid, cname))
        dependency_lines += [
            f"configurations.maybeCreate('{cname}')",
            f"configurations.getByName('{cname}').canBeResolved = true",
            f"configurations.getByName('{cname}').canBeConsumed = false",
            f"configurations.getByName('{cname}').attributes {{",
            "    attribute(org.gradle.api.attributes.Category.CATEGORY_ATTRIBUTE, objects.named(org.gradle.api.attributes.Category, org.gradle.api.attributes.Category.LIBRARY))",
            "    attribute(org.gradle.api.attributes.Usage.USAGE_ATTRIBUTE, objects.named(org.gradle.api.attributes.Usage, org.gradle.api.attributes.Usage.JAVA_RUNTIME))",
            "}",
        ]
        for coord in feature["roots"]:
            dependency_lines.append(f"dependencies.add('{cname}', '{coord}')")

    resolved_json = WORK / "resolved.json"
    collect_lines = [f"result['{fid}'] = configurations.getByName('{cname}').resolvedConfiguration.resolvedArtifacts.collect {{ a -> [file: a.file.absolutePath, module: a.moduleVersion.id.group + ':' + a.name + ':' + a.moduleVersion.id.version] }}" for fid, cname in configurations]
    groovy = """plugins { id 'base' }

repositories {
    google()
    mavenCentral()
}

""" + "\n".join(dependency_lines) + """

tasks.register('dumpArtifacts') {
    doLast {
        def result = [:]
""" + "\n".join("        " + line for line in collect_lines) + f"""
        file('{resolved_json.as_posix()}').text = groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(result))
    }}
}}
"""

    resolver_gradle = WORK / "resolver.gradle"
    resolver_gradle.write_text(groovy, encoding="utf-8")
    (WORK / "settings.gradle").write_text("rootProject.name = 'compose-bundle-resolver'\n", encoding="utf-8")
    run("gradle", "-q", "-b", resolver_gradle, "dumpArtifacts")
    resolved = json.loads(resolved_json.read_text(encoding="utf-8"))

    rejected = []
    for fid, entries in resolved.items():
        kept = []
        for entry in entries:
            if is_android_artifact(entry["module"]):
                kept.append(entry)
            else:
                rejected.append({"feature": fid, "coordinate": entry["module"]})
        resolved[fid] = kept

    sdk = Path(os.environ.get("ANDROID_SDK_ROOT", os.environ.get("ANDROID_HOME", "")))
    android_jar = Path(os.environ.get("ANDROID_JAR", "")) if os.environ.get("ANDROID_JAR") else sdk / "platforms" / ANDROID_PLATFORM / "android.jar"
    if not android_jar.is_file():
        raise RuntimeError(f"android.jar not found: {android_jar}")

    bundle_root = WORK / "bundle"
    classes_dir = bundle_root / "classes"
    dex_dir = bundle_root / "dex"
    classes_dir.mkdir(parents=True)
    dex_dir.mkdir(parents=True)

    artifact_by_file = {}
    file_meta = {}
    feature_files = {}
    for fid, entries in resolved.items():
        feature_files[fid] = []
        for entry in entries:
            f = entry["file"]
            feature_files[fid].append(f)
            file_meta[f] = entry["module"]
            if f not in artifact_by_file:
                group, name, _version = entry["module"].split(":", 2)
                artifact_by_file[f] = f"{group}_{name}".replace(".", "_")

    d8 = shutil.which("d8")
    if not d8:
        candidates = sorted(sdk.glob("build-tools/*/d8"))
        if not candidates:
            raise RuntimeError("d8 executable not found")
        d8 = str(candidates[-1])

    total_input_bytes = 0
    for source, aid in artifact_by_file.items():
        src = Path(source)
        classes_jar = classes_dir / f"{aid}.jar"
        if src.suffix == ".aar":
            with zipfile.ZipFile(src) as zf:
                if "classes.jar" not in zf.namelist():
                    continue
                classes_jar.write_bytes(zf.read("classes.jar"))
        else:
            shutil.copy2(src, classes_jar)
        if not classes_jar.exists() or classes_jar.stat().st_size == 0:
            continue
        total_input_bytes += classes_jar.stat().st_size
        tmp = WORK / "dex-tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        run(d8, "--min-api", "23", "--lib", android_jar, "--output", tmp, classes_jar)
        dex_files = sorted(tmp.glob("classes*.dex"))
        if len(dex_files) != 1:
            raise RuntimeError(f"Expected one dex from {src.name}, got {len(dex_files)}")
        shutil.move(dex_files[0], dex_dir / f"{aid}.dex")

    artifacts = []
    for source, aid in sorted(artifact_by_file.items(), key=lambda item: item[1]):
        module = file_meta[source]
        group, _name, _version = module.split(":", 2)
        artifacts.append({"id": aid, "coordinate": module, "packageName": group, "dependencies": []})

    for fid, feature in FEATURES.items():
        feature["artifacts"] = [artifact_by_file[f] for f in sorted(feature_files[fid]) if f in artifact_by_file]

    manifest = {"schemaVersion": 1, "composeVersion": COMPOSE_UI, "features": [{"id": fid, **{k: v for k, v in feature.items() if k != "roots"}} for fid, feature in FEATURES.items()], "artifacts": artifacts}
    (OUT / "compose-libraries.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    report = {"androidOnly": True, "resolutionStrategy": "explicit-android-artifacts", "composeVersion": COMPOSE_UI, "material3Version": COMPOSE_MATERIAL3, "artifactCount": len(artifacts), "rejectedArtifactCount": len(rejected), "rejectedArtifacts": rejected, "d8InputBytes": total_input_bytes}
    (OUT / "resolution-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    archive = OUT / "compose-libs.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in bundle_root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_root))

    print(f"Wrote {archive}")
    print(f"Android artifacts: {len(artifacts)} | rejected: {len(rejected)} | D8 input: {total_input_bytes / (1024 * 1024):.1f} MiB")


if __name__ == "__main__":
    main()
