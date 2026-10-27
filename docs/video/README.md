# Devpost recording

[Play the video](routewise-devpost.mp4) · [Timed script](../video-script.md) ·
[Captions](captions.vtt) · [Recording manifest](recording.json)

**Revision 3: 1:38.82, 1920 × 1080, H.264 at 24 fps with AAC audio.** Recorded
October 26, 2026 (Pacific), using the updated local Streamlit app. Microsoft Ava
provides computer-generated neural narration. Captions are burned into the
picture and supplied as WebVTT, aligned to the speech service’s word timings.

The opening makes the value proposition explicit and leads with **77.1% lower
inference cost versus grounded Super**, **98.4% correct completion without
review**, and **zero observed wrong releases**, all from the same 312-conversation
representative dataset. The comparison method follows the results. At one million
requests/month, recorded usage costs project to $80 versus $349, excluding hosting
and human review. The separate original v1 91% saving is not mixed into this claim.

[Independent human validation is complete for this dataset](../dataset-validation.md),
as confirmed by the project owner. The conversations remain synthetic. This does
not claim that the separate fresh-customer pilot has run. The later noise study’s
failed latency gate remains explicit.

## Recording evidence

All footage was recaptured from the updated app through CUA at four viewport
frames/second. The edit uses cuts and held views, with narration added afterward.
Historical examples remain labelled RECORDED. The live segment contains a new
real Nebius Token Factory request captured for this revision; no output or model
response was substituted.

[Full live trace](live-trace.json): `ba2990267734491683a55c63339cd4c6`, October 26, 2026,
8:51 p.m. Pacific (`2026-10-27T03:51:26.541071+00:00`). Nano returned a null issue
date; normalization resolved April 2 plus four days to April 6 and kept the
April 10 arrival date separate. One call, 1,488.2 ms,
$0.00006852 estimated usage cost, complete billing, and a released
final answer. This is one functionality check, not a new benchmark.

The complete final MP4 decoded without errors. Scene previews were visually
inspected. Audio analysis found mean volume -16.7 dB and peak -1.4 dB; mastering
targets -16 LUFS integrated loudness and -1.5 dBTP. The final file hash, source
commit and per-scene narration hashes are in `recording.json`.

The final video, script, trace and metadata are versioned. Local source captures,
word timing and narration tracks are in `artifacts/video/source-captures-v3.tar.gz`
(intentionally ignored). Earlier cuts are retained in `artifacts/video/revisions/`
and Git history. No YouTube upload or final submission has been performed.

## Re-render

Restore the source archive to a writable directory, then run:

```bash
.venv/bin/python docs/video/render.py /private/tmp/routewise-video-v3
```

The renderer needs Pillow, ffmpeg and ffprobe. To regenerate speech, install
[edge-tts](https://github.com/rany2/edge-tts) in a separate authoring environment and
run `python docs/video/synthesize.py /private/tmp/routewise-video-v3`. Voice and
capture-group settings are in `production.json`. Only the public narration text
is sent to Microsoft’s speech service.
