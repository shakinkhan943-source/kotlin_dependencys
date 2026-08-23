"""Build a curated Android-only Jetpack Compose dependency bundle.

Produces:
  output/compose-libraries.json   - manifest with feature roots + artifact dependency edges
  output/compose-libs.zip         - pre-dexed runtime + per-artifact compile jars/resources

Requires the `:compose-catalog:dumpArtifacts` Gradle task to also emit each resolved
artifact's dependency edges. See the README / build.gradle.kts note at the bottom of
this file; each entry of resolved.json must look like:

  {"file": "/abs/path/foo.aar", "module": "androidx.compose.ui:ui-android:1.7.8",
   "dependencies": ["androidx.collection:collection-jvm", "..."]}

The `dependencies` values MUST be the resolved variant module ids (group:name, no
version) taken from the same Gradle resolution that produced `module`, otherwise the
KMP variant suffixes (_android/_jvm) won't line up with the bundle artifact ids and
Sketchware's transitive closure will be incomplete.
"""
import hashlib
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
    "lifecycle-compose": {"name": "Lifecycle ViewModel + Runtime Compose", "description": "ViewModel + lifecycle-aware state collection for Compose (viewModel(), collectAsStateWithLifecycle()).", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.lifecycle:lifecycle-viewmodel-compose:{LIFECYCLE_COMPOSE}", f"androidx.lifecycle:lifecycle-runtime-compose-android:{LIFECYCLE_COMPOSE}"]},
    "ui-tooling-preview": {"name": "Compose UI Tooling Preview", "description": "Stubs required by @Preview composables.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.compose.ui:ui-tooling-preview-android:{COMPOSE_UI}"]},
}

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


