"""Processo separato per le voci SAPI di Windows (pyttsx3).

    python -m app.services.sapi_worker --list
    python -m app.services.sapi_worker jobs.json   # {"voice": id, "rate": wpm, "items": [{"text", "path"}]}
"""
import json
import sys

import pyttsx3


def main(argv):
    engine = pyttsx3.init()
    if argv and argv[0] == "--list":
        voices = engine.getProperty("voices") or []
        print(json.dumps([{"id": v.id, "name": v.name, "italian": "ital" in v.name.lower() or any("it" in str(l).lower() for l in (v.languages or []))} for v in voices]))
        return 0
    spec = json.loads(open(argv[0], encoding="utf-8").read())
    if spec.get("voice"):
        engine.setProperty("voice", spec["voice"])
    engine.setProperty("rate", int(spec.get("rate") or 165))
    engine.setProperty("volume", 1.0)
    for item in spec["items"]:
        engine.save_to_file(item["text"], item["path"])
    engine.runAndWait()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
