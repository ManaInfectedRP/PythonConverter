"""Convert MP3 files to MP4 videos using embedded album art as a static frame.

Usage:
    python convert.py INPUT [-o OUTPUT] [--default-image PATH]
                      [--resolution WxH] [--recursive] [--overwrite]

INPUT may be a single .mp3 file or a directory containing .mp3 files.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional

import imageio_ffmpeg
from mutagen.id3 import ID3, ID3NoHeaderError
from PIL import Image


DEFAULT_RESOLUTION = (1280, 720)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert MP3 files to MP4 videos using embedded album art."
    )
    p.add_argument("input", type=Path, help="MP3 file or directory of MP3 files.")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .mp4 path (single file) or output directory (folder mode).",
    )
    p.add_argument(
        "--default-image",
        type=Path,
        default=None,
        help="Image used when an MP3 has no embedded art.",
    )
    p.add_argument(
        "--resolution",
        default="1920x1080",
        help=(
            "Target output resolution WxH (default 1920x1080). Smaller images "
            "are upscaled (LANCZOS) and letterboxed to fit; larger images keep "
            "their native size."
        ),
    )
    p.add_argument(
        "--crf",
        type=int,
        default=18,
        help="libx264 CRF quality (lower = sharper; 18 near-lossless).",
    )
    p.add_argument(
        "--preset",
        default="medium",
        help="libx264 preset (ultrafast..veryslow). Slower = better quality per bit.",
    )
    p.add_argument(
        "--fps",
        type=int,
        default=2,
        help="Output framerate. 2 fps is plenty for a still image (smaller file).",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="When input is a directory, walk subdirectories.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files.",
    )
    return p.parse_args()


def parse_resolution(s: str) -> tuple[int, int]:
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception:
        raise SystemExit(f"Invalid --resolution {s!r}. Expected WxH, e.g. 1280x720.")


def extract_cover(mp3_path: Path) -> Optional[bytes]:
    """Return bytes of first embedded APIC cover image, else None."""
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        return None
    except Exception:
        return None
    for key in tags.keys():
        if key.startswith("APIC"):
            frame = tags.get(key)
            if frame is not None and getattr(frame, "data", None):
                return frame.data
    return None


def _normalize_even(img: Image.Image) -> Image.Image:
    """libx264 requires even width/height. Pad by 1 px as needed."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    new_w = w + (w % 2)
    new_h = h + (h % 2)
    if (new_w, new_h) == (w, h):
        return img
    padded = Image.new("RGB", (new_w, new_h), (0, 0, 0))
    padded.paste(img, (0, 0))
    return padded


def _fit_to_resolution(
    img: Image.Image, target: tuple[int, int]
) -> Image.Image:
    """Scale image to fit target resolution preserving aspect ratio.

    Pads with black (letterbox/pillarbox) so final image is exactly target size.
    Uses LANCZOS for high-quality resampling. Skips scaling when the source
    is already >= target on both axes (no-op keeps native sharpness).
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    tw, th = target
    # Ensure target dims are even.
    tw += tw % 2
    th += th % 2
    sw, sh = img.size
    if sw >= tw and sh >= th:
        # Source already meets or exceeds target; don't downscale, just
        # ensure even dims.
        return _normalize_even(img)
    # Scale up preserving aspect ratio.
    scale = min(tw / sw, th / sh)
    new_w = max(2, int(round(sw * scale)))
    new_h = max(2, int(round(sh * scale)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th), (0, 0, 0))
    canvas.paste(resized, ((tw - new_w) // 2, (th - new_h) // 2))
    return canvas


SIBLING_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def find_sibling_image(mp3_path: Path) -> Optional[Path]:
    """Look for an image file next to the MP3 with the same stem."""
    for ext in SIBLING_IMAGE_EXTS:
        cand = mp3_path.with_suffix(ext)
        if cand.exists():
            return cand
        cand_upper = mp3_path.with_suffix(ext.upper())
        if cand_upper.exists():
            return cand_upper
    return None


def prepare_cover_image(
    cover_bytes: Optional[bytes],
    sibling_image_path: Optional[Path],
    default_image_path: Optional[Path],
    resolution: tuple[int, int],
) -> Path:
    """Produce a temp PNG suitable as ffmpeg image input. Caller deletes."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.close()
    out = Path(tmp.name)

    if cover_bytes:
        img = Image.open(io.BytesIO(cover_bytes))
    elif sibling_image_path is not None:
        img = Image.open(sibling_image_path)
    elif default_image_path is not None:
        img = Image.open(default_image_path)
    else:
        img = Image.new("RGB", resolution, (0, 0, 0))

    img = _fit_to_resolution(img, resolution)
    img.save(out, format="PNG", compress_level=1)
    return out


