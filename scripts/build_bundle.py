"""Build a curated Android-only Jetpack Compose dependency bundle.

Produces:
  output/compose-libs.zip  - per-artifact folder layout:
        <id>/classes.jar         - compile-time jar
        <id>/classes.dex         - pre-dexed runtime bytecode (classes2.dex, ... if multi-dex)
        <id>/res/...             - extracted AAR resources
        <id>/assets/...          - extracted AAR assets
        <id>/proguard.txt        - consumer ProGuard rules, if any
        <id>/AndroidManifest.xml - AAR manifest (for manifest merger), if any
  output/compose-libraries.json - machine-readable manifest: the toolchain the
        bundle was built with, every packaged artifact and its version, plus the
        modules the *host app* has to provide (kotlin-stdlib, coroutines) with
        the exact versions the bundle was resolved against.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
WORK = ROOT / "build" / "bundle"

# Toolchain / runtime pins. Keep the defaults in sync with
# compose-catalog/build.gradle.kts and build.gradle.kts.
GRADLE_VERSION = os.environ.get("GRADLE_VERSION", "9.6.0")
AGP_VERSION = os.environ.get("AGP_VERSION", "9.4.0")
KOTLIN_VERSION = os.environ.get("KOTLIN_VERSION", "2.4.10")
COROUTINES_VERSION = os.environ.get("COROUTINES_VERSION", "1.11.0")
COMPOSE_BOM_VERSION = os.environ.get("COMPOSE_BOM_VERSION", "2026.06.01")
COMPILE_SDK_LEVEL = os.environ.get("ANDROID_COMPILE_SDK_LEVEL", "36")
MIN_SDK_LEVEL = os.environ.get("ANDROID_MIN_SDK_LEVEL", "26")
ANDROID_PLATFORM = os.environ.get("ANDROID_COMPILE_SDK", f"android-{COMPILE_SDK_LEVEL}")

# Modules deliberately kept OUT of the bundle: the host app (Sketchware-Pro)
# already ships them, and shipping a second copy is what causes duplicate-class
# / NoSuchMethodError style version mismatches at runtime. The versions the
# bundle was resolved against are recorded in compose-libraries.json so the app
# side can check it provides something compatible (>= those versions).
SKIP_COORDINATE_PREFIXES = (
    "org.jetbrains.kotlin:kotlin-stdlib",
    "org.jetbrains.kotlin:kotlin-stdlib-common",
    "org.jetbrains.kotlin:kotlin-stdlib-jdk7",
    "org.jetbrains.kotlin:kotlin-stdlib-jdk8",
    "org.jetbrains.kotlin:kotlin-reflect",
    "org.jetbrains.kotlinx:kotlinx-coroutines-core",
    "org.jetbrains.kotlinx:kotlinx-coroutines-core-jvm",
    "org.jetbrains.kotlinx:kotlinx-coroutines-android",
    "org.jetbrains.kotlinx:kotlinx-coroutines-bom",
)
PLATFORM_TOKENS = ("-desktop", "-jvmstubs", "-jvm-stubs", "-ios", "-wasm", "-js", "-linux", "-macos", "-swing", "-awt")

# module -> version that MUST match the pinned toolchain. If Gradle resolved
# something else, the force()/BOM pins in compose-catalog/build.gradle.kts are
# not doing their job and the bundle would be built against a different Kotlin
# runtime than the app ships -- fail loudly instead of shipping a mismatch.
EXPECTED_VERSIONS = {
    "org.jetbrains.kotlin:kotlin-stdlib": KOTLIN_VERSION,
    "org.jetbrains.kotlin:kotlin-reflect": KOTLIN_VERSION,
    "org.jetbrains.kotlinx:kotlinx-coroutines-core": COROUTINES_VERSION,
    "org.jetbrains.kotlinx:kotlinx-coroutines-core-jvm": COROUTINES_VERSION,
    "org.jetbrains.kotlinx:kotlinx-coroutines-android": COROUTINES_VERSION,
}


def run(*args):
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def find_android_jar():
    """android.jar for the requested platform, else the highest one installed."""
    explicit = os.environ.get("ANDROID_JAR")
    if explicit:
        return Path(explicit)

    sdk_root = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or "")
    preferred = sdk_root / "platforms" / ANDROID_PLATFORM / "android.jar"
    if preferred.is_file():
        return preferred

    def level(path):
        match = re.search(r"android-(\d+)", path.parent.name)
        return int(match.group(1)) if match else -1

    installed = sorted((p for p in sdk_root.glob("platforms/android-*/android.jar")), key=level)
    if installed:
        print(f"! {preferred} not installed, falling back to {installed[-1]}", flush=True)
        return installed[-1]
    return preferred


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

    resolved_json = ROOT / "compose-catalog" / "build" / "resolved.json"
    if resolved_json.exists(): resolved_json.unlink()
    run("gradle", "-q", "-p", ROOT, ":compose-catalog:dumpArtifacts")
    if not resolved_json.is_file():
        raise RuntimeError(f"dumpArtifacts did not produce {resolved_json}")
    resolved = json.loads(resolved_json.read_text(encoding="utf-8"))

    android_jar = find_android_jar()
    if not android_jar.is_file(): raise RuntimeError(f"android.jar not found: {android_jar}")
    d8 = shutil.which("d8")
    if not d8:
        candidates = sorted(Path(os.environ["ANDROID_SDK_ROOT"]).glob("build-tools/*/d8"))
        if not candidates: raise RuntimeError("d8 executable not found")
        d8 = str(candidates[-1])

    # Per-artifact staging directory. Each artifact gets its own folder
    # named by its <id> and containing classes.jar, classes*.dex, and (when
    # the AAR ships them) res/, assets/, proguard.txt, AndroidManifest.xml.
    artifacts_root = WORK / "artifacts"
    artifacts_root.mkdir(parents=True)

    classes_tmp = WORK / "classes-tmp"
    classes_tmp.mkdir(parents=True)

    selected = {}
    host_provided = {}
    rejected = []
    mismatches = []
    for entry in resolved:
        file, module = entry["file"], entry["module"]
        group, name, version = module.split(":", 2)
        key = f"{group}:{name}"

        expected = EXPECTED_VERSIONS.get(key)
        if expected and version != expected:
            mismatches.append(f"{key}: resolved {version}, expected {expected}")

        if excluded(module):
            rejected.append(module)
            if key.startswith(("org.jetbrains.kotlin:", "org.jetbrains.kotlinx:")):
                host_provided[key] = version
            continue
        selected[key] = {"file": file, "module": module}

    if mismatches:
        raise RuntimeError(
            "Kotlin runtime version mismatch (check the force()/BOM pins in "
            "compose-catalog/build.gradle.kts):\n  " + "\n  ".join(mismatches)
        )

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

    artifact_count = 0
    dex_count = 0
    skipped_no_classes = []
    packaged = []

    for entry in final_entries:
        src = Path(entry["file"])
        group, name, version = entry["module"].split(":", 2)
        aid = f"{group}_{name}".replace(".", "_").replace("-", "_")

        art_dir = artifacts_root / aid
        art_dir.mkdir(parents=True, exist_ok=False)

        classes_jar = classes_tmp / f"{aid}.jar"
        if classes_jar.exists(): classes_jar.unlink()

        # Extract classes.jar + res/ + assets/ + consumer rules + manifest from
        # AARs; plain jars are used as-is. res, assets, the manifest and
        # consumer ProGuard rules are all required by the IDE and build
        # pipeline (resource merger, manifest merger, aapt2, R8), so dropping
        # them causes compile/runtime errors.
        if src.suffix.lower() == ".aar":
            with zipfile.ZipFile(src) as zf:
                names = zf.namelist()
                if "classes.jar" not in names:
                    skipped_no_classes.append(entry["module"])
                    shutil.rmtree(art_dir)
                    continue
                classes_jar.write_bytes(zf.read("classes.jar"))
                # Directory trees shipped by the AAR.
                for nm in names:
                    if (nm.startswith("res/") or nm.startswith("assets/")) and not nm.endswith("/"):
                        out = art_dir / nm
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(zf.read(nm))
                # Single files the IDE/build pipeline expects at the library root.
                for single in ("proguard.txt", "consumer-rules.pro", "AndroidManifest.xml"):
                    if single in names:
                        (art_dir / single).write_bytes(zf.read(single))
        else:
            shutil.copy2(src, classes_jar)

        if not classes_jar.exists() or classes_jar.stat().st_size == 0 or not jar_has_classes(classes_jar):
            skipped_no_classes.append(entry["module"])
            shutil.rmtree(art_dir)
            continue

        shutil.copy2(classes_jar, art_dir / "classes.jar")

        # Dex the jar individually and place classes*.dex next to classes.jar.
        dex_tmp = WORK / "dex-tmp"
        if dex_tmp.exists(): shutil.rmtree(dex_tmp)
        dex_tmp.mkdir(parents=True)
        run(d8, "--min-api", MIN_SDK_LEVEL, "--lib", android_jar, "--output", dex_tmp, classes_jar)
        dex_files = sorted(dex_tmp.glob("classes*.dex"))
        if not dex_files:
            skipped_no_classes.append(entry["module"])
            shutil.rmtree(art_dir)
            continue
        for dex_file in dex_files:
            shutil.move(str(dex_file), str(art_dir / dex_file.name))
        dex_count += len(dex_files)
        artifact_count += 1
        packaged.append({
            "id": aid,
            "module": entry["module"],
            "group": group,
            "name": name,
            "version": version,
            "type": "aar" if src.suffix.lower() == ".aar" else "jar",
            "dex": [f.name for f in sorted(art_dir.glob("classes*.dex"))],
            "hasRes": (art_dir / "res").is_dir(),
            "hasAssets": (art_dir / "assets").is_dir(),
            "hasManifest": (art_dir / "AndroidManifest.xml").is_file(),
            "hasProguard": (art_dir / "proguard.txt").is_file(),
        })

    # If an artifact has no res/ or assets/, the folder simply contains
    # classes.jar + classes*.dex (+ optional consumer rules / manifest for
    # AARs that ship them).
    archive = OUT / "compose-libs.zip"
    if archive.exists(): archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for art_dir in sorted(artifacts_root.iterdir()):
            if not art_dir.is_dir():
                continue
            for path in art_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(artifacts_root))

    print(f"Wrote {archive}")

    # Machine-readable manifest: what's in the zip, what toolchain produced it,
    # and what the host app has to bring itself.
    manifest = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "toolchain": {
            "gradle": GRADLE_VERSION,
            "androidGradlePlugin": AGP_VERSION,
            "kotlin": KOTLIN_VERSION,
            "kotlinxCoroutines": COROUTINES_VERSION,
            "composeBom": COMPOSE_BOM_VERSION,
            "compileSdk": int(COMPILE_SDK_LEVEL),
            "minSdk": int(MIN_SDK_LEVEL),
            "androidJar": str(android_jar),
        },
        # NOT inside compose-libs.zip -- the app must already ship these, at
        # these versions or newer, or you get the classic version mismatch.
        "hostProvided": [
            {"module": f"{key}:{version}", "group": key.split(":")[0], "name": key.split(":")[1], "version": version}
            for key, version in sorted(host_provided.items())
        ],
        "libraryCount": len(packaged),
        "libraries": sorted(packaged, key=lambda item: item["module"]),
    }
    manifest_path = OUT / "compose-libraries.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {manifest_path}")

    print(f"Artifacts: {artifact_count}, DEX files: {dex_count}, Duplicates removed: {duplicate_count}")
    print(
        "Toolchain: Gradle {gradle} / AGP {agp} / Kotlin {kotlin} / coroutines {coroutines} / "
        "compose-bom {bom} / compileSdk {sdk} / minSdk {min_sdk}".format(
            gradle=GRADLE_VERSION, agp=AGP_VERSION, kotlin=KOTLIN_VERSION,
            coroutines=COROUTINES_VERSION, bom=COMPOSE_BOM_VERSION,
            sdk=COMPILE_SDK_LEVEL, min_sdk=MIN_SDK_LEVEL,
        )
    )
    if host_provided:
        print("Host app must provide (not bundled): " + ", ".join(f"{k}:{v}" for k, v in sorted(host_provided.items())))
    if skipped_no_classes:
        print(f"Skipped (no bytecode): {len(skipped_no_classes)}")


if __name__ == "__main__": main()
