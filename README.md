# PythonConverter

CLI tool. Convert MP3 to MP4. Use embedded album art as static video frame. Video length = MP3 length.

## Install

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

ffmpeg bundled via `imageio-ffmpeg`. No system install needed.

## Usage

Single file:

```
python convert.py song.mp3
```

Output: `song.mp4` next to input.

Custom output path:

```
python convert.py song.mp3 -o out.mp4
```

Folder (non-recursive):

```
python convert.py .\Music -o .\out
```

Folder recursive:

```
python convert.py .\Music -o .\out --recursive
```

Fallback image when MP3 has no embedded art:

```
python convert.py song.mp3 --default-image cover.png
```

No embedded art and no `--default-image` → solid black frame at `--resolution` (default `1280x720`).

## Flags

| Flag | Description |
|------|-------------|
| `input` | MP3 file or directory. |
| `-o, --output` | Output file (single) or output dir (batch). |
| `--default-image` | Fallback image when no embedded APIC art. |
| `--resolution` | Black-frame size `WxH`, default `1280x720`. |
| `--recursive` | Walk subdirectories in folder mode. |
| `--overwrite` | Overwrite existing MP4s. |

## How it works

1. Read ID3 APIC frame via `mutagen`. First frame wins.
2. Pillow normalizes image: RGB, pad to even dimensions (libx264 requirement).
3. ffmpeg: `-loop 1 -i image -i mp3 -c:v libx264 -tune stillimage -pix_fmt yuv420p -c:a aac -b:a 192k -shortest -movflags +faststart`.
4. `-shortest` stops at audio end → video duration == mp3 duration.
# PythonConverter
