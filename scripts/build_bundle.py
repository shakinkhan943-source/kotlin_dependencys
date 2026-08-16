#!/usr/bin/env python3
"""Build an Android-only Jetpack Compose dependency bundle.

Compose roots use concrete Android-published artifacts. Gradle resolves their
normal Android/JVM transitive graph. All resolved bytecode is then supplied to
one D8 invocation so cross-library references are resolved together and the
bundle can contain classes.dex, classes2.dex, etc. as required.
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


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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

    # Each feature resolves its own configuration, so Gradle's version-conflict
    # resolution never sees the full graph at once: two features can legally
    # land on two different versions of the same module (e.g. lifecycle-viewmodel-ktx
    # pulled in by both navigation-compose and lifecycle-viewmodel-compose).
    # Artifact IDs are derived from group:name only (no version), so two such
    # files collide on disk and/or reach D8 as conflicting duplicate classes.
    # Align every module to a single, highest-resolved version up front.
    def version_key(version):
        return tuple((0, int(tok)) if tok.isdigit() else (1, tok) for tok in version.replace("-", ".").split("."))

    canonical = {}
    for entries in resolved.values():
        for entry in entries:
            group, name, version = entry["module"].split(":", 2)
            key = f"{group}:{name}"
            if key not in canonical or version_key(version) > version_key(canonical[key]["version"]):
                canonical[key] = {"version": version, "file": entry["file"], "module": entry["module"]}

    version_conflicts = []
    for fid, entries in resolved.items():
        for entry in entries:
            group, name, version = entry["module"].split(":", 2)
            key = f"{group}:{name}"
            chosen = canonical[key]
            if chosen["version"] != version:
                version_conflicts.append({"feature": fid, "coordinate": entry["module"], "alignedTo": chosen["module"]})
            entry["file"] = chosen["file"]
            entry["module"] = chosen["module"]

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
            f = str(Path(entry["file"]).resolve())
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

    # Materialize each artifact once, then deduplicate byte-identical class
    # archives. Different Gradle artifact records can point at the same file,
    # or different files can contain exactly the same classes. Feeding either
    # duplicate to D8 causes duplicate-class errors.
    input_jars = []
    seen_hashes = {}
    duplicate_inputs = []
    total_input_bytes = 0
    for source, aid in sorted(artifact_by_file.items(), key=lambda item: item[1]):
        src = Path(source)
        classes_jar = classes_dir / f"{aid}.jar"
        if src.suffix.lower() == ".aar":
            with zipfile.ZipFile(src) as zf:
                if "classes.jar" not in zf.namelist():
                    continue
                classes_jar.write_bytes(zf.read("classes.jar"))
        elif src.suffix.lower() == ".jar":
            shutil.copy2(src, classes_jar)
        else:
            continue
        if not classes_jar.exists() or classes_jar.stat().st_size == 0:
            continue

        digest = sha256_file(classes_jar)
        if digest in seen_hashes:
            duplicate_inputs.append({"artifact": aid, "duplicateOf": seen_hashes[digest], "sha256": digest})
            classes_jar.unlink()
            continue
        seen_hashes[digest] = aid
        input_jars.append(classes_jar)
        total_input_bytes += classes_jar.stat().st_size

    if not input_jars:
        raise RuntimeError("No Android bytecode artifacts were collected for D8")

    # Some AndroidX/Kotlin-multiplatform libraries publish both an umbrella
    # module and a JVM-target module under different Maven coordinates (e.g.
    # androidx.collection:collection vs collection-jvm, compose.runtime:runtime
    # vs runtime-android, lifecycle-runtime-ktx vs lifecycle-runtime-ktx-android).
    # Both can legally resolve for different transitive paths and both contain
    # the same compiled classes, which D8 rejects as duplicate class
    # definitions even though the *files* aren't byte-identical (so the
    # sha256 file-level dedup above doesn't catch them). Build de-duplicated
    # copies for the D8 invocation only, dropping any .class entry already
    # seen in an earlier jar (sorted by artifact id) -- the untouched
    # per-artifact jars in classes_dir still ship in compose-libs.zip.
    d8_dir = bundle_root / "d8-input"
    d8_dir.mkdir(parents=True, exist_ok=True)
    seen_class_names = {}
    class_duplicates = []
    d8_input_jars = []
    for classes_jar in input_jars:
        d8_jar = d8_dir / classes_jar.name
        kept_any = False
        with zipfile.ZipFile(classes_jar) as src_zf, zipfile.ZipFile(d8_jar, "w", zipfile.ZIP_DEFLATED) as dst_zf:
            for info in src_zf.infolist():
                if info.filename.endswith(".class"):
                    if info.filename in seen_class_names:
                        class_duplicates.append({"class": info.filename, "droppedFrom": classes_jar.name, "keptIn": seen_class_names[info.filename]})
                        continue
                    seen_class_names[info.filename] = classes_jar.name
                dst_zf.writestr(info, src_zf.read(info.filename))
                kept_any = True
        if kept_any:
            d8_input_jars.append(d8_jar)

    if not d8_input_jars:
        raise RuntimeError("No Android bytecode remained for D8 after class-level dedup")

    run(d8, "--min-api", "23", "--lib", android_jar, "--output", dex_dir, *d8_input_jars)
    dex_files = sorted(dex_dir.glob("classes*.dex"), key=lambda p: (p.name != "classes.dex", p.name))
    if not dex_files:
        raise RuntimeError("D8 produced no dex files")

    artifacts = []
    for source, aid in sorted(artifact_by_file.items(), key=lambda item: item[1]):
        module = file_meta[source]
        group, _name, _version = module.split(":", 2)
        artifacts.append({"id": aid, "coordinate": module, "packageName": group, "dependencies": []})

    for fid, feature in FEATURES.items():
        feature["artifacts"] = [artifact_by_file[f] for f in sorted(feature_files[fid]) if f in artifact_by_file]

    manifest = {"schemaVersion": 1, "composeVersion": COMPOSE_UI, "features": [{"id": fid, **{k: v for k, v in feature.items() if k != "roots"}} for fid, feature in FEATURES.items()], "artifacts": artifacts}
    (OUT / "compose-libraries.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    report = {"androidOnly": True, "resolutionStrategy": "explicit-android-artifacts", "composeVersion": COMPOSE_UI, "material3Version": COMPOSE_MATERIAL3, "artifactCount": len(artifacts), "uniqueD8InputCount": len(input_jars), "duplicateD8InputCount": len(duplicate_inputs), "duplicates": duplicate_inputs, "versionConflictCount": len(version_conflicts), "versionConflicts": version_conflicts, "classDuplicateCount": len(class_duplicates), "classDuplicates": class_duplicates, "rejectedArtifactCount": len(rejected), "rejectedArtifacts": rejected, "d8InputBytes": total_input_bytes, "dexFileCount": len(dex_files), "dexFiles": [p.name for p in dex_files]}
    (OUT / "resolution-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    archive = OUT / "compose-libs.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in bundle_root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_root))

    print(f"Wrote {archive}")
    print(f"Android artifacts: {len(artifacts)} | rejected: {len(rejected)} | unique D8 inputs: {len(input_jars)} | duplicate jars removed: {len(duplicate_inputs)} | version conflicts aligned: {len(version_conflicts)} | duplicate classes dropped: {len(class_duplicates)} | D8 input: {total_input_bytes / (1024 * 1024):.1f} MiB | dex files: {len(dex_files)}")


if __name__ == "__main__":
    main()
