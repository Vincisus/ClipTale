import argparse
import asyncio
import json
import os
from pathlib import Path
import re
from app.core.story_brain import StoryBrain
from app.services.llm_service import LLMService

async def resume(project_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project_id):
        raise ValueError("ID progetto non valido.")
    folder = Path("projects") / project_id
    def read(name):
        return json.loads((folder / name).read_text(encoding="utf-8"))
    brief, concept, structure = read("brief.json"), read("concept.json"), read("structure.json")
    if brief.get("id") != project_id:
        raise ValueError("Brief e cartella non corrispondono.")
    lock = folder / ".scene_recovery.lock"
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        brain = StoryBrain(LLMService())
        brain.on_progress = lambda text: print(text, flush=True)
        script = await brain.generate_script(concept, structure, brief)
        brain.save(folder / "script.md", script)
        brain.save(folder / "storyboard.json", await brain.generate_storyboard(script, structure, brief))
        print("Scrittura e storyboard completati. Nessuna voce o video generati.", flush=True)
    finally:
        lock.unlink()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Riprende scrittura e storyboard. Fermare prima Uvicorn.")
    parser.add_argument("project_id")
    asyncio.run(resume(parser.parse_args().project_id))
