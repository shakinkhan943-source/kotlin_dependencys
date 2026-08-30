"""Build a curated Android-only Jetpack Compose dependency bundle.

Produces:
  output/compose-libs.zip  - per-artifact folder layout:
        <id>/classes.jar         - compile-time jar
        <id>/classes.dex         - pre-dexed runtime bytecode (classes2.dex, ... if multi-dex)
        <id>/res/...             - extracted AAR resources
        <id>/assets/...          - extracted AAR assets
        <id>/proguard.txt        - consumer ProGuard rules, if any
        <id>/AndroidManifest.xml - AAR manifest (for manifest merger), if any
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
ANDROID_PLATFORM = os.environ.get("ANDROID_COMPILE_SDK", "android-36")

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

    # Per-artifact staging directory. Each artifact gets its own folder
    # named by its <id> and containing classes.jar, classes*.dex, and (when
    # the AAR ships them) res/, assets/, proguard.txt, AndroidManifest.xml.
    artifacts_root = WORK / "artifacts"
    artifacts_root.mkdir(parents=True)

    classes_tmp = WORK / "classes-tmp"
    classes_tmp.mkdir(parents=True)

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

    artifact_count = 0
    dex_count = 0
    skipped_no_classes = []

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
        run(d8, "--min-api", "23", "--lib", android_jar, "--output", dex_tmp, classes_jar)
        dex_files = sorted(dex_tmp.glob("classes*.dex"))
        if not dex_files:
            skipped_no_classes.append(entry["module"])
            shutil.rmtree(art_dir)
            continue
        for dex_file in dex_files:
            shutil.move(str(dex_file), str(art_dir / dex_file.name))
        dex_count += len(dex_files)
        artifact_count += 1

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
    print(f"Artifacts: {artifact_count}, DEX files: {dex_count}, Duplicates removed: {duplicate_count}")
    if skipped_no_classes:
        print(f"Skipped (no bytecode): {len(skipped_no_classes)}")


if __name__ == "__main__": main()
