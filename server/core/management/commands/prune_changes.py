# -*- coding: utf-8 -*-
"""Очистка старых записей журнала изменений.

    python manage.py prune_changes --days 180        # что удалится
    python manage.py prune_changes --days 180 --yes  # удалить

Журнал растёт непрерывно: сотня человек, каждая правка строки — запись. Без
очистки он однажды перевесит сами данные. Полгода — разумный горизонт: столько
живёт вопрос «кто поставил этот план на прошлый квартал».

По умолчанию только показывает, что будет удалено. Удаление истории необратимо,
и делать его молчаливым побочным эффектом запуска нельзя.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import models as m

DEFAULT_DAYS = 180


class Command(BaseCommand):
    help = 'Удалить записи журнала изменений старше указанного возраста'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                            help=f'сколько дней хранить (по умолчанию {DEFAULT_DAYS})')
        parser.add_argument('--yes', action='store_true',
                            help='действительно удалить, а не только показать')

    def handle(self, *args, **opts):
        days = opts['days']
        if days < 1:
            self.stdout.write(self.style.ERROR('Срок хранения должен быть хотя бы день.'))
            return

        edge = timezone.now() - timedelta(days=days)
        qs = m.ChangeLog.objects.filter(at__lt=edge)
        count = qs.count()
        total = m.ChangeLog.objects.count()

        if not count:
            self.stdout.write(f'Записей старше {days} дн. нет (всего в журнале {total}).')
            return

        oldest = qs.order_by('at').values_list('at', flat=True).first()
        self.stdout.write(f'Старше {days} дн.: {count} из {total}, '
                          f'самая ранняя от {oldest:%Y-%m-%d}.')
        if not opts['yes']:
            self.stdout.write(self.style.WARNING(
                'Ничего не удалено. Повторите с --yes, если это то, что нужно.'))
            return

        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Удалено {count}; осталось {m.ChangeLog.objects.count()}.'))
