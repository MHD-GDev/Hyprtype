#!/usr/bin/env python3
"""Install and validate Hyprtype's local Whisper.cpp runtime."""
from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = DATA_HOME / "hyprtype"
WHISPER_DIR = DATA_DIR / "whisper.cpp"
BUILD_DIR = WHISPER_DIR / "build"
WHISPER = BUILD_DIR / "bin" / "whisper-cli"
MODEL_DIR = DATA_DIR / "models"
MODEL = MODEL_DIR / "ggml-base.en.bin"
MODEL_STAMP = MODEL_DIR / ".base.en.verified"
MODEL_SHA1 = "137c40403d78fd54d454da0f9bd998f78703390c"
SOURCE_DIR = ROOT / "vendor" / "whisper.cpp"


def notify(message: str, urgency: str = "normal") -> None:
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", "Hyprtype", "-u", urgency, "Hyprtype", message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )


def library_environment() -> dict[str, str]:
    env = os.environ.copy()
    lib_dir = str(WHISPER.parent)
    env["LD_LIBRARY_PATH"] = lib_dir + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
    )
    return env


def model_is_valid(path: Path = MODEL) -> bool:
    if not path.is_file():
        return False
    stat = path.stat()
    if stat.st_size < 100_000_000:
        return False

    # Avoid hashing 142 MiB on every push-to-talk launch. Re-hash whenever
    # the installed file's size or modification time changes.
    if path == MODEL and MODEL_STAMP.is_file():
        try:
            stamp = MODEL_STAMP.read_text().split()
            if stamp == [MODEL_SHA1, str(stat.st_size), str(stat.st_mtime_ns)]:
                return True
        except OSError:
            pass

    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != MODEL_SHA1:
        MODEL_STAMP.unlink(missing_ok=True)
        return False
    if path == MODEL:
        MODEL_STAMP.write_text(f"{MODEL_SHA1} {stat.st_size} {stat.st_mtime_ns}\n")
    return True


