# -*- coding: utf-8 -*-
"""Создать группы под известные роли.

    python manage.py ensure_roles

Роли создаются и по требованию — при первом назначении через API. Команда нужна,
чтобы они были видны в админке Django сразу после разворачивания, до того как
кому-то выдали первую роль.

Список ролей живёт в `core/api/roles.py` и только там: миграция с данными завела
бы второй список, и правку пришлось бы делать дважды.
"""
from django.core.management.base import BaseCommand

from core.api.roles import ROLES, SECTIONS, group_for


class Command(BaseCommand):
    help = 'Создать группы Django под роли «Плана»'

    def handle(self, *args, **opts):
        for name, (descr, sections) in ROLES.items():
            group_for(name)
            titles = ', '.join(SECTIONS[s][0] for s in sections)
            self.stdout.write(f'  {name} — правит: {titles}')
        self.stdout.write(self.style.SUCCESS(
            f'Ролей: {len(ROLES)}. Администратор ролью не выдаётся — это '
            f'признак «суперпользователь» у самой учётной записи.'))
