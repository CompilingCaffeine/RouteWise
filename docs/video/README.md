# Devpost recording

[Play the video](routewise-devpost.mp4) · [Timed script](../video-script.md) ·
[Captions](captions.vtt) · [Recording manifest](recording.json)

Recorded October 26, 2026 (Pacific). Duration **172.52 seconds**, 1920 × 1080,
H.264 video at 24 fps with AAC audio. Captions are burned into the picture and
also provided as WebVTT. Narration uses the local macOS Samantha voice at 151
words/minute; it is computer-generated, not a recording of the project author.
There is no music.

The actual local app was captured through CUA at four viewport frames/second.
The edit uses cuts and held views, with narration added afterward. Recorded study
examples remain labelled RECORDED. The live section shows a genuine Nebius Token
Factory request made while capturing the demo; no answers or API responses were
substituted. The source captures and narration tracks are retained locally in
`artifacts/video/source-captures.tar.gz` (intentionally ignored by Git).

## Live call

[Full synthetic-input trace](live-trace.json): request
`92f5fcf03b2e458bb2aff70a41022480`, October 26, 2026, 8:16 p.m. Pacific
(`2026-10-27T03:16:45.640086+00:00`). Nano returned a null refund issue date;
normalization resolved April 2 plus four days to April 6. The April 10 arrival
date remained separate. The final output passed checks: one call, 3,194.4 ms,
$0.00006852 estimated usage cost with complete billing. This demonstrates the
functionality on one illustrative synthetic input, not benchmark performance.

## Checks and remaining steps

The entire final MP4 decoded without errors. Its duration is below three minutes.
Scene previews were visually inspected, including the source-backed correction,
review warning, fixed Super selection, abstention, actual live output, comparison,
and deployment recipe. Audio analysis confirmed a nonempty track (mean -15.9 dB,
peak -1.5 dB). Caption timings are estimated from narration word counts; review
pronunciation and synchronization before publishing.

The public app still showed the older navigation in a fresh browser check. Deploy
the committed guided build and verify it before submission. Upload the video
publicly to YouTube, verify signed-out playback, and put its URL in Devpost.
No upload, public deployment or final submission has been performed.

## Re-render

The renderer expects the actual CUA captures (`frames.json` and per-scene PNGs)
and eight matching AIFF narration tracks. Restore the local source archive to a
writable directory, then run:

```bash
.venv/bin/python docs/video/render.py /private/tmp/routewise-video
```

Pillow, ffmpeg and ffprobe are required. The renderer reads
[`narration.json`](narration.json), keeps the latest capture take per scene,
adds titles/captions outside the app pixels, renders the final MP4, and updates
the recording manifest and caption file. The original UI evidence is preserved.
