# -*- coding: utf-8 -*-
"""Отдача самого приложения.

`План.html` подаётся тем же сервером, что и API. Иначе браузер считал бы запросы
межсайтовыми: пришлось бы настраивать CORS и разбираться с куками сессии. Один
адрес снимает вопрос целиком — и это то, к чему мы всё равно идём (ТЗ §12:
nginx отдаёт статику, Django — API).
"""
from pathlib import Path

from django.http import FileResponse, Http404

ROOT = Path(__file__).resolve().parents[3]
ALLOWED = {'': 'План.html', 'index.html': 'План.html', 'plan': 'План.html',
           'help.html': 'help.html', 'schema.html': 'schema.html'}


def page(request, name=''):
    """Страница приложения. Список файлов закрытый — каталог наружу не отдаём."""
    filename = ALLOWED.get(name)
    if not filename:
        raise Http404('Нет такой страницы')
    path = ROOT / filename
    if not path.exists():
        raise Http404('Файл не найден')
    resp = FileResponse(open(path, 'rb'), content_type='text/html; charset=utf-8')
    # Правки подхватываются обычным F5 — как и в serve.py.
    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp
