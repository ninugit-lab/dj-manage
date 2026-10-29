from django.core.management.base import BaseCommand
from django.db import transaction
from django.core.management.base import CommandError
from wishlist.models import PricingPackage, PricingWorkflow, PricingRule, PricingFormula, PriceItem


class Command(BaseCommand):
    help = 'Basis-Paket mit normalen Kalkulator-Posten, Regeln, Formel und Workflow einrichten.'

    @transaction.atomic
    def handle(self, *args, **options):
        package, created = PricingPackage.objects.get_or_create(name='Basis-Paket', defaults={
            'base_price': '660',
            'description': '6 Stunden DJ-Spielzeit mit Tontechnik. Lichttechnik und Mikrofone inklusive. Nebelmaschine inklusive, sofern die Location ihren Einsatz erlaubt.',
            'is_active': True, 'is_public': True,
        })
        if package.workflow_id:
            self.stdout.write(f'Basis-Paket #{package.pk} bereits eingerichtet; bestehende Änderungen bleiben erhalten.')
            return
        def component(model, name, **defaults):
            if model.objects.filter(name=name).exists():
                raise CommandError(f'Namenskonflikt: {name}. Bestehenden Eintrag zuerst prüfen.')
            return model.objects.create(name=name, **defaults)
        items = [component(PriceItem, f'Basis: {name}', price='0', category='base',
                           is_public=True, is_active=True) for name in ('DJ-Leistung', 'Lichttechnik', 'Mikrofone')]
        guest = component(PricingRule, 'Basis: Gäste ab 51', effect_type='flat_add', effect_value='90',
            condition_json=[{'field': 'guest_count', 'op': 'gte', 'value': 51}], sort_order=0)
        sound = component(PricingRule, 'Basis: Ohne Tontechnik', effect_type='flat_add', effect_value='-80',
            condition_json=[{'field': 'with_sound', 'op': 'eq', 'value': False}], sort_order=1)
        formula = component(PricingFormula, 'Basis: Zusatzstunden und Anfahrt', mode='add',
            expression='max(0, ceil((duration_minutes - 360) / 60)) * 60 + max(0, 2 * distance - 100) * 0.30',
            description='6 Stunden inklusive; weitere angefangene Stunde 60 EUR. 100 km Hin-/Rückfahrt inklusive, danach 0,30 EUR/km.')
        workflow = component(PricingWorkflow, 'Basis: Buchung', is_default=False, workflow_json=[
            {'type': 'package'}, {'type': 'items'},
            {'type': 'rules', 'config': {'rule_ids': [guest.pk, sound.pk]}},
            {'type': 'formula', 'config': {'formula_id': formula.pk}},
        ])
        package.included_items.add(*items)
        for rule in (guest, sound):
            rule.condition_json.insert(0, {'field': 'workflow_id', 'op': 'eq', 'value': workflow.pk})
            rule.save(update_fields=['condition_json'])
        package.workflow = workflow
        package.save(update_fields=['workflow'])
        self.stdout.write(f'Basis-Paket #{package.pk}: Workflow #{workflow.pk}, 3 Posten, 2 Regeln, 1 Formel eingerichtet.')
