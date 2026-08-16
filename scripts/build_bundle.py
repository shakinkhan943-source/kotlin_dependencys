#!/usr/bin/env python3
"""
Resolves the essential Jetpack Compose Maven artifacts, dexes each one
SEPARATELY (never merged), and packages them into:

  output/compose-libs.zip        - classes/<artifact-id>.jar + dex/<artifact-id>.dex per artifact
  output/compose-libraries.json  - manifest describing features + artifacts

This repo has no dependencyResolutionManagement restrictions, so the
generated Gradle script is free to declare its own repositories{} block
(unlike sketchware-pro's monorepo, which uses FAIL_ON_PROJECT_REPOS).
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
    "core": {
        "name": "Compose Core",
        "description": "Required Compose runtime, UI and foundation APIs.",
        "required": True,
        "tag": "IMPORTANT",
        "roots": [
            f"androidx.compose.runtime:runtime:{COMPOSE_UI}",
            f"androidx.compose.ui:ui:{COMPOSE_UI}",
            f"androidx.compose.foundation:foundation:{COMPOSE_UI}",
        ],
    },
    "material3": {
        "name": "Material 3",
        "description": "Material 3 components and theming for Compose.",
        "required": True,
        "tag": "IMPORTANT",
        "roots": [f"androidx.compose.material3:material3:{COMPOSE_MATERIAL3}"],
    },
    "activity-compose": {
        "name": "Activity Compose",
        "description": "Integrates Compose content with Android activities.",
        "required": True,
        "tag": "IMPORTANT",
        "roots": [f"androidx.activity:activity-compose:{ACTIVITY_COMPOSE}"],
    },
    "animation": {
        "name": "Compose Animation",
        "description": "Animation APIs beyond the core foundation set.",
        "required": False,
        "tag": "OPTIONAL",
        "roots": [f"androidx.compose.animation:animation:{COMPOSE_UI}"],
    },
    "material-icons": {
        "name": "Material Icons Extended",
        "description": "The full Material icon set for Compose.",
        "required": False,
        "tag": "OPTIONAL",
        "roots": [f"androidx.compose.material:material-icons-extended:{COMPOSE_UI}"],
    },
    "navigation-compose": {
        "name": "Navigation Compose",
        "description": "Navigate between composables with a NavHost/NavController.",
        "required": False,
        "tag": "OPTIONAL",
        "roots": [f"androidx.navigation:navigation-compose:{NAVIGATION_COMPOSE}"],
    },
    "lifecycle-compose": {
        "name": "Lifecycle ViewModel Compose",
        "description": "ViewModel + lifecycle-aware state collection for Compose.",
        "required": False,
        "tag": "OPTIONAL",
        "roots": [f"androidx.lifecycle:lifecycle-viewmodel-compose:{LIFECYCLE_COMPOSE}"],
    },
}


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
        dependency_lines.append(f"configurations.maybeCreate('{config_name}')")
        for coord in feature["roots"]:
            dependency_lines.append(f"dependencies.add('{config_name}', '{coord}')")

    resolved_json = WORK / "resolved.json"
    dump_lines = [
        f"result['{fid}'] = configurations.getByName('{cname}')"
        f".resolvedConfiguration.resolvedArtifacts.collect {{ a -> "
        f"[file: a.file.absolutePath, "
        f"module: a.moduleVersion.id.group + ':' + a.name + ':' + a.moduleVersion.id.version] }}"
        for fid, cname in configurations
    ]
    groovy = f"""
repositories {{
    google()
    mavenCentral()
}}
{chr(10).join(dependency_lines)}

tasks.register('dumpArtifacts') {{
    doLast {{
        def result = [:]
        {chr(10).join(dump_lines)}
        file('{resolved_json.as_posix()}').text =
            groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(result))
    }}
}}
"""
    resolver_gradle = WORK / "resolver.gradle"
    resolver_gradle.write_text(groovy, encoding="utf-8")
    
    # Add settings.gradle to make the directory a valid Gradle project
    settings_gradle = WORK / "settings.gradle"
    settings_gradle.write_text("rootProject.name = 'compose-bundle-resolver'\n", encoding="utf-8")
    
    run("gradle", "-q", "-b", resolver_gradle, "dumpArtifacts")

    resolved = json.loads(resolved_json.read_text(encoding="utf-8"))

    android_jar = Path(os.environ.get("ANDROID_JAR", ""))
    if not android_jar.exists():
        sdk = Path(os.environ.get("ANDROID_SDK_ROOT", os.environ.get("ANDROID_HOME", "")))
        android_jar = sdk / "platforms" / ANDROID_PLATFORM / "android.jar"
    if not android_jar.exists():
        raise RuntimeError(f"android.jar not found: {android_jar}")

    bundle_root = WORK / "bundle"
    (bundle_root / "classes").mkdir(parents=True)
    (bundle_root / "dex").mkdir(parents=True)

    artifact_by_file = {}
    file_meta = {}
    feature_files = {}
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

    for file, aid in artifact_by_file.items():
        src = Path(file)
        classes_jar = bundle_root / "classes" / f"{aid}.jar"
        if src.suffix == ".aar":
            with zipfile.ZipFile(src) as zf:
                if "classes.jar" in zf.namelist():
                    classes_jar.write_bytes(zf.read("classes.jar"))
                res_dir = bundle_root / "res" / aid
                if any(n.startswith("res/") for n in zf.namelist()):
                    res_dir.mkdir(parents=True, exist_ok=True)
                    for name in zf.namelist():
                        if name.startswith("res/") and not name.endswith("/"):
                            out = res_dir / name[len("res/"):]
                            out.parent.mkdir(parents=True, exist_ok=True)
                            out.write_bytes(zf.read(name))
        else:
            shutil.copy2(src, classes_jar)

        if not classes_jar.exists() or classes_jar.stat().st_size == 0:
            (bundle_root / "dex" / f"{aid}.dex").touch()
            continue

        dex_tmp = WORK / "dex-tmp"
        if dex_tmp.exists():
            shutil.rmtree(dex_tmp)
        dex_tmp.mkdir()
        run(d8, "--min-api", "23", "--lib", android_jar, "--output", dex_tmp, classes_jar)
        shutil.move(dex_tmp / "classes.dex", bundle_root / "dex" / f"{aid}.dex")

    artifacts = []
    for file, aid in sorted(artifact_by_file.items(), key=lambda item: item[1]):
        module = file_meta[file]
        group, name, version = module.split(":", 2)
        artifacts.append({
            "id": aid,
            "coordinate": module,
            "packageName": group,
            "dependencies": [],
        })

    for fid, feature in FEATURES.items():
        feature["artifacts"] = [artifact_by_file[f] for f in sorted(feature_files[fid])]

    manifest = {
        "schemaVersion": 1,
        "composeVersion": COMPOSE_UI,
        "features": [
            {"id": fid, **{k: v for k, v in feature.items() if k != "roots"}}
            for fid, feature in FEATURES.items()
        ],
        "artifacts": artifacts,
    }

    (OUT / "compose-libraries.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    archive = OUT / "compose-libs.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in bundle_root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_root))

    print(f"Wrote {archive} and {OUT / 'compose-libraries.json'}")


if __name__ == "__main__":
    main()
