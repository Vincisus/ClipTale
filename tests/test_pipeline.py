<<<<<<< HEAD
from pathlib import Path
import tempfile
=======
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
from app.services.subtitle_service import SubtitleService
from app.services.tiktok_service import TikTokService


def test_subtitle_generation():
    service = SubtitleService()
<<<<<<< HEAD
    with tempfile.TemporaryDirectory() as tmp:
        out = service.create_srt_from_script(
            "Ciao. Questo è un test. La storia continua.",
            output_path=Path(tmp) / "test.srt",
            target_duration=30,
        )
        assert out.exists()
        assert "00:00:00,000 -->" in out.read_text(encoding="utf-8")
=======
    output = service.create_srt_from_script(
        "Ciao. Questo è un test. La storia continua.",
        output_path=None,
        target_duration=30,
    )
    assert output is not None
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387


def test_select_parts():
    assert TikTokService._select_parts(10, 4) == [0, 2, 5, 7]
    assert TikTokService._select_parts(4, 4) == [0, 1, 2, 3]