def jar_has_classes(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return any(n.endswith(".class") for n in zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def main():
    if WORK.exists(): shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # Resolution now happens in the real `compose-catalog` Android library
    # module (see compose-catalog/build.gradle.kts) instead of a hand-rolled
    # Gradle configuration in a generated script. AGP wires up the correct
    # Kotlin Multiplatform variant attributes automatically -- the same way
    # any real app's runtimeClasspath does -- so ui-text-android,
    # ui-graphics-android, lifecycle-runtime-compose-android, etc. all
    # resolve correctly without manual attribute hacking.
    resolved_json = ROOT / "compose-catalog" / "build" / "resolved.json"
    if resolved_json.exists(): resolved_json.unlink()
    run("gradle", "-q", "-p", ROOT, ":compose-catalog:dumpArtifacts")
    if not resolved_json.is_file():
        raise RuntimeError(f"dumpArtifacts did not produce {resolved_json}")
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
    dex_root = bundle_root / "dex"
    dex_root.mkdir(parents=True)
    classes_root = WORK / "classes"
    classes_root.mkdir(parents=True)

    selected = {}
    rejected = []
    for entry in resolved:
        file, module = entry["file"], entry["module"]
        if excluded(module):
            rejected.append(module)
            continue
        group, name, version = module.split(":", 2)
        selected[f"{group}:{name}"] = {"file": file, "module": module}

    unique_by_hash = {}
    duplicate_count = 0
    final_entries = []
    for key, entry in sorted(selected.items()):
        digest = hashlib.sha256(Path(entry["file"]).read_bytes()).hexdigest()
        if digest in unique_by_hash:
            duplicate_count += 1
            continue
        unique_by_hash[digest] = key
        final_entries.append(entry)
    selected_aids = set()
    for entry in final_entries:
        g, n, _ = entry["module"].split(":", 2)
        selected_aids.add(f"{g}_{n}".replace(".", "_").replace("-", "_"))

    artifacts = []
    skipped_no_classes = []
    dex_count = 0
    library_roots = []  # (aid, dir_path) later packaged under libraries/<aid>/
    for entry in final_entries:
        src = Path(entry["file"])
        group, name, version = entry["module"].split(":", 2)
        aid = f"{group}_{name}".replace(".", "_").replace("-", "_")
        classes_jar = classes_root / f"{aid}.jar"
        lib_dir = WORK / "libs" / aid
        lib_dir.mkdir(parents=True)
        if src.suffix.lower() == ".aar":
            with zipfile.ZipFile(src) as zf:
                if "classes.jar" not in zf.namelist():
                    skipped_no_classes.append(entry["module"])
                    continue
                classes_jar.write_bytes(zf.read("classes.jar"))
                if "proguard.txt" in zf.namelist():
                    (lib_dir / "proguard.txt").write_bytes(zf.read("proguard.txt"))
                for member in ("res", "assets"):
                    for nm in zf.namelist():
                        if nm.startswith(member + "/") and not nm.endswith("/"):
                            (lib_dir / nm).write_bytes(zf.read(nm))
        else:
            shutil.copy2(src, classes_jar)
        if not classes_jar.exists() or classes_jar.stat().st_size == 0 or not jar_has_classes(classes_jar):
            skipped_no_classes.append(entry["module"])
            continue
        shutil.copy2(classes_jar, lib_dir / "classes.jar")
        library_roots.append((aid, lib_dir))

        dex_tmp = WORK / "dex-tmp"
        if dex_tmp.exists(): shutil.rmtree(dex_tmp)
        dex_tmp.mkdir(parents=True)
        run(d8, "--min-api", "23", "--lib", android_jar, "--output", dex_tmp, classes_jar)
        dex_files = sorted(dex_tmp.glob("classes*.dex"))
        if not dex_files:
            skipped_no_classes.append(entry["module"])
            continue
        for index, dex_file in enumerate(dex_files, 1):
            suffix = "" if index == 1 else str(index)
            shutil.move(dex_file, dex_root / f"{aid}{suffix}.dex")
        dex_count += len(dex_files)

        # Resolved runtime dependency edges (group:name, no version), filtered to
        # artifacts actually present in this bundle. Dependencies SK provides itself
        # (kotlin-stdlib, kotlinx-coroutines, ...) are dropped here.
        deps = []
        for dep in entry.get("dependencies", []) or []:
            if excluded(dep):
                continue
            dg, dn = dep.split(":", 2)[:2]
            did = f"{dg}_{dn}".replace(".", "_").replace("-", "_")
            if did in selected_aids:
                deps.append(did)
        artifacts.append({"id": aid, "coordinate": entry["module"], "packageName": group, "dependencies": deps})

    total_deps = sum(len(a["dependencies"]) for a in artifacts)
    if total_deps == 0:
        print("WARNING: no artifact has dependency edges. Sketchware will only select feature roots,")
        print("so transitive artifacts will be missing from the build. Make sure :compose-catalog:dumpArtifacts")
        print("emits the resolved graph in each entry's 'dependencies' field.", flush=True)

    feature_meta = []
    for fid, feature in FEATURES.items():
        roots_for_feature = []
        for root in feature["roots"]:
            parts = root.split(":")
            root_key = f"{parts[0]}:{parts[1]}"
            roots_for_feature += [a["id"] for a in artifacts if a["coordinate"].startswith(root_key + ":")]
        feature_meta.append({"id": fid, "name": feature["name"], "description": feature["description"], "required": feature["required"], "tag": feature["tag"], "roots": sorted(set(roots_for_feature))})
    manifest = {
        "schemaVersion": 2,
        "composeVersion": COMPOSE_UI,
        "material3Version": COMPOSE_MATERIAL3,
        "features": feature_meta,
        "artifacts": artifacts,
        "skippedBuiltInDependencies": list(SKIP_COORDINATE_PREFIXES),
        "rejectedPlatformArtifacts": sorted(set(rejected)),
        "skippedNoBytecodeArtifacts": sorted(set(skipped_no_classes)),
        "buildStats": {"globallyResolvedArtifacts": len(resolved), "uniqueArtifacts": len(artifacts), "duplicateArtifactsRemoved": duplicate_count, "dexFiles": dex_count},
    }
    (OUT / "compose-libraries.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Distribution ZIP carries the pre-dexed runtime, the per-artifact compile
    # classpath JARs (plus optional res/assets/proguard) and an embedded manifest.
    archive = OUT / "compose-libs.zip"
    if archive.exists(): archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in dex_root.rglob("*.dex"):
            zf.write(path, Path("dex") / path.name)
        for aid, lib_dir in library_roots:
            for path in lib_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, Path("libraries") / aid / path.relative_to(lib_dir))
        zf.writestr("compose-libraries.json", json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {archive} and {OUT / 'compose-libraries.json'}")
    print(f"Artifacts: {len(artifacts)}, DEX files: {dex_count}, Library dirs: {len(library_roots)}")


if __name__ == "__main__": main()
