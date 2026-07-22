"""
Точка входа для Vercel Python-рантайма. Vercel ищет объект `app`
(WSGI) в файлах под api/ — здесь просто отдаём ему уже готовое
Flask-приложение из app.py в корне проекта.
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402
