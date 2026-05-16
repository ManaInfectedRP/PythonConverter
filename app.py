"""MP3 to MP4 converter - drag and drop GUI."""

from __future__ import annotations

import io
import math
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk
from typing import Optional

import imageio_ffmpeg
from mutagen.id3 import ID3
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD
import tkinter as tk


def get_exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def parse_dropped_files(data: str, ext: str) -> list[Path]:
    paths: list[Path] = []
    data = data.strip()
    i = 0
    while i < len(data):
        if data[i] == "{":
            end = data.index("}", i)
            paths.append(Path(data[i + 1 : end]))
            i = end + 1
        elif data[i] == " ":
            i += 1
        else:
            j = i
            while j < len(data) and data[j] != " ":
                j += 1
            paths.append(Path(data[i:j]))
            i = j
    return [p for p in paths if p.suffix.lower() == ext and p.exists()]


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

def _run_cmd(cmd: list[str]) -> None:
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        snippet = result.stderr[-1500:] if result.stderr else "(no stderr)"
        raise RuntimeError(f"ffmpeg error:\n{snippet}")


def probe_duration(ffmpeg_exe: str, path: Path) -> float:
    """Return media duration in seconds, or 0.0 on failure."""
    result = subprocess.run(
        [ffmpeg_exe, "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", result.stderr)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return 0.0


def probe_video_size(ffmpeg_exe: str, video_path: Path) -> Optional[tuple[int, int]]:
    result = subprocess.run(
        [ffmpeg_exe, "-i", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    m = re.search(r"Video:.*?\s(\d{2,5})x(\d{2,5})", result.stderr)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def extract_first_frame(ffmpeg_exe: str, video_path: Path) -> Optional[Path]:
    """Extract the first frame of a video to a temp PNG. Caller deletes."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.close()
    out = Path(tmp.name)
    try:
        _run_cmd([
            ffmpeg_exe, "-y", "-i", str(video_path),
            "-vframes", "1", "-f", "image2", str(out),
        ])
        return out
    except Exception:
        try:
            out.unlink()
        except OSError:
            pass
        return None


# ── conversion ────────────────────────────────────────────────────────────────

def extract_cover(mp3_path: Path) -> Optional[bytes]:
    try:
        tags = ID3(mp3_path)
    except Exception:
        return None
    for key in tags.keys():
        if key.startswith("APIC"):
            frame = tags.get(key)
            if frame is not None and getattr(frame, "data", None):
                return frame.data
    return None


SIBLING_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def find_sibling_image(mp3_path: Path) -> Optional[Path]:
    for ext in SIBLING_EXTS:
        for cand in (mp3_path.with_suffix(ext), mp3_path.with_suffix(ext.upper())):
            if cand.exists():
                return cand
    return None


def _fit_to_resolution(img: Image.Image, target: tuple[int, int]) -> Image.Image:
    if img.mode != "RGB":
        img = img.convert("RGB")
    tw, th = target[0] + target[0] % 2, target[1] + target[1] % 2
    sw, sh = img.size
    if sw >= tw and sh >= th:
        w, h = img.size
        pw, ph = w + w % 2, h + h % 2
        if (pw, ph) != (w, h):
            pad = Image.new("RGB", (pw, ph), (0, 0, 0))
            pad.paste(img, (0, 0))
            return pad
        return img
    scale = min(tw / sw, th / sh)
    nw, nh = max(2, round(sw * scale)), max(2, round(sh * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th), (0, 0, 0))
    canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def convert_image_mode(ffmpeg_exe: str, mp3_path: Path, out_path: Path) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cover = extract_cover(mp3_path)
    sibling = None if cover else find_sibling_image(mp3_path)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.close()
    img_tmp = Path(tmp.name)
    img = (Image.open(io.BytesIO(cover)) if cover
           else Image.open(sibling) if sibling
           else Image.new("RGB", (1920, 1080), (0, 0, 0)))
    _fit_to_resolution(img, (1920, 1080)).save(img_tmp, format="PNG", compress_level=1)

    try:
        _run_cmd([
            ffmpeg_exe, "-y",
            "-loop", "1", "-framerate", "2", "-i", str(img_tmp),
            "-i", str(mp3_path),
            # explicit mapping avoids ffmpeg 7.x picking the MP3's embedded art
            # as a second video stream and conflicting with the looped image
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", "2",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(out_path),
        ])
    finally:
        try:
            img_tmp.unlink()
        except OSError:
            pass

    src = "embedded art" if cover else (f"sibling ({sibling.name})" if sibling else "black frame")
    return f"OK    {mp3_path.name}  [{src}]"


def convert_video_mode(
    ffmpeg_exe: str,
    mp3_path: Path,
    out_path: Path,
    video_path: Path,
    delogo: Optional[tuple[int, int, int, int]],
) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Compute a finite loop count from actual durations.
    # stream_loop -1 (infinite) silently fails with certain codecs in ffmpeg 7.x.
    audio_dur = probe_duration(ffmpeg_exe, mp3_path)
    video_dur = probe_duration(ffmpeg_exe, video_path)
    if audio_dur > 0 and video_dur > 0:
        loops = math.ceil(audio_dur / video_dur) + 1
    else:
        loops = 999

    cmd = [ffmpeg_exe, "-y",
           "-stream_loop", str(loops), "-i", str(video_path),
           "-i", str(mp3_path)]

    # explicit mapping prevents ffmpeg 7.x auto-selecting the MP3's embedded art
    cmd += ["-map", "0:v:0", "-map", "1:a:0"]

    # scale forces even dimensions required by libx264
    if delogo:
        x, y, w, h = delogo
        vf = f"delogo=x={x}:y={y}:w={w}:h={h},scale=trunc(iw/2)*2:trunc(ih/2)*2"
    else:
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"

    cmd += ["-vf", vf]

    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    _run_cmd(cmd)

    tag = "looping video"
    if delogo:
        tag += f" + watermark removed @ ({delogo[0]},{delogo[1]}) {delogo[2]}×{delogo[3]}"
    return f"OK    {mp3_path.name}  [{tag}]"


# ── colours ───────────────────────────────────────────────────────────────────

BG      = "#1e1e2e"
SURFACE = "#313244"
BORDER  = "#89b4fa"
TEXT    = "#cdd6f4"
SUBTEXT = "#6c7086"
GREEN   = "#a6e3a1"
RED     = "#f38ba8"
YELLOW  = "#fab387"
MANTLE  = "#181825"

CANVAS_MAX_W = 440
CANVAS_MAX_H = 240


# ── app ───────────────────────────────────────────────────────────────────────

class App(TkinterDnD.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MP3 → MP4 Converter")
        self.geometry("700x700")
        self.minsize(560, 560)
        self.configure(bg=BG)

        self._ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self._output_dir = get_exe_dir() / "output"
        self._output_dir.mkdir(exist_ok=True)
        self._busy = False

        self._video_path: Optional[Path] = None
        self._video_w = 0
        self._video_h = 0
        self._canvas_w = CANVAS_MAX_W
        self._canvas_h = CANVAS_MAX_H
        self._photo: Optional[ImageTk.PhotoImage] = None   # keep reference alive

        self._sel_start: Optional[tuple[int, int]] = None
        self._sel_rect_id: Optional[int] = None
        self._sel_coords: Optional[tuple[int, int, int, int]] = None  # canvas px

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_mp3_zone()
        self._build_video_section()
        self._build_progress()
        self._build_log()
        self._build_statusbar()

    def _build_mp3_zone(self) -> None:
        drop = tk.Frame(self, bg=SURFACE, highlightbackground=BORDER, highlightthickness=2)
        drop.pack(fill="x", padx=20, pady=(16, 8))

        lbl_big = tk.Label(drop, text="Drop MP3 files here",
                           font=("Segoe UI", 18, "bold"), fg=TEXT, bg=SURFACE)
        lbl_big.pack(pady=(16, 4))

        lbl_sub = tk.Label(drop,
                           text="Converted files go to  output\\  (next to this program)",
                           font=("Segoe UI", 9), fg=SUBTEXT, bg=SURFACE)
        lbl_sub.pack(pady=(0, 12))

        for w in (drop, lbl_big, lbl_sub):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", self._on_mp3_drop)

    def _build_video_section(self) -> None:
        self._vsec = tk.LabelFrame(
            self, text="  Background Video (optional)  ",
            bg=BG, fg=SUBTEXT, font=("Segoe UI", 9),
            bd=1, highlightbackground=SURFACE, highlightthickness=1,
        )
        self._vsec.pack(fill="x", padx=20, pady=(0, 8))

        # ── file row ──
        frow = tk.Frame(self._vsec, bg=BG)
        frow.pack(fill="x", padx=10, pady=(8, 6))

        self._vid_label = tk.Label(
            frow, text="No video selected  —  drop an .mp4 here or browse",
            font=("Segoe UI", 9), fg=SUBTEXT, bg=BG, anchor="w",
        )
        self._vid_label.pack(side="left", fill="x", expand=True)

        def _btn(parent, text, cmd, fg=TEXT):
            return tk.Button(parent, text=text, command=cmd,
                             font=("Segoe UI", 9), bg=SURFACE, fg=fg,
                             activebackground="#45475a", activeforeground=TEXT,
                             relief="flat", padx=10, pady=3, cursor="hand2", bd=0)

        _btn(frow, "Clear",   self._clear_video, fg=SUBTEXT).pack(side="right")
        _btn(frow, "Browse…", self._browse_video).pack(side="right", padx=(0, 6))

        for w in (self._vsec, frow, self._vid_label):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", self._on_video_drop)

        # ── preview canvas (hidden until a video is loaded) ──
        self._canvas_outer = tk.Frame(self._vsec, bg=BG)
        self._canvas_outer.pack_forget()

        self._canvas = tk.Canvas(
            self._canvas_outer,
            width=CANVAS_MAX_W, height=CANVAS_MAX_H,
            bg=MANTLE, highlightthickness=1, highlightbackground=SURFACE,
            cursor="crosshair",
        )
        self._canvas.pack(padx=10, pady=(0, 6))
        self._canvas.bind("<ButtonPress-1>",   self._sel_press)
        self._canvas.bind("<B1-Motion>",        self._sel_drag)
        self._canvas.bind("<ButtonRelease-1>",  self._sel_release)

        self._canvas_hint = tk.Label(
            self._canvas_outer,
            text="Draw a box over the watermark to remove it. Right-click to clear selection.",
            font=("Segoe UI", 8), fg=SUBTEXT, bg=BG,
        )
        self._canvas_hint.pack(pady=(0, 6))
        self._canvas.bind("<ButtonPress-3>", lambda _: self._clear_selection())

        # ── watermark toggle ──
        wrow = tk.Frame(self._vsec, bg=BG)
        wrow.pack(fill="x", padx=10, pady=(0, 10))

        self._wm_var = tk.BooleanVar(value=False)
        self._wm_chk = tk.Checkbutton(
            wrow, text="Remove watermark (draw selection on frame above)",
            variable=self._wm_var,
            font=("Segoe UI", 9), fg=SUBTEXT, bg=BG, activebackground=BG,
            selectcolor=SURFACE, state="disabled",
        )
        self._wm_chk.pack(side="left")

    def _build_progress(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TProgressbar", troughcolor=SURFACE, background=BORDER, bordercolor=BG)
        self._progress = ttk.Progressbar(self, mode="indeterminate", style="TProgressbar")
        self._progress.pack(fill="x", padx=20, pady=(0, 6))

    def _build_log(self) -> None:
        self._log = scrolledtext.ScrolledText(
            self, state="disabled", font=("Consolas", 9),
            bg=MANTLE, fg=TEXT, insertbackground=TEXT,
            selectbackground=SURFACE, relief="flat",
        )
        self._log.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        for tag, color in [("green", GREEN), ("red", RED), ("yellow", YELLOW), ("sub", SUBTEXT)]:
            self._log.tag_config(tag, foreground=color)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 14))

        tk.Button(bar, text="Clear log", command=self._clear_log,
                  font=("Segoe UI", 9), bg=SURFACE, fg=TEXT,
                  activebackground="#45475a", activeforeground=TEXT,
                  relief="flat", padx=12, pady=4, cursor="hand2", bd=0).pack(side="left")

        self._status = tk.Label(bar, text="Ready", font=("Segoe UI", 9), fg=SUBTEXT, bg=BG)
        self._status.pack(side="right")

    # ── video loading ─────────────────────────────────────────────────────────

    def _set_video(self, path: Path) -> None:
        self._video_path = path
        self._video_w = self._video_h = 0
        self._clear_selection()
        self._vid_label.config(text=f"{path.name}  (loading…)", fg=TEXT)
        self._wm_chk.config(state="disabled", fg=SUBTEXT)
        # Show canvas area with placeholder while loading
        self._canvas_outer.pack(fill="x")
        self._canvas.delete("all")
        self._canvas.config(width=CANVAS_MAX_W, height=CANVAS_MAX_H)
        self._canvas.create_text(
            CANVAS_MAX_W // 2, CANVAS_MAX_H // 2,
            text="Loading preview…", fill=SUBTEXT, font=("Segoe UI", 10),
        )
        threading.Thread(target=self._load_video_async, args=(path,), daemon=True).start()

    def _load_video_async(self, path: Path) -> None:
        size = probe_video_size(self._ffmpeg, path)
        frame = extract_first_frame(self._ffmpeg, path)
        self.after(0, lambda: self._on_video_loaded(path, size, frame))

    def _on_video_loaded(
        self,
        path: Path,
        size: Optional[tuple[int, int]],
        frame_path: Optional[Path],
    ) -> None:
        if self._video_path != path:
            return  # user cleared video while loading

        if size:
            self._video_w, self._video_h = size

        short = path.name if len(path.name) <= 50 else "…" + path.name[-47:]
        dim_str = f"  ({self._video_w}×{self._video_h})" if size else ""
        self._vid_label.config(text=short + dim_str, fg=TEXT)

        if frame_path and frame_path.exists():
            try:
                img = Image.open(frame_path)
                # Scale to fit canvas bounds
                iw, ih = img.size
                scale = min(CANVAS_MAX_W / iw, CANVAS_MAX_H / ih)
                cw = max(2, int(iw * scale))
                ch = max(2, int(ih * scale))
                self._canvas_w = cw
                self._canvas_h = ch
                img = img.resize((cw, ch), Image.LANCZOS)
                self._photo = ImageTk.PhotoImage(img)
                self._canvas.config(width=cw, height=ch)
                self._canvas.delete("all")
                self._canvas.create_image(0, 0, anchor="nw", image=self._photo)
            except Exception:
                self._canvas.delete("all")
                self._canvas.create_text(
                    CANVAS_MAX_W // 2, CANVAS_MAX_H // 2,
                    text="Preview unavailable", fill=SUBTEXT, font=("Segoe UI", 10),
                )
            finally:
                try:
                    frame_path.unlink()
                except OSError:
                    pass
        else:
            self._canvas.delete("all")
            self._canvas.create_text(
                CANVAS_MAX_W // 2, CANVAS_MAX_H // 2,
                text="Preview unavailable", fill=SUBTEXT, font=("Segoe UI", 10),
            )

        self._wm_chk.config(state="normal", fg=TEXT)

    def _clear_video(self) -> None:
        self._video_path = None
        self._video_w = self._video_h = 0
        self._photo = None
        self._vid_label.config(
            text="No video selected  —  drop an .mp4 here or browse", fg=SUBTEXT
        )
        self._wm_var.set(False)
        self._wm_chk.config(state="disabled", fg=SUBTEXT)
        self._clear_selection()
        self._canvas_outer.pack_forget()

    def _browse_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Select background video",
            filetypes=[("MP4 video", "*.mp4"), ("All video files", "*.mp4 *.mov *.mkv")],
        )
        if path:
            self._set_video(Path(path))

    # ── canvas selection ──────────────────────────────────────────────────────

    def _sel_press(self, event: tk.Event) -> None:
        self._sel_start = (event.x, event.y)
        if self._sel_rect_id:
            self._canvas.delete(self._sel_rect_id)
            self._sel_rect_id = None
        self._sel_coords = None

    def _sel_drag(self, event: tk.Event) -> None:
        if not self._sel_start:
            return
        x0, y0 = self._sel_start
        x1, y1 = event.x, event.y
        if self._sel_rect_id:
            self._canvas.delete(self._sel_rect_id)
        self._sel_rect_id = self._canvas.create_rectangle(
            x0, y0, x1, y1,
            outline=RED, width=2, dash=(4, 2),
        )

    def _sel_release(self, event: tk.Event) -> None:
        if not self._sel_start:
            return
        x0, y0 = self._sel_start
        x1, y1 = event.x, event.y
        # Ensure min size of 4px so accidental clicks don't register
        if abs(x1 - x0) < 4 or abs(y1 - y0) < 4:
            self._clear_selection()
            return
        self._sel_coords = (
            min(x0, x1), min(y0, y1),
            max(x0, x1), max(y0, y1),
        )
        self._sel_start = None
        if not self._wm_var.get():
            self._wm_var.set(True)

    def _clear_selection(self) -> None:
        if self._sel_rect_id:
            self._canvas.delete(self._sel_rect_id)
            self._sel_rect_id = None
        self._sel_coords = None
        self._sel_start = None

    def _get_delogo(self) -> Optional[tuple[int, int, int, int]]:
        if not self._wm_var.get():
            return None
        if not self._sel_coords:
            self._append("Draw a selection box on the video frame first.\n", "yellow")
            return None

        cx1, cy1, cx2, cy2 = self._sel_coords
        vw = self._video_w or self._canvas_w
        vh = self._video_h or self._canvas_h
        sx = vw / self._canvas_w
        sy = vh / self._canvas_h

        x = int(cx1 * sx)
        y = int(cy1 * sy)
        w = int((cx2 - cx1) * sx)
        h = int((cy2 - cy1) * sy)
        # ensure even dims for libx264
        w += w % 2
        h += h % 2
        # clamp so region never exceeds video bounds
        x = max(0, min(x, vw - 2))
        y = max(0, min(y, vh - 2))
        w = max(2, min(w, vw - x))
        h = max(2, min(h, vh - y))

        if w <= 0 or h <= 0:
            self._append("Selection too small — draw a larger box.\n", "yellow")
            return None

        return (x, y, w, h)

    # ── drop handlers ─────────────────────────────────────────────────────────

    def _on_mp3_drop(self, event: tk.Event) -> None:
        if self._busy:
            self._append("Already converting — please wait.\n", "yellow")
            return
        files = parse_dropped_files(event.data, ".mp3")
        if not files:
            self._append("No valid .mp3 files found in drop.\n", "red")
            return
        threading.Thread(target=self._run_batch, args=(files,), daemon=True).start()

    def _on_video_drop(self, event: tk.Event) -> None:
        files = parse_dropped_files(event.data, ".mp4")
        if not files:
            self._append("Drop a single .mp4 file here.\n", "red")
            return
        self._set_video(files[0])

    # ── batch conversion ──────────────────────────────────────────────────────

    def _run_batch(self, files: list[Path]) -> None:
        self._busy = True
        n = len(files)
        video  = self._video_path
        delogo = self._get_delogo() if video else None

        self.after(0, self._progress.start)
        self.after(0, lambda: self._status.config(text=f"Converting {n} file(s)…"))
        mode = "looping video" if video else "album art"
        self.after(0, lambda: self._append(f"── {n} file(s) → output\\  [{mode}]\n", "sub"))

        ok = 0
        for mp3 in files:
            out = self._output_dir / mp3.with_suffix(".mp4").name
            try:
                if video:
                    msg = convert_video_mode(self._ffmpeg, mp3, out, video, delogo)
                else:
                    msg = convert_image_mode(self._ffmpeg, mp3, out)
                self.after(0, lambda m=msg: self._append(m + "\n", "green"))
                ok += 1
            except Exception as exc:
                self.after(0, lambda m=f"FAIL  {mp3.name}: {exc}": self._append(m + "\n", "red"))

        self.after(0, self._progress.stop)
        self.after(0, lambda: self._status.config(text=f"Done — {ok}/{n} ok"))
        self.after(0, lambda: self._append(f"── Done: {ok}/{n} succeeded.\n", "sub"))
        self._busy = False

    # ── log ───────────────────────────────────────────────────────────────────

    def _append(self, text: str, tag: str = "") -> None:
        self._log.config(state="normal")
        self._log.insert("end", text, tag)
        self._log.see("end")
        self._log.config(state="disabled")

    def _clear_log(self) -> None:
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
