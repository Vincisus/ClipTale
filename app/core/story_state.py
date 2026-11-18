<<<<<<< HEAD

=======
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
import json
from pathlib import Path


class StoryState:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.state_path = project_dir / "story_state.json"

    def load(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {}

    def save(self, state: dict):
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
