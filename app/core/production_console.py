"""Percorsi e utilità per console di produzione — voce, video, sottotitoli, diagnostica."""
from pathlib import Path

# Percorsi standard nei progetti
PROJECTS_ROOT = Path("projects")

def project_path(project_id):
    """Restituisce il percorso del progetto dato un ID."""
    return PROJECTS_ROOT / project_id

def assets_dir(project_path):
    """Restituisce le sottocartelle asset standard."""
    audio = project_path / "audio"
    video = project_path / "render"
    subtitles = project_path / "subtitles"
    tiktok = project_path / "tiktok"
    for d in [audio, video, subtitles, tiktok]:
        d.mkdir(parents=True, exist_ok=True)
    return {
        "audio": audio,
        "video": video,
        "subtitles": subtitles,
        "tiktok": tiktok,
        "diagnostics": project_path / "diagnostics"
    }

def list_projects():
    """Elenco progetti con stato."""
    results = []
    for p in PROJECTS_ROOT.glob("*"):
        if p.is_dir():
            brief = p / "brief.json"
            if brief.exists():
                results.append({
                    "id": p.name,
                    "title": p.name,
                    "path": str(p)
                })
    return sorted(results, key=lambda x: x["id"], reverse=True)
