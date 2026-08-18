#!/usr/bin/env python3
"""Build a curated Android-only Jetpack Compose dependency bundle."""
import json
import os
import shutil
import subprocess
import tempfile
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

# Curated roots. Gradle resolves their real Android/JVM support graph, but the
# final manifest exposes only these user-selectable features. Kotlin/coroutines
# are deliberately not roots because Sketchware Pro already provides them.
FEATURES = {
    "core": {"name": "Compose Core", "description": "Required Compose runtime, UI and foundation APIs.", "required": True, "tag": "IMPORTANT", "roots": [
        f"androidx.compose.runtime:runtime-android:{COMPOSE_UI}",
        f"androidx.compose.runtime:runtime-saveable-android:{COMPOSE_UI}",
        f"androidx.compose.ui:ui-android:{COMPOSE_UI}",
        f"androidx.compose.foundation:foundation-android:{COMPOSE_UI}",
    ]},
    "material3": {"name": "Material 3", "description": "Material 3 components and theming for Compose.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.compose.material3:material3-android:{COMPOSE_MATERIAL3}"]},
    "activity-compose": {"name": "Activity Compose", "description": "Integrates Compose content with Android activities.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.activity:activity-compose:{ACTIVITY_COMPOSE}"]},
    "animation": {"name": "Compose Animation", "description": "Animation APIs beyond the core foundation set.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.compose.animation:animation-android:{COMPOSE_UI}"]},
    "material-icons": {"name": "Material Icons Extended", "description": "The full Material icon set for Compose.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.compose.material:material-icons-extended-android:{COMPOSE_UI}"]},
    "navigation-compose": {"name": "Navigation Compose", "description": "Navigate between composables with a NavHost/NavController.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.navigation:navigation-compose:{NAVIGATION_COMPOSE}"]},
    "lifecycle-compose": {"name": "Lifecycle ViewModel Compose", "description": "ViewModel + lifecycle-aware state collection for Compose.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.lifecycle:lifecycle-viewmodel-compose:{LIFECYCLE_COMPOSE}"]},
}

# Libraries already supplied by Sketchware Pro. They may still appear as
# Gradle transitives, but they are not copied into the built-in bundle.
SKIP_COORDINATE_PREFIXES = (
    "org.jetbrains.kotlin:kotlin-stdlib",
    "org.jetbrains.kotlin:kotlin-stdlib-common",
    "org.jetbrains.kotlin:kotlin-stdlib-jdk7",
    "org.jetbrains.kotlin:kotlin-stdlib-jdk8",
    "org.jetbrains.kotlinx:kotlinx-coroutines-core",
    "org.jetbrains.kotlinx:kotlinx-coroutines-core-jvm",
    "org.jetbrains.kotlinx:kotlinx-coroutines-android",
)
PLATFORM_TOKENS = ("-desktop", "-jvmstubs", "-jvm-stubs", "-ios", "-wasm", "-js", "-linux", "-macos", "-swing", "-awt")


def run(*args):
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def excluded(coord):
    c = coord.lower()
    return c.startswith(SKIP_COORDINATE_PREFIXES) or any(token in c for token in PLATFORM_TOKENS) or "skiko" in c


