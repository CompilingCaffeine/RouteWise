"""Render the demo from CUA viewport captures and local narration tracks.

Usage: .venv/bin/python docs/video/render.py /private/tmp/routewise-video
Requires Pillow, ffmpeg, and ffprobe. Captures contain real UI pixels; no model
outputs are substituted. Static views are held to fit the narration.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = Path(sys.argv[1])
OUT = ROOT / "artifacts/video"
OUT.mkdir(parents=True, exist_ok=True)
SCENES = json.loads((ROOT / "docs/video/narration.json").read_text())
FRAMES = json.loads((CAPTURES / "frames.json").read_text())
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
TITLE = ImageFont.truetype(BOLD_PATH, 30)
SMALL = ImageFont.truetype(FONT_PATH, 21)
CAPTION = ImageFont.truetype(FONT_PATH, 30)
GROUPS = {
    "05-choices": ["05-choices", "05b-none"],
    "06-live": ["06-live", "06c-live-final", "06b-live-result"],
    "08-export": ["08-export", "08b-limits"],
}


def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ]))


def timestamp(seconds, sep="."):
    ms = round(seconds * 1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}{sep}{ms%1000:03}"


def caption_chunks(text):
    chunks = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = sentence.split()
        for i in range(0, len(words), 13):
            chunks.append(" ".join(words[i:i+13]))
    return chunks


manifest = {"recording_date_pacific": "2026-10-26", "environment": "Local Streamlit app, real Nebius API",
            "narration": "macOS Samantha, computer-generated, 151 words/minute",
            "editing": "CUA viewport captures at 4 fps; cuts and held frames; narration added afterward",
            "resolution": [1920, 1080], "output_fps": 24, "published": False, "scenes": []}
subtitle_entries, vtt_entries = [], []
elapsed = 0.0
for scene in SCENES:
    name = scene["id"]
    audio = CAPTURES / (name + ".aiff")
    spoken = float(probe(audio)["format"]["duration"])
    duration = math.ceil((spoken + .5) * 4) / 4
    groups = GROUPS.get(name, [name])
    shots = []
    for group in groups:
        latest = []
        for frame in FRAMES:
            if frame["scene"] != group:
                continue
            if latest and frame["time"] < latest[-1]["time"]:
                latest = []  # A retake starts its capture clock again.
            latest.append(frame)
        shots.extend(latest)
    if not shots:
        raise ValueError(f"No actual viewport captures for {name}")
    chunks = caption_chunks(scene["narration"])
    count = sum(len(c.split()) for c in chunks)
    cues, at = [], .1
    for chunk in chunks:
        end = min(spoken, at + (spoken - .1) * len(chunk.split()) / count)
        cues.append((at, end, chunk))
        subtitle_entries.append(f"{len(subtitle_entries)+1}\n{timestamp(elapsed+at, ',')} --> {timestamp(elapsed+end, ',')}\n{chunk}\n")
        vtt_entries.append(f"{timestamp(elapsed+at)} --> {timestamp(elapsed+end)}\n{chunk}\n")
        at = end
    destination = OUT / (name + ".mp4")
    command = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
               "-video_size", "1920x1080", "-framerate", "4", "-i", "pipe:0", "-i", str(audio),
               "-vf", "fps=24", "-af", "apad", "-t", str(duration), "-c:v", "libx264",
               "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-ar", "48000", "-b:a", "192k", "-movflags", "+faststart", str(destination)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    cached_path, cached_image = None, None
    preview_saved = False
    for i in range(round(duration * 4)):
        t = i / 4
        # Keep real call progress in order, then hold the last view. Other static
        # groups are spread across the spoken scene to give each view reading time.
        index = min(len(shots)-1, int(i / (duration * 4) * len(shots)))
        frame_path = shots[index]["file"]
        if frame_path != cached_path:
            cached_image = Image.open(frame_path).convert("RGB")
            if cached_image.size != (1600, 900):
                raise ValueError(f"Unexpected viewport size: {cached_image.size}")
            cached_path = frame_path
        canvas = Image.new("RGB", (1920, 1080), "#080c15")
        canvas.paste(cached_image, (160, 80))
        draw = ImageDraw.Draw(canvas)
        draw.text((160, 24), f"RouteWise  /  {scene['title']}", font=TITLE, fill="#f3f6fc")
        draw.text((1430, 32), "LOCAL APP  |  AI NARRATION", font=SMALL, fill="#9aabc2")
        draw.line((160, 73, 1760, 73), fill="#3861a8", width=2)
        text = next((text for start, end, text in cues if start <= t < end), "")
        words, lines, line = text.split(), [], ""
        for word in words:
            candidate = (line + " " + word).strip()
            if draw.textlength(candidate, font=CAPTION) > 1660:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        for line_no, line in enumerate(lines):
            width = draw.textlength(line, font=CAPTION)
            draw.text(((1920-width)/2, 994 + line_no*36), line, font=CAPTION, fill="white")
        process.stdin.write(canvas.tobytes())
        if not preview_saved and t >= duration/2:
            canvas.save(OUT / (name + "-preview.jpg"), quality=92)
            preview_saved = True
    process.stdin.close()
    if process.wait():
        raise RuntimeError(f"ffmpeg failed for {name}")
    manifest["scenes"].append({"id": name, "title": scene["title"], "start_seconds": elapsed,
                               "duration_seconds": duration, "capture_groups": groups,
                               "narration_sha256": hashlib.sha256(audio.read_bytes()).hexdigest()})
    elapsed += duration
    print(f"Rendered {name}: {duration:.2f}s", flush=True)

concat = OUT / "concat.txt"
concat.write_text("\n".join(f"file '{s['id']}.mp4'" for s in SCENES) + "\n")
master = ROOT / "docs/video/routewise-devpost.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                "-c", "copy", "-movflags", "+faststart", str(master)], check=True)
(OUT / "routewise-devpost.srt").write_text("\n".join(subtitle_entries))
(ROOT / "docs/video/captions.vtt").write_text("WEBVTT\n\n" + "\n".join(vtt_entries))
manifest["duration_seconds"] = float(probe(master)["format"]["duration"])
manifest["sha256"] = hashlib.sha256(master.read_bytes()).hexdigest()
manifest["bytes"] = master.stat().st_size
manifest["file"] = "docs/video/routewise-devpost.mp4"
manifest["live_trace"] = "docs/video/live-trace.json"
manifest["code_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
(ROOT / "docs/video/recording.json").write_text(json.dumps(manifest, indent=2) + "\n")
assert manifest["duration_seconds"] < 180
print(f"Finished: {master} ({manifest['duration_seconds']:.2f}s)", flush=True)
