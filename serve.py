# -*- coding: utf-8 -*-
"""Lance le serveur local depuis n'importe quel dossier : python serve.py [--port 8765] [--runs runs]"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
from prgraph.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["serve"] + sys.argv[1:]))