def main():
    if WORK.exists(): shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)
    configurations, dependency_lines = [], []
    for fid, feature in FEATURES.items():
        cname = "compose_" + fid.replace("-", "_")
        configurations.append((fid, cname))
        dependency_lines.append(f"def {cname} = configurations.maybeCreate('{cname}')")
        dependency_lines.append(f"{cname}.attributes {{ attribute(org.gradle.api.attributes.Usage.USAGE_ATTRIBUTE, objects.named(org.gradle.api.attributes.Usage, org.gradle.api.attributes.Usage.JAVA_RUNTIME)); attribute(org.gradle.api.attributes.Category.CATEGORY_ATTRIBUTE, objects.named(org.gradle.api.attributes.Category, org.gradle.api.attributes.Category.LIBRARY)) }}")
        for coord in feature["roots"]: dependency_lines.append(f"dependencies.add('{cname}', '{coord}')")
    resolved_json = WORK / "resolved.json"
    dump_lines = [f"result['{fid}'] = configurations.getByName('{cname}').resolvedConfiguration.resolvedArtifacts.collect {{ a -> [file: a.file.absolutePath, module: a.moduleVersion.id.group + ':' + a.name + ':' + a.moduleVersion.id.version] }}" for fid, cname in configurations]
    groovy = f'''repositories {{ google(); mavenCentral() }}
{chr(10).join(dependency_lines)}
tasks.register('dumpArtifacts') {{ doLast {{ def result = [:]; {chr(10).join(dump_lines)}; file('{resolved_json.as_posix()}').text = groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(result)) }} }}
'''
    resolver_gradle = WORK / "resolver.gradle"
    resolver_gradle.write_text(groovy, encoding="utf-8")
    (WORK / "settings.gradle").write_text("rootProject.name = 'compose-bundle-resolver'\n", encoding="utf-8")
    run("gradle", "-q", "-b", resolver_gradle, "dumpArtifacts")
    resolved = json.loads(resolved_json.read_text(encoding="utf-8"))

    android_jar_env = os.environ.get("ANDROID_JAR")
    android_jar = Path(android_jar_env) if android_jar_env else Path(os.environ.get("ANDROID_SDK_ROOT", os.environ.get("ANDROID_HOME", ""))) / "platforms" / ANDROID_PLATFORM / "android.jar"
    if not android_jar.is_file(): raise RuntimeError(f"android.jar not found: {android_jar}")
    d8 = shutil.which("d8")
    if not d8:
        candidates = sorted(Path(os.environ["ANDROID_SDK_ROOT"]).glob("build-tools/*/d8"))
        if not candidates: raise RuntimeError("d8 executable not found")
        d8 = str(candidates[-1])

    bundle_root = WORK / "bundle"
    (bundle_root / "classes").mkdir(parents=True)
    (bundle_root / "dex").mkdir(parents=True)
    artifact_by_file, file_meta, feature_files = {}, {}, {fid: [] for fid in FEATURES}
    rejected = []
    for fid, entries in resolved.items():
        for entry in entries:
            f, module = entry["file"], entry["module"]
            if excluded(module):
                rejected.append(module); continue
            file_meta[f] = module
            if f not in artifact_by_file:
                group, name, version = module.split(":", 2)
                artifact_by_file[f] = f"{group}_{name}".replace(".", "_")
            feature_files[fid].append(f)

    # Deduplicate byte-identical artifacts before D8.
    import hashlib
    unique = {}
    duplicate_count = 0
    for file in artifact_by_file:
        data = Path(file).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in unique:
            duplicate_count += 1
            continue
        unique[digest] = file
    artifact_by_file = {f: artifact_by_file[f] for f in unique.values()}

    for file, aid in artifact_by_file.items():
        src = Path(file)
        classes_jar = bundle_root / "classes" / f"{aid}.jar"
        if src.suffix == ".aar":
            with zipfile.ZipFile(src) as zf:
                if "classes.jar" not in zf.namelist(): continue
                classes_jar.write_bytes(zf.read("classes.jar"))
        else: shutil.copy2(src, classes_jar)
        if not classes_jar.exists() or classes_jar.stat().st_size == 0: continue
        dex_tmp = WORK / "dex-tmp"
        if dex_tmp.exists(): shutil.rmtree(dex_tmp)
        dex_tmp.mkdir()
        run(d8, "--min-api", "23", "--lib", android_jar, "--output", dex_tmp, classes_jar)
        dex_files = sorted(dex_tmp.glob("classes*.dex"))
        if not dex_files: continue
        if len(dex_files) > 1: raise RuntimeError(f"D8 produced multiple dex files for {src.name}")
        shutil.move(dex_files[0], bundle_root / "dex" / f"{aid}.dex")

    artifacts = []
    for file, aid in sorted(artifact_by_file.items(), key=lambda item: item[1]):
        module = file_meta[file]
        group, name, version = module.split(":", 2)
        artifacts.append({"id": aid, "coordinate": module, "packageName": group, "dependencies": []})
    for fid, feature in FEATURES.items(): feature["artifacts"] = [artifact_by_file[f] for f in feature_files[fid] if f in artifact_by_file]
    manifest = {"schemaVersion": 1, "composeVersion": COMPOSE_UI, "features": [{"id": fid, **{k:v for k,v in feature.items() if k != "roots"}} for fid, feature in FEATURES.items()], "artifacts": artifacts, "skippedBuiltInDependencies": list(SKIP_COORDINATE_PREFIXES), "rejectedPlatformArtifacts": sorted(set(rejected)), "buildStats": {"uniqueArtifacts": len(artifact_by_file), "duplicateArtifactsRemoved": duplicate_count}}
    (OUT / "compose-libraries.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = OUT / "compose-libs.zip"
    if archive.exists(): archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in bundle_root.rglob("*"):
            if path.is_file(): zf.write(path, path.relative_to(bundle_root))
    print(f"Wrote {archive} and {OUT / 'compose-libraries.json'}")

if __name__ == "__main__": main()
