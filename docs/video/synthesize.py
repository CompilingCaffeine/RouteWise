"""Generate neural narration and word timing for the public demo script.

Install edge-tts in a separate authoring environment, then run:
python docs/video/synthesize.py /private/tmp/routewise-video-v2
Only the public narration text is sent to Microsoft's Edge speech service.
"""
import asyncio
import json
from pathlib import Path
import sys

import edge_tts

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
PRODUCTION = json.loads((HERE / "production.json").read_text())


async def main():
    semaphore = asyncio.Semaphore(2)

    async def generate(scene):
        async with semaphore:
            voice = PRODUCTION["voice"]
            speech = edge_tts.Communicate(scene["narration"], voice, rate=PRODUCTION["rate"],
                                          boundary="WordBoundary")
            events = []
            with (OUT / (scene["id"] + ".mp3")).open("wb") as audio:
                async for chunk in speech.stream():
                    if chunk["type"] == "audio":
                        audio.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        events.append(chunk)
            if not events:
                raise RuntimeError(f"No word timings for {scene['id']}")
            (OUT / (scene["id"] + ".words.json")).write_text(json.dumps(events, indent=2) + "\n")
            print(f"Generated {scene['id']}: {len(events)} timed words", flush=True)

    scenes = json.loads((HERE / "narration.json").read_text())
    selected = set(sys.argv[2:])
    await asyncio.gather(*(generate(scene) for scene in scenes if not selected or scene["id"] in selected))


asyncio.run(main())
