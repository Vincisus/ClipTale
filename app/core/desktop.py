"""Collegamento fra il server e l'app desktop (finestra e icona nella barra), quando ClipTale gira come programma.

Il launcher (desktop/launcher.py) registra qui le sue funzioni; se il server gira da solo (Avvia.bat, test) restano vuote.
"""
VERSION = "0.6.3"
SUPPORT_URL = "https://paypal.me/dammilpizzo"  # donazioni: "Sostieni ClipTale" in Impostazioni
HOOKS = {"show": None, "quit": None}


def show(project_id=None):
    """Porta in primo piano la finestra di ClipTale (e apre un progetto). False se non c'è una finestra."""
    fn = HOOKS.get("show")
    if not fn:
        return False
    fn(project_id)
    return True
