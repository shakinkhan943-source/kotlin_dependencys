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
        lib_dir.mkdir(parents=True, exist_ok=True)
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
                            out = lib_dir / nm
                            out.parent.mkdir(parents=True, exist_ok=True)
                            out.write_bytes(zf.read(nm))
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
