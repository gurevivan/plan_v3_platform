# Образ серверной части «План V3».
#
# Само приложение — это `План.html` в корне репозитория, и отдаёт его тот же
# Django (см. core/api/pages.py). Поэтому в образ копируется весь проект, а не
# только каталог server/: страница и API обязаны быть на одном адресе, иначе
# браузер сочтёт запросы межсайтовыми и куки сессии перестанут ходить.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/server

# Зависимости отдельным слоем: правка кода не должна тянуть переустановку пакетов.
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Postgres в контейнере поднимается не мгновенно, а первому запуску нужны миграции.
COPY server/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY server/ /app/server/
COPY План.html help.html schema.html /app/

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "planserver.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "120"]