def runtime_is_valid() -> bool:
    if not WHISPER.is_file() or not model_is_valid():
        return False
    try:
        result = subprocess.run(
            [str(WHISPER), "--help"], env=library_environment(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_build_tools(interactive: bool) -> None:
    required = {
        "git": "git",
        "cmake": "cmake",
        "c++": "base-devel",
        "make": "base-devel",
        "curl": "curl",
    }
    missing = [package for command, package in required.items() if not shutil.which(command)]
    if not missing:
        return
    packages = list(dict.fromkeys(missing))
    if not interactive or not sys.stdin.isatty():
        raise RuntimeError(
            "Missing build tools. Install them with: sudo pacman -S --needed "
            + " ".join(packages)
            + ". Then run hyprtype-setup again."
        )
    print("Hyprtype needs these Arch packages: " + " ".join(packages))
    answer = input("Install them now with pacman? [Y/n] ").strip().lower()
    if answer not in ("", "y", "yes"):
        raise RuntimeError("Dependency setup cancelled.")
    result = subprocess.run(["sudo", "pacman", "-S", "--needed", *packages], check=False)
    if result.returncode:
        raise RuntimeError("Package installation failed. Resolve the package error and retry.")
    missing_commands = [name for name in required if not shutil.which(name)]
    if missing_commands:
        raise RuntimeError("Required build tools are still unavailable: " + ", ".join(missing_commands))


def ensure_source() -> None:
    if (SOURCE_DIR / "CMakeLists.txt").is_file():
        return
    print("Fetching the pinned Whisper.cpp submodule...")
    result = subprocess.run(
        ["git", "submodule", "update", "--init", "--recursive", "--", "vendor/whisper.cpp"],
        cwd=ROOT, check=False,
    )
    if result.returncode or not (SOURCE_DIR / "CMakeLists.txt").is_file():
        raise RuntimeError(
            "Could not fetch vendor/whisper.cpp. Check your internet connection and "
            "the repository's Git submodule configuration, then retry."
        )


def install_model() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if model_is_valid():
        return

    # Reuse a valid model from older Hyprtype checkouts without downloading it again.
    legacy_model = SOURCE_DIR / "models" / "ggml-base.en.bin"
    if model_is_valid(legacy_model):
        shutil.copy2(legacy_model, MODEL)
        print(f"Using existing model: {MODEL}")
        return

    MODEL.unlink(missing_ok=True)
    downloader = SOURCE_DIR / "models" / "download-ggml-model.sh"
    if not downloader.is_file():
        raise RuntimeError("Whisper.cpp's model downloader is missing from the submodule.")
    print("Downloading the English base model (approximately 142 MiB)...")
    notify("Downloading the speech recognition model. This is a one-time setup.")
    result = subprocess.run(["sh", str(downloader), "base.en", str(MODEL_DIR)], check=False)
    if result.returncode or not model_is_valid():
        MODEL.unlink(missing_ok=True)
        raise RuntimeError(
            "The Whisper model download or SHA-1 verification failed. "
            "Check your connection and run hyprtype-setup again."
        )
    print(f"Model verified: {MODEL}")


def build_whisper() -> None:
    if runtime_binary_works():
        return
    ensure_source()

    # Migrate a previous in-project build, including its shared libraries.
    legacy_bin = SOURCE_DIR / "build" / "bin"
    legacy_executable = legacy_bin / "whisper-cli"
    if legacy_executable.is_file() and not WHISPER.exists():
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy_bin, WHISPER.parent, dirs_exist_ok=True)
        if runtime_binary_works():
            print(f"Migrated the existing Whisper.cpp build to {WHISPER.parent}")

    if runtime_binary_works():
        return
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    jobs = max(1, min(os.cpu_count() or 2, 4))
    print("Building Whisper.cpp. This can take a few minutes...")
    notify("Building the local speech recognition engine. This may take a few minutes.")
    configure = subprocess.run(
        ["cmake", "-S", str(SOURCE_DIR), "-B", str(BUILD_DIR),
         "-DCMAKE_BUILD_TYPE=Release", "-DWHISPER_BUILD_TESTS=OFF"],
        check=False,
    )
    if configure.returncode:
        raise RuntimeError("Whisper.cpp configuration failed. See the terminal output and retry.")
    build = subprocess.run(
        ["cmake", "--build", str(BUILD_DIR), "--config", "Release",
         "--target", "whisper-cli", f"-j{jobs}"],
        check=False,
    )
    if build.returncode or not runtime_binary_works():
        raise RuntimeError("Whisper.cpp build failed. See the terminal output and retry.")
    print(f"Whisper.cpp ready: {WHISPER}")


def runtime_binary_works() -> bool:
    if not WHISPER.is_file():
        return False
    try:
        result = subprocess.run(
            [str(WHISPER), "--help"], env=library_environment(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def cleanup_legacy_runtime() -> None:
    """Remove ignored in-checkout runtime artifacts after their data-dir copies work."""
    legacy_model = SOURCE_DIR / "models" / "ggml-base.en.bin"
    if legacy_model.is_file() and model_is_valid(legacy_model):
        legacy_model.unlink(missing_ok=True)

    legacy_build = SOURCE_DIR / "build"
    if legacy_build.is_dir() and runtime_binary_works():
        shutil.rmtree(legacy_build, ignore_errors=True)


def ensure_dependencies(interactive: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DATA_DIR / ".setup.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if runtime_is_valid():
            cleanup_legacy_runtime()
            return
        ensure_build_tools(interactive)
        ensure_source()
        build_whisper()
        install_model()
        if not runtime_is_valid():
            raise RuntimeError("Whisper.cpp setup did not pass its final validation.")
        cleanup_legacy_runtime()
    notify("Speech recognition is ready.")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Install or check Hyprtype's local Whisper runtime.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Check whether Whisper.cpp and its model are ready.")
    group.add_argument("--install", action="store_true", help="Install or repair Whisper.cpp and its model.")
    args = parser.parse_args()
    try:
        if args.check:
            if runtime_is_valid():
                print("Whisper.cpp and the base.en model are ready.")
                return 0
            print("Whisper.cpp or its model is missing or invalid.")
            return 1
        ensure_dependencies(interactive=True)
        print("Hyprtype speech recognition dependencies are ready.")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Hyprtype dependency setup failed: {exc}", file=sys.stderr)
        notify(str(exc), "critical")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
