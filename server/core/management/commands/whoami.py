# -*- coding: utf-8 -*-
"""Кто заведён в базе и какие у кого права.

    python manage.py whoami                # все учётные записи
    python manage.py whoami --make-admin ЛОГИН

Нужна, когда интерфейс говорит «вы не администратор», а человек уверен в
обратном: список показывает, как есть на самом деле, без догадок.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core import models as m


class Command(BaseCommand):
    help = 'Показать пользователей и их права; при необходимости выдать администратора'

    def add_arguments(self, parser):
        parser.add_argument('--make-admin', metavar='ЛОГИН',
                            help='сделать эту учётную запись администратором')

    def handle(self, *args, **opts):
        User = get_user_model()

        if opts['make_admin']:
            try:
                user = User.objects.get(username=opts['make_admin'])
            except User.DoesNotExist:
                raise CommandError(f'нет пользователя «{opts["make_admin"]}». '
                                   f'Завести: manage.py createsuperuser')
            user.is_superuser = True
            # В админку Django пускает is_staff; отдельно его не спрашиваем,
            # чтобы не заводить второй признак администратора.
            user.is_staff = True
            user.save()
            self.stdout.write(self.style.SUCCESS(
                f'«{user.username}» теперь администратор. '
                f'В браузере обновите страницу (Ctrl+Shift+R).'))
            return

        users = User.objects.prefetch_related('groups', 'op_access').order_by('username')
        if not users:
            self.stdout.write('Пользователей нет. Завести: manage.py createsuperuser')
            return

        self.stdout.write(f'{"логин":20} {"админ":7} {"активен":8} {"роли":34} площадки')
        for u in users:
            roles = ', '.join(g.name for g in u.groups.all()) or '—'
            ops = list(u.op_access.all())
            # Пустой набор доступов означает «все площадки» — то же правило
            # действует в расчёте, и показывать его надо словами, иначе выглядит
            # как отсутствие доступа.
            where = 'все' if not ops else ', '.join(
                f'{a.op_name}{"" if a.can_edit else " (только смотрит)"}' for a in ops)
            self.stdout.write(f'{u.username:20} {"да" if u.is_superuser else "нет":7} '
                              f'{"да" if u.is_active else "НЕТ":8} {roles[:34]:34} {where}')
