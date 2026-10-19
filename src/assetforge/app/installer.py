"""Build AssetForge.app: compile the Swift shell, write Info.plist/launch.json, draw the icon, ad-hoc sign.

The bundle runs this checkout's virtualenv Python (paths in Contents/Resources/launch.json), so it
always uses your current code and settings; re-run `assetforge app install` if the repo moves.
"""

from __future__ import annotations

import json
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from assetforge import __version__
from assetforge.errors import InvalidInputError, StorageError

BUNDLE_ID = "com.ideaclan.assetforge"
MIN_MACOS = "13.0"
ICON_SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]


def project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "macos").is_dir():
            return parent
    raise StorageError("Couldn't find the AssetForge checkout (pyproject.toml + macos/) above the installed package",
                       hint="Run `assetforge app install` from a source checkout (uv run assetforge app install)")  # fmt: skip


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise InvalidInputError(f"`{name}` was not found", hint="Install Xcode or the Command Line Tools: xcode-select --install")
    return found


def compile_shell(source: Path, output: Path) -> None:
    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    cmd = [_tool("swiftc"), "-O", "-target", f"{arch}-apple-macosx{MIN_MACOS}", "-framework", "Cocoa",
           "-framework", "WebKit", "-o", str(output), str(source)]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StorageError("Compiling the Mac app failed", hint="See `output` for the compiler messages",
                           output=(proc.stderr or proc.stdout)[-4000:])  # fmt: skip


def draw_icon(size: int = 1024) -> Image.Image:
    """A forge spark on an anvil, on the app's orange→magenta→violet squircle."""
    s = size * 2  # supersample for smooth edges
    gradient = Image.new("RGBA", (s, s))
    stops = [(0.0, (240, 162, 59)), (0.55, (226, 86, 43)), (1.0, (122, 43, 209))]
    px = gradient.load()
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s)
            for (t0, c0), (t1, c1) in zip(stops, stops[1:], strict=False):
                if t0 <= t <= t1:
                    k = (t - t0) / (t1 - t0)
                    px[x, y] = (*(round(a + (b - a) * k) for a, b in zip(c0, c1, strict=True)), 255)
                    break
    margin = int(s * 0.1)  # Apple's icon grid leaves ~10% around the squircle
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((margin, margin, s - margin, s - margin), radius=int(s * 0.18), fill=255)
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((margin, margin + s // 60, s - margin, s - margin + s // 60),
                                             radius=int(s * 0.18), fill=(0, 0, 0, 90))  # fmt: skip
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s // 80)))
    icon.paste(gradient, (0, 0), mask)

    d = ImageDraw.Draw(icon)
    white = (255, 255, 255, 255)
    u = s / 100
    # anvil: face with a horn on the left, waist, and foot
    d.polygon([(22 * u, 52 * u), (34 * u, 46 * u), (76 * u, 46 * u), (76 * u, 56 * u), (64 * u, 58 * u),
               (60 * u, 64 * u), (40 * u, 64 * u), (36 * u, 58 * u), (30 * u, 56 * u)], fill=white)  # fmt: skip
    d.rectangle((42 * u, 63 * u, 58 * u, 70 * u), fill=white)
    d.rounded_rectangle((32 * u, 69 * u, 68 * u, 76 * u), radius=2 * u, fill=white)
    # spark: four-point star above the anvil plus two embers
    cx, cy, r = 54 * u, 29 * u, 13 * u
    d.polygon([(cx, cy - r), (cx + r * 0.22, cy - r * 0.22), (cx + r, cy), (cx + r * 0.22, cy + r * 0.22),
               (cx, cy + r), (cx - r * 0.22, cy + r * 0.22), (cx - r, cy), (cx - r * 0.22, cy - r * 0.22)],
              fill=(255, 241, 196, 255))  # fmt: skip
    for ex, ey, er in ((70 * u, 36 * u, 2.4 * u), (40 * u, 34 * u, 1.8 * u)):
        d.ellipse((ex - er, ey - er, ex + er, ey + er), fill=(255, 241, 196, 255))
    return icon.resize((size, size), Image.Resampling.LANCZOS)


def write_icns(target: Path) -> None:
    base = draw_icon(1024)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for points, scale in ICON_SIZES:
            px = points * scale
            name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
            base.resize((px, px), Image.Resampling.LANCZOS).save(iconset / name)
        proc = subprocess.run([_tool("iconutil"), "-c", "icns", str(iconset), "-o", str(target)],
                              capture_output=True, text=True)  # fmt: skip
        if proc.returncode != 0:
            raise StorageError("iconutil failed", output=proc.stderr[-2000:])


def install(dest: Path, *, name: str = "AssetForge", python: Path | None = None, repo: Path | None = None) -> dict[str, Any]:
    """Build `<dest>/<name>.app` (replacing an existing one) and return what was installed."""
    if sys.platform != "darwin":
        raise InvalidInputError("The AssetForge app is macOS only; use `assetforge ui --browser` elsewhere")
    repo = (repo or project_root()).resolve()
    python = Path(python or sys.executable)  # keep the venv path: resolving the symlink would lose the venv
    if not python.exists():
        raise InvalidInputError(f"Python not found: {python}")
    source = repo / "macos" / "AssetForgeApp.swift"
    if not source.is_file():
        raise StorageError(f"Missing {source}")
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(dir=dest, prefix=f".{name}-build-"))
    try:
        app = staging / f"{name}.app"
        macos_dir = app / "Contents" / "MacOS"
        resources = app / "Contents" / "Resources"
        macos_dir.mkdir(parents=True)
        resources.mkdir(parents=True)
        compile_shell(source, macos_dir / name)
        info = {
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": name,
            "CFBundleDisplayName": name,
            "CFBundleExecutable": name,
            "CFBundlePackageType": "APPL",
            "CFBundleIconFile": "AppIcon",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": MIN_MACOS,
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "NSHumanReadableCopyright": "AssetForge",
        }
        with (app / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump(info, handle)
        (resources / "launch.json").write_text(json.dumps({"python": str(python), "repo": str(repo)}, indent=2))
        write_icns(resources / "AppIcon.icns")
        signed = False
        if shutil.which("codesign"):
            proc = subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(app)], capture_output=True, text=True)
            signed = proc.returncode == 0

        final = dest / f"{name}.app"
        backup = dest / f".{name}.app.old"
        if backup.exists():
            shutil.rmtree(backup)
        if final.exists():
            final.rename(backup)
        app.rename(final)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        # Tell LaunchServices about the (new) bundle so Spotlight/Dock pick up the icon immediately.
        lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
        if Path(lsregister).exists():
            subprocess.run([lsregister, "-f", str(final)], capture_output=True)
        return {"app": final, "python": python, "repo": repo, "signed": signed, "version": __version__}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
