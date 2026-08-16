#!/usr/bin/env python3
"""Build the Jetpack Compose Android dependency bundle."""
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
    "core": {"name": "Compose Core", "description": "Required Compose runtime, UI and foundation APIs.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.compose.runtime:runtime:{COMPOSE_UI}", f"androidx.compose.ui:ui:{COMPOSE_UI}", f"androidx.compose.foundation:foundation:{COMPOSE_UI}"]},
    "material3": {"name": "Material 3", "description": "Material 3 components and theming for Compose.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.compose.material3:material3:{COMPOSE_MATERIAL3}"]},
    "activity-compose": {"name": "Activity Compose", "description": "Integrates Compose content with Android activities.", "required": True, "tag": "IMPORTANT", "roots": [f"androidx.activity:activity-compose:{ACTIVITY_COMPOSE}"]},
    "animation": {"name": "Compose Animation", "description": "Animation APIs beyond the core foundation set.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.compose.animation:animation:{COMPOSE_UI}"]},
    "material-icons": {"name": "Material Icons Extended", "description": "The full Material icon set for Compose.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.compose.material:material-icons-extended:{COMPOSE_UI}"]},
    "navigation-compose": {"name": "Navigation Compose", "description": "Navigate between composables with a NavHost/NavController.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.navigation:navigation-compose:{NAVIGATION_COMPOSE}"]},
    "lifecycle-compose": {"name": "Lifecycle ViewModel Compose", "description": "ViewModel + lifecycle-aware state collection for Compose.", "required": False, "tag": "OPTIONAL", "roots": [f"androidx.lifecycle:lifecycle-viewmodel-compose:{LIFECYCLE_COMPOSE}"]},
}

# These are platforms/toolkits that must never enter an Android-only bundle.
# In particular, do NOT exclude every artifact containing "jvm": the current
# Sketchware/Android compiler can legitimately need JVM stub artifacts.
UNSUPPORTED_ARTIFACT_SUFFIXES = (
    "-desktop",
    "-windows",
    "-linux",
    "-macos",
    "-macosx",
    "-ios",
    "-tvos",
    "-watchos",
    "-wasm",
    "-js",
    "-mingw",
    "-swing",
    "-awt",
)
UNSUPPORTED_GROUPS = {
    "org.jetbrains.compose.desktop",
}


def is_android_artifact(module: str) -> bool:
    """Return whether a resolved Maven coordinate belongs in the Android bundle."""
    group, name, _version = module.split(":", 2)
    if group in UNSUPPORTED_GROUPS:
        return False
    return not name.lower().endswith(UNSUPPORTED_ARTIFACT_SUFFIXES)


