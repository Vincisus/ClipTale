import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from app.core.story_brain import StoryBrain
from app.services.llm_service import LLMService

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = Path.cwd()
        os.chdir(self.temp.name)
        self.env = patch.dict(os.environ, {"LLM_DIAGNOSTIC_DIR": "diagnostics", "LMSTUDIO_BASE_URL": "http://127.0.0.1:1234/v1"})
        self.env.start()
    def tearDown(self):
        self.env.stop()
        os.chdir(self.cwd)
        self.temp.cleanup()
    def service(self, answers):
        calls, queue = [], iter(answers)
        def handle(request):
            calls.append(json.loads(request.content))
            content, finish, reasoning = next(queue)
            return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": {"content": content, "reasoning_content": reasoning}}]})
        return LLMService(httpx.MockTransport(handle)), calls
    async def test_empty_then_final(self):
        llm, calls = self.service([("", "stop", "private"), ("Finale", "stop", "")])
        self.assertEqual(await llm.generate_text("Scena"), "Finale")
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[0]["stream"])
        records = [json.loads(p.read_text()) for p in Path("diagnostics").glob("*.json")]
        self.assertEqual(len(records), 2)
        self.assertNotIn("private", str(records))
    async def test_two_empty_stop(self):
        llm, calls = self.service([("", "stop", "a"), ("", "stop", "b")])
        with self.assertRaisesRegex(RuntimeError, "recupero limitato"):
            await llm.generate_text("Scena")
        self.assertEqual(len(calls), 2)
    async def test_length_rejected(self):
        llm, calls = self.service([("Parziale", "length", "")])
        with self.assertRaisesRegex(RuntimeError, "incompleta"):
            await llm.generate_text("Scena")
        self.assertEqual(len(calls), 1)
    async def test_unclosed_think_rejected(self):
        llm, calls = self.service([("<think>privato", "stop", "")])
        with self.assertRaisesRegex(RuntimeError, "non bilanciati"):
            await llm.generate_text("Scena")
    async def test_final_content_accepted(self):
        llm, calls = self.service([("<think>privato</think>Finale", "stop", "")])
        self.assertEqual(await llm.generate_text("Scena"), "Finale")
        self.assertEqual(len(calls), 1)
    async def test_reviewer_learning_rule(self):
        class Fake:
            async def generate_json(self, prompt):
                assert "NON contestarli" in prompt
                return {"ok": True, "issues": []}
        self.assertTrue((await StoryBrain(Fake()).review({}, "scena"))["ok"])
    async def test_folder_rejects_invalid_id(self):
        with self.assertRaises(ValueError):
            StoryBrain.folder({"id": "../other"})
    async def test_resume_keeps_approved_scene(self):
        class Fake:
            def __init__(self): self.writes = 0
            async def generate_json(self, prompt):
                if prompt.startswith("Sei un revisore."):
                    return {"ok": True, "issues": []}
                n = int(re.search(r"scena (\d+)", prompt).group(1))
                return {"scene_id": n, "part": 1, "pov_id": "c1", "location": "Stanza", "characters_present": ["c1"], "facts": ["Scopre"], "knowledge_before": ["Non sa"], "knowledge_after": ["Sa"], "withhold": [], "visual": "Interno"}
            async def generate_text(self, prompt):
                self.writes += 1
                if self.writes == 2: raise RuntimeError("Interruzione simulata")
                return "parola " * 120
        fake = Fake()
        brain = StoryBrain(fake)
        concept = {"characters": [{"id": "c1", "name": "Marco"}]}
        structure = {"parts": [{"part": 1, "title": "T", "summary": "S", "cliffhanger": "C"}]}
        brief = {"id": "test", "duration_minutes": 1, "tiktok_clips": 1}
        with self.assertRaisesRegex(RuntimeError, "Interruzione"):
            await brain.generate_script(concept, structure, brief)
        first = Path("projects/test/scenes/001_approved.txt").read_text()
        await brain.generate_script(concept, structure, brief)
        self.assertEqual(fake.writes, 4)
        self.assertEqual(Path("projects/test/scenes/001_approved.txt").read_text(), first)
        structure["parts"][0]["summary"] = "Piano cambiato"
        with self.assertRaisesRegex(ValueError, "incompatibile"):
            await brain.generate_script(concept, structure, brief)

if __name__ == "__main__":
    unittest.main()
