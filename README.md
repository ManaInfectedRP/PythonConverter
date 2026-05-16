# PythonConverter

Converts MP3 files to MP4 video. Two modes:

- **Album-art mode** — uses embedded APIC cover art (or sibling image / black frame) as a static video frame.
- **Background-video mode** — loops an existing MP4 as the video track for the full duration of the audio, with optional watermark removal.

---

## GUI app (recommended)

### Build the exe

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install tkinterdnd2 pyinstaller

pyinstaller --onefile --windowed --name "MP3toMP4" `
    --collect-all tkinterdnd2 `
    --collect-all imageio_ffmpeg `
    app.py
```

The finished exe is at `dist\MP3toMP4.exe`. Copy it anywhere — ffmpeg and all Python dependencies are bundled inside.

### Using the GUI

1. **Drop MP3 files** onto the top drop zone. Output goes to `output\` next to the exe.
2. *(Optional)* **Add a background video** — drop an `.mp4` onto the *Background Video* section, or click **Browse…**.
   - The first frame of the video is shown as a live preview.
   - **Draw a selection box** over any watermark on the preview frame. The app scales the canvas selection back to the actual video resolution and removes it via ffmpeg's `delogo` filter.
   - Right-click the preview to clear the selection.
3. Drop your MP3s — conversion starts immediately. Progress bar and log show status in real time.

Converted files are placed in `output\` next to the exe, named `<original stem>.mp4`.

---

## CLI tool (`convert.py`)

### Install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

ffmpeg is bundled via `imageio-ffmpeg`. No system install needed.

### Usage

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

No embedded art and no `--default-image` → solid black frame at target resolution.

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `input` | — | MP3 file or directory. |
| `-o, --output` | next to input | Output file (single) or output dir (batch). |
| `--default-image` | — | Fallback image when no embedded APIC art. |
| `--resolution` | `1920x1080` | Black-frame / upscale target size `WxH`. |
| `--crf` | `18` | libx264 quality (lower = sharper; 18 ≈ near-lossless). |
| `--preset` | `medium` | libx264 speed preset (`ultrafast` → `veryslow`). |
| `--fps` | `2` | Output framerate (2 fps is plenty for a still image). |
| `--recursive` | off | Walk subdirectories in folder mode. |
| `--overwrite` | off | Overwrite existing MP4s. |

---

## How it works

### Album-art mode
1. Read ID3 `APIC` frame via `mutagen`. First frame wins.
2. Pillow normalises the image: convert to RGB, scale to fit target resolution with LANCZOS (letterbox/pillarbox), pad to even dimensions (libx264 requirement).
3. ffmpeg encodes with `-loop 1 -map 0:v:0 -map 1:a:0 -vf scale=trunc(iw/2)*2:trunc(ih/2)*2 -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest -movflags +faststart`.
4. `-shortest` stops at audio end → output duration == MP3 duration.

### Background-video mode
1. Both the MP3 and background video are probed for duration via ffmpeg stderr.
2. Loop count is calculated as `ceil(audio_duration / video_duration) + 1` to ensure full coverage.
3. A finite `-stream_loop N` is used instead of `-1` — infinite loop silently produces 0 frames with HEVC/AV1 content in ffmpeg 7.x.
4. If watermark removal is active, ffmpeg's `delogo` filter blurs the selected region using neighbouring pixel estimation before encoding.
5. `scale=trunc(iw/2)*2:trunc(ih/2)*2` is always applied to force even dimensions regardless of source video resolution.

### Stream mapping
Both modes explicitly use `-map 0:v:0 -map 1:a:0` to prevent ffmpeg 7.x from auto-selecting the MP3's embedded album art as a second video stream, which caused `Could not open encoder before EOF` on affected files.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `mutagen` | ID3 tag / APIC cover art extraction |
| `Pillow` | Image resize, pad, format conversion |
| `imageio-ffmpeg` | Bundled ffmpeg binary (no system install) |
| `tkinterdnd2` | Drag-and-drop support in the GUI |
| `pyinstaller` | Build single-file Windows exe |