def run_ffmpeg(
    ffmpeg_exe: str,
    image_path: Path,
    mp3_path: Path,
    out_path: Path,
    overwrite: bool,
    crf: int = 18,
    preset: str = "medium",
    fps: int = 2,
) -> None:
    cmd = [ffmpeg_exe]
    cmd += ["-y"] if overwrite else ["-n"]
    cmd += [
        "-loop", "1",
        "-framerate", str(fps),
        "-i", str(image_path),
        "-i", str(mp3_path),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({result.returncode}) for {mp3_path}:\n{result.stderr}"
        )


def convert_one(
    ffmpeg_exe: str,
    mp3_path: Path,
    out_path: Path,
    default_image: Optional[Path],
    resolution: tuple[int, int],
    overwrite: bool,
    crf: int = 18,
    preset: str = "medium",
    fps: int = 2,
) -> None:
    if out_path.exists() and not overwrite:
        print(f"SKIP  {mp3_path}  (output exists: {out_path})")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)

    cover = extract_cover(mp3_path)
    sibling = None if cover else find_sibling_image(mp3_path)
    image_path = prepare_cover_image(cover, sibling, default_image, resolution)
    try:
        run_ffmpeg(
            ffmpeg_exe,
            image_path,
            mp3_path,
            out_path,
            overwrite,
            crf=crf,
            preset=preset,
            fps=fps,
        )
        if cover:
            src = "embedded art"
        elif sibling:
            src = f"sibling image ({sibling.name})"
        elif default_image:
            src = "default image"
        else:
            src = "black frame"
        print(f"OK    {mp3_path} -> {out_path}  [{src}]")
    finally:
        try:
            image_path.unlink()
        except OSError:
            pass


def iter_mp3s(path: Path, recursive: bool) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    if path.is_dir():
        pattern = "**/*.mp3" if recursive else "*.mp3"
        for p in sorted(path.glob(pattern)):
            if p.is_file():
                yield p
        return
    raise SystemExit(f"Input not found: {path}")


def resolve_output(
    mp3_path: Path, input_root: Path, output_arg: Optional[Path], batch: bool
) -> Path:
    if output_arg is None:
        return mp3_path.with_suffix(".mp4")
    if batch:
        # Preserve relative layout under output dir.
        rel = mp3_path.relative_to(input_root) if input_root.is_dir() else mp3_path.name
        return (output_arg / rel).with_suffix(".mp4")
    return output_arg


def main() -> int:
    args = parse_args()
    resolution = parse_resolution(args.resolution)

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 2

    if args.default_image is not None and not args.default_image.exists():
        print(f"--default-image not found: {args.default_image}", file=sys.stderr)
        return 2

    batch = args.input.is_dir()
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    failures = 0
    total = 0
    for mp3 in iter_mp3s(args.input, args.recursive):
        total += 1
        out_path = resolve_output(mp3, args.input, args.output, batch)
        try:
            convert_one(
                ffmpeg_exe=ffmpeg_exe,
                mp3_path=mp3,
                out_path=out_path,
                default_image=args.default_image,
                resolution=resolution,
                overwrite=args.overwrite,
                crf=args.crf,
                preset=args.preset,
                fps=args.fps,
            )
        except Exception as e:
            failures += 1
            print(f"FAIL  {mp3}: {e}", file=sys.stderr)

    print(f"\nDone. {total - failures}/{total} succeeded.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