def run(*args):
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)

    configurations = []
    dependency_lines = []
    for feature_id, feature in FEATURES.items():
        config_name = "compose_" + feature_id.replace("-", "_")
        configurations.append((feature_id, config_name))
        # Ask Gradle for an Android/JVM consumer variant. The old custom `ui`
        # attribute is retained for compatibility, but by itself it is not a
        # standard Compose/Gradle platform attribute and therefore cannot stop
        # Desktop variants from entering the graph.
        dependency_lines.append(
            f"def {config_name} = configurations.maybeCreate('{config_name}')\n"
            f"{config_name}.attributes {{\n"
            f"    attribute(org.gradle.api.attributes.Attribute.of('ui', String), 'android')\n"
            f"    attribute(org.gradle.api.attributes.Attribute.of('org.jetbrains.kotlin.platform.type', String), 'androidJvm')\n"
            f"    attribute(org.gradle.api.attributes.Usage.USAGE_ATTRIBUTE, objects.named(org.gradle.api.attributes.Usage, org.gradle.api.attributes.Usage.JAVA_RUNTIME))\n"
            f"    attribute(org.gradle.api.attributes.Category.CATEGORY_ATTRIBUTE, objects.named(org.gradle.api.attributes.Category, org.gradle.api.attributes.Category.LIBRARY))\n"
            f"}}\n"
            f"{config_name}.resolutionStrategy.componentSelection {{\n"
            f"    all {{ selection ->\n"
            f"        def n = selection.candidate.module.toLowerCase()\n"
            f"        def blocked = ['-desktop', '-windows', '-linux', '-macos', '-macosx', '-ios', '-tvos', '-watchos', '-wasm', '-js', '-mingw', '-swing', '-awt'].any {{ n.endsWith(it) }}\n"
            f"        def g = selection.candidate.group.toLowerCase()\n"
            f"        if (blocked || g == 'org.jetbrains.compose.desktop') {{\n"
            f"            selection.reject('Android-only Compose bundle: unsupported platform artifact')\n"
            f"        }}\n"
            f"    }}\n"
            f"}}"
        )
        for coord in feature["roots"]:
            dependency_lines.append(f"dependencies.add('{config_name}', '{coord}')")

    resolved_json = WORK / "resolved.json"
    dump_lines = [
        f"result['{fid}'] = configurations.getByName('{cname}').resolvedConfiguration.resolvedArtifacts.collect {{ a -> [file: a.file.absolutePath, module: a.moduleVersion.id.group + ':' + a.name + ':' + a.moduleVersion.id.version] }}"
        for fid, cname in configurations
    ]
    groovy = f"""
repositories {{
    google()
    mavenCentral()
    maven {{ url "https://maven.pkg.jetbrains.space/public/p/compose/dev" }}
    maven {{ url "https://androidx.dev/storage/compose-mirrors/repository/" }}
}}
{chr(10).join(dependency_lines)}

tasks.register('dumpArtifacts') {{
    doLast {{
        def result = [:]
        {chr(10).join(dump_lines)}
        file('{resolved_json.as_posix()}').text = groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(result))
    }}
}}
"""
    resolver_gradle = WORK / "resolver.gradle"
    resolver_gradle.write_text(groovy, encoding="utf-8")
    (WORK / "settings.gradle").write_text("rootProject.name = 'compose-bundle-resolver'\n", encoding="utf-8")

    run("gradle", "-q", "-b", resolver_gradle, "dumpArtifacts")
    resolved = json.loads(resolved_json.read_text(encoding="utf-8"))

    # Belt-and-suspenders safety check: even if a repository publishes a
    # malformed variant or a future Gradle change bypasses component selection,
    # an unsupported platform artifact can never be copied into the bundle.
    rejected = []
    for fid, entries in resolved.items():
        kept = []
        for entry in entries:
            if is_android_artifact(entry["module"]):
                kept.append(entry)
            else:
                rejected.append({"feature": fid, "coordinate": entry["module"]})
        resolved[fid] = kept

    if rejected:
        print(f"Rejected {len(rejected)} unsupported platform artifacts after resolution:")
        for item in rejected:
            print(f"  - {item['feature']}: {item['coordinate']}")

    # Do not use Path("") here: it becomes '.' and makes the existence check
    # succeed, which previously caused D8 to receive the project directory as
    # its --lib instead of the actual Android platform jar.
    android_jar_env = os.environ.get("ANDROID_JAR")
    android_jar = Path(android_jar_env) if android_jar_env else Path()
    if not android_jar_env or not android_jar.is_file():
        sdk = Path(os.environ.get("ANDROID_SDK_ROOT", os.environ.get("ANDROID_HOME", "")))
        android_jar = sdk / "platforms" / ANDROID_PLATFORM / "android.jar"
    if not android_jar.is_file():
        raise RuntimeError(f"android.jar not found: {android_jar}")

    bundle_root = WORK / "bundle"
    (bundle_root / "classes").mkdir(parents=True)
    (bundle_root / "dex").mkdir(parents=True)

    artifact_by_file, file_meta, feature_files = {}, {}, {}
    for fid, entries in resolved.items():
        feature_files[fid] = []
        for entry in entries:
            f = entry["file"]
            file_meta[f] = entry["module"]
            feature_files[fid].append(f)
            if f not in artifact_by_file:
                group, name, version = entry["module"].split(":", 2)
                artifact_by_file[f] = f"{group}_{name}".replace(".", "_").replace(":", "_")

    d8 = shutil.which("d8")
    if not d8:
        candidates = sorted(Path(os.environ["ANDROID_SDK_ROOT"]).glob("build-tools/*/d8"))
        if not candidates:
            raise RuntimeError("d8 executable not found")
        d8 = str(candidates[-1])

    total_input_bytes = 0
    for file, aid in artifact_by_file.items():
        src = Path(file)
        classes_jar = bundle_root / "classes" / f"{aid}.jar"
        if src.suffix == ".aar":
            with zipfile.ZipFile(src) as zf:
                if "classes.jar" in zf.namelist():
                    classes_jar.write_bytes(zf.read("classes.jar"))
        else:
            shutil.copy2(src, classes_jar)

        if not classes_jar.exists() or classes_jar.stat().st_size == 0:
            # Some published artifacts are metadata/empty JVM stub artifacts.
            # They contain no bytecode and therefore have nothing to D8.
            continue

        total_input_bytes += classes_jar.stat().st_size
        dex_tmp = WORK / "dex-tmp"
        if dex_tmp.exists():
            shutil.rmtree(dex_tmp)
        dex_tmp.mkdir()
        run(d8, "--min-api", "23", "--lib", android_jar, "--output", dex_tmp, classes_jar)

        dex_files = sorted(dex_tmp.glob("classes*.dex"))
        if not dex_files:
            # D8 can legitimately produce no dex for an empty/metadata-only jar.
            # Do not make the whole bundle fail merely because classes.dex is absent.
            continue
        if len(dex_files) > 1:
            raise RuntimeError(f"D8 produced multiple dex files for {src.name}: {dex_files}")
        shutil.move(dex_files[0], bundle_root / "dex" / f"{aid}.dex")

    artifacts = []
    for file, aid in sorted(artifact_by_file.items(), key=lambda item: item[1]):
        module = file_meta[file]
        group, name, version = module.split(":", 2)
        artifacts.append({"id": aid, "coordinate": module, "packageName": group, "dependencies": []})
    for fid, feature in FEATURES.items():
        feature["artifacts"] = [artifact_by_file[f] for f in sorted(feature_files[fid])]

    manifest = {
        "schemaVersion": 1,
        "composeVersion": COMPOSE_UI,
        "features": [{"id": fid, **{k: v for k, v in feature.items() if k != "roots"}} for fid, feature in FEATURES.items()],
        "artifacts": artifacts,
    }
    (OUT / "compose-libraries.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = {
        "androidOnly": True,
        "composeVersion": COMPOSE_UI,
        "material3Version": COMPOSE_MATERIAL3,
        "artifactCount": len(artifacts),
        "rejectedArtifactCount": len(rejected),
        "rejectedArtifacts": rejected,
        "d8InputBytes": total_input_bytes,
    }
    (OUT / "resolution-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    archive = OUT / "compose-libs.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in bundle_root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_root))
    print(f"Wrote {archive} and {OUT / 'compose-libraries.json'}")
    print(f"Android artifacts: {len(artifacts)} | rejected: {len(rejected)} | D8 input: {total_input_bytes / (1024 * 1024):.1f} MiB")


if __name__ == "__main__":
    main()
