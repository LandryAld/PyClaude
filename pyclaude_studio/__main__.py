"""Point d'entrée : ``python -m pyclaude_studio [options]``."""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv=None) -> int:
    from . import APP_NAME, __version__

    parser = argparse.ArgumentParser(prog="pyclaude_studio",
                                     description=f"{APP_NAME} — atelier scientifique et créatif")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--theme", help="thème à appliquer au démarrage")
    parser.add_argument("--module", action="append", default=[],
                        help="module à ouvrir (répétable), ex. --module fractal")
    parser.add_argument("--no-splash", action="store_true", help="sans écran de démarrage")
    parser.add_argument("--fresh", action="store_true",
                        help="ne pas restaurer la session précédente")
    parser.add_argument("--safe-mode", action="store_true",
                        help="sans plugins utilisateur ni restauration de session")
    parser.add_argument("--reset", action="store_true", help="réinitialise tous les réglages")
    parser.add_argument("--selftest", action="store_true",
                        help="ouvre chaque module, vérifie l'absence d'erreur puis quitte")
    parser.add_argument("--verbose", action="store_true", help="journal détaillé sur stderr")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    # Le journal détaillé va dans le panneau « Journal » ; stderr reste sobre.
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    import importlib.util

    if importlib.util.find_spec("tkinter") is None:
        print("Tkinter est introuvable. Sous Debian/Ubuntu : sudo apt install python3-tk",
              file=sys.stderr)
        return 2

    from .app import StudioApp
    from .core.settings import Settings

    settings = Settings.load()
    if args.reset:
        settings.reset()
        settings.save()
    safe = args.safe_mode or args.selftest
    app = StudioApp(theme=args.theme, splash=False if (args.no_splash or args.selftest) else None,
                    restore=False if (args.fresh or safe) else None, plugins=not args.safe_mode,
                    settings=settings)
    for mid in args.module:
        app.open_module(mid)

    if args.selftest:
        result = {"ok": False}

        def finish(ok: bool, report) -> None:
            result["ok"] = ok
            print("\n".join(report))
            app.root.after(200, app.quit)

        app.root.after(500, lambda: app.run_selftest(finish))
        app.run()
        return 0 if result["ok"] else 1
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
