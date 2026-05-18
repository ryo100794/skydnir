#!/usr/bin/env python3
"""Host-only release-readiness checks for source, secrets, and binary inventory."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from fnmatch import fnmatch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL_NOTICE = ROOT / "THIRD_PARTY_NOTICES.md"
APK_NOTICE_SOURCE = ROOT / "app" / "src" / "main" / "assets" / "oss-licenses" / "THIRD_PARTY_NOTICES.md"
APK_NOTICE_ENTRY = "assets/oss-licenses/THIRD_PARTY_NOTICES.md"
FDROID_DOC = ROOT / "docs" / "release" / "FDROID_RELEASE_PROCESS.md"
METADATA_README = ROOT / "metadata" / "fdroid" / "README.md"
INVENTORY = ROOT / "metadata" / "fdroid" / "generated-binary-inventory.md"

PAYLOAD_DIRS = (
    ROOT / "app" / "src" / "main" / "assets" / "pdockerd",
    ROOT / "app" / "src" / "main" / "jniLibs",
    ROOT / "app" / "src" / "compat" / "jniLibs",
    ROOT / "docker-proot-setup" / "bin",
    ROOT / "docker-proot-setup" / "docker-bin",
    ROOT / "docker-proot-setup" / "lib",
)

SKIP_DIR_NAMES = {".git", ".gradle", ".idea", "__pycache__", "build"}

SECRET_NAME_RE = re.compile(
    r"(^|/)(?:keystore|release-signing|signing)\.properties$|"
    r"\.(?:jks|keystore|p12|pem|key|crt)$",
    re.IGNORECASE,
)
SECRET_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}

LEGACY_FORBIDDEN_APK_ENTRIES = (
    "lib/arm64-v8a/libproot.so",
    "lib/arm64-v8a/libproot-loader.so",
    "lib/arm64-v8a/libtalloc.so",
    "lib/arm64-v8a/libdocker.so",
    "lib/arm64-v8a/libdocker-compose.so",
)

APK_NOTICE_TOKENS = (
    "go-containerregistry",
    "xterm.js",
    "xterm-addon-fit",
    "Chaquopy",
    "CPython",
    "Python 3.11",
    "OpenSSL",
    "SQLite",
    "certificate",
    "AndroidX",
    "Material Components",
    "Kotlin",
)

APK_RESOLVED_PAYLOAD_BUCKETS = (
    ("lib/arm64-v8a/libchaquopy_java.so", ("Chaquopy",)),
    ("lib/arm64-v8a/libpython3.11.so", ("CPython", "Python 3.11")),
    ("lib/arm64-v8a/libcrypto_chaquopy.so", ("OpenSSL",)),
    ("lib/arm64-v8a/libssl_chaquopy.so", ("OpenSSL",)),
    ("lib/arm64-v8a/libsqlite3_chaquopy.so", ("SQLite",)),
    ("assets/chaquopy/cacert.pem", ("certificate",)),
    ("assets/chaquopy/*.imy", ("Chaquopy", "CPython")),
    ("assets/chaquopy/bootstrap-native/arm64-v8a/*.so", ("Chaquopy", "CPython")),
)


class CheckFailure(Exception):
    pass


def fail(message: str) -> None:
    raise CheckFailure(message)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read(path: Path) -> str:
    if not path.is_file():
        fail(f"missing required file: {rel(path)}")
    return path.read_text(encoding="utf-8")


def git_list(args: list[str]) -> list[Path]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [ROOT / item for item in result.stdout.decode().split("\0") if item]


def candidate_files() -> list[Path]:
    try:
        files = git_list(["ls-files", "-z"])
        files.extend(git_list(["ls-files", "--others", "--exclude-standard", "-z"]))
        return sorted(set(files))
    except (subprocess.CalledProcessError, FileNotFoundError):
        found: list[Path] = []
        for root, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
            found.extend(Path(root) / name for name in names)
        return sorted(found)


def is_binary_file(path: Path) -> bool:
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\0" in data


def looks_like_payload(path: Path) -> bool:
    if not path.is_file():
        return False
    if not any(path == payload_dir or payload_dir in path.parents for payload_dir in PAYLOAD_DIRS):
        return False
    try:
        head = path.read_bytes()[:4]
    except OSError:
        return False
    return head == b"\x7fELF" or rel(path) == "app/src/main/assets/pdockerd/pdockerd"


def check_docs() -> None:
    fdroid = read(FDROID_DOC)
    metadata = read(METADATA_README)
    required_fdroid_tokens = (
        "does not claim that pdocker-android is ready for F-Droid submission",
        "user explicitly selected",
        "The app does not silently extend the APK",
        "Issue #9",
        "release candidate",
        "host-only",
        "Storage metrics release evidence",
    )
    for token in required_fdroid_tokens:
        if token not in fdroid:
            fail(f"{rel(FDROID_DOC)} missing required release-readiness language: {token!r}")

    if "does not claim that\npdocker-android is ready for inclusion" not in metadata:
        fail(f"{rel(METADATA_README)} must remain an inactive metadata placeholder")

    forbidden_claims = (
        "ready for F-Droid submission",
        "ready for inclusion in F-Droid",
        "F-Droid ready",
    )
    combined = fdroid + "\n" + metadata
    for claim in forbidden_claims:
        allowed = f"does not claim that pdocker-android is {claim}"
        if claim in combined and allowed not in combined:
            fail(f"forbidden readiness claim found: {claim!r}")


def check_metadata_placeholder() -> None:
    active_metadata = sorted(
        p for p in (ROOT / "metadata" / "fdroid").glob("*")
        if p.suffix.lower() in {".yml", ".yaml"} or p.name.endswith(".txt")
    )
    if active_metadata:
        names = ", ".join(rel(p) for p in active_metadata)
        fail(f"metadata/fdroid must stay placeholder-only for now; active metadata found: {names}")


def parse_inventory_paths() -> set[str]:
    text = read(INVENTORY)
    paths: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|", 2)[1].strip()
        if first_cell.startswith("`") and first_cell.endswith("`"):
            paths.add(first_cell.strip("`"))
    if not paths:
        fail(f"{rel(INVENTORY)} has no payload rows")
    if "not F-Droid submission metadata" not in text:
        fail(f"{rel(INVENTORY)} must say it is not F-Droid submission metadata")
    return paths


def check_payload_inventory() -> None:
    inventory_paths = parse_inventory_paths()
    payloads = sorted(rel(path) for base in PAYLOAD_DIRS for path in base.rglob("*") if looks_like_payload(path))
    missing = [path for path in payloads if path not in inventory_paths]
    if missing:
        fail("payloads missing from generated/prebuilt inventory: " + ", ".join(missing))
    stale = sorted(
        path
        for path in inventory_paths
        if not path.startswith("APK:")
        and "*" not in path
        and not (ROOT / path).exists()
    )
    if stale:
        fail("inventory entries point to missing files: " + ", ".join(stale))

    source = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
    staged = ROOT / "app" / "src" / "main" / "assets" / "pdockerd" / "pdockerd"
    if source.is_file() and staged.is_file() and source.read_bytes() != staged.read_bytes():
        fail("staged pdockerd asset differs from docker-proot-setup/bin/pdockerd")


def find_built_apks() -> list[Path]:
    override = os.environ.get("PDOCKER_APK_NOTICE_AUDIT_PATHS")
    if override:
        return [Path(item) for item in override.split(os.pathsep) if item]
    apk_root = ROOT / "app" / "build" / "outputs" / "apk"
    if not apk_root.exists():
        return []
    return sorted(path for path in apk_root.rglob("*.apk") if path.is_file())


def has_token(text: str, token: str) -> bool:
    return token.lower() in text.lower()


def require_tokens(text: str, tokens: tuple[str, ...], where: str) -> None:
    missing = [token for token in tokens if not has_token(text, token)]
    if missing:
        fail(f"{where} missing notice/inventory tokens: {', '.join(missing)}")


def matches_apk_pattern(entry: str, pattern: str) -> bool:
    return entry == pattern or fnmatch(entry, pattern)


def check_apk_notice_audit(
    required: bool = False,
    apk_paths: list[Path] | None = None,
    notice_source_text: str | None = None,
    top_notice_text: str | None = None,
    inventory_text: str | None = None,
) -> None:
    """Verify third-party notices against resolved APK contents.

    The source-tree inventory covers staged binaries, but Gradle/Chaquopy add
    package-resolved runtime payloads only at APK build time.  This audit keeps
    those APK-only payloads tied to the shipped notice asset and the release
    inventory so a later packaging change cannot silently add third-party code.
    """

    apks = list(apk_paths) if apk_paths is not None else find_built_apks()
    if not apks:
        if required:
            fail("APK notice audit required but no built APKs were found")
        return

    source_notice = notice_source_text if notice_source_text is not None else read(APK_NOTICE_SOURCE)
    root_notice = top_notice_text if top_notice_text is not None else read(TOP_LEVEL_NOTICE)
    inventory = inventory_text if inventory_text is not None else read(INVENTORY)

    require_tokens(source_notice, APK_NOTICE_TOKENS, rel(APK_NOTICE_SOURCE))
    require_tokens(root_notice, APK_NOTICE_TOKENS, rel(TOP_LEVEL_NOTICE))

    for apk in apks:
        try:
            with zipfile.ZipFile(apk) as archive:
                names = set(archive.namelist())
                if APK_NOTICE_ENTRY not in names:
                    fail(f"{apk} missing {APK_NOTICE_ENTRY}")
                apk_notice = archive.read(APK_NOTICE_ENTRY).decode("utf-8")
        except zipfile.BadZipFile:
            fail(f"{apk} is not a readable APK/ZIP archive")
        except UnicodeDecodeError:
            fail(f"{apk}:{APK_NOTICE_ENTRY} is not UTF-8")

        if apk_notice.strip() != source_notice.strip():
            fail(f"{apk}:{APK_NOTICE_ENTRY} is stale or differs from {rel(APK_NOTICE_SOURCE)}")
        require_tokens(apk_notice, APK_NOTICE_TOKENS, f"{apk}:{APK_NOTICE_ENTRY}")

        forbidden = sorted(entry for entry in LEGACY_FORBIDDEN_APK_ENTRIES if entry in names)
        if forbidden:
            fail(f"{apk} contains forbidden legacy/upstream payloads: {', '.join(forbidden)}")

        for pattern, tokens in APK_RESOLVED_PAYLOAD_BUCKETS:
            matched = sorted(entry for entry in names if matches_apk_pattern(entry, pattern))
            if not matched:
                continue
            require_tokens(apk_notice, tokens, f"{apk}:{APK_NOTICE_ENTRY} for {pattern}")
            require_tokens(root_notice, tokens, f"{rel(TOP_LEVEL_NOTICE)} for {pattern}")
            require_tokens(inventory, tokens, f"{rel(INVENTORY)} for {pattern}")


def check_secret_filenames(files: list[Path]) -> None:
    offenders = [rel(path) for path in files if SECRET_NAME_RE.search(rel(path))]
    if offenders:
        fail("secret/signing material is not ignored or is tracked: " + ", ".join(offenders))


def check_secret_content(files: list[Path]) -> None:
    offenders: list[str] = []
    for path in files:
        if not path.is_file() or is_binary_file(path):
            continue
        relative = rel(path)
        if any(part in SKIP_DIR_NAMES for part in path.relative_to(ROOT).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                offenders.append(f"{relative} ({label})")
    if offenders:
        fail("possible committed secret content: " + ", ".join(offenders))


def main() -> int:
    try:
        files = candidate_files()
        check_docs()
        check_metadata_placeholder()
        check_payload_inventory()
        check_apk_notice_audit(os.environ.get("PDOCKER_REQUIRE_APK_NOTICE_AUDIT") == "1")
        check_secret_filenames(files)
        check_secret_content(files)
    except CheckFailure as exc:
        print(f"verify-release-readiness: FAIL: {exc}", file=sys.stderr)
        return 1
    print("verify-release-readiness: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
