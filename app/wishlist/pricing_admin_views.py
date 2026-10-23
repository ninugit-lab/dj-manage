"""Staff-only administration of immutable global pricing profile versions."""
import json
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .models import (PriceCalculationRevision, PriceItem, PricingFormula, PricingPackage,
                     PricingPackageOverride, PricingProfile, PricingRule, PricingWorkflow)
from .pricing_v2.config import _FIELDS, resolve_pricing_config, validate_profile_config
from .pricing_v2.input import PricingValidationError


staff_only = user_passes_test(
    lambda user: user.is_active and user.is_staff, login_url='/dj-admin/login/',
)
STALE = 'Die Profilversion ist veraltet. Bitte Seite neu laden.'
INITIAL_CONFIG = {
    'included_hours': '0', 'extra_hour_rate': '0',
    'setup_minutes': 30, 'teardown_minutes': 30,
    'setup_teardown_hour_rate': '0', 'included_km': '0', 'km_rate': '0',
    'saturday_percent': '0', 'sunday_percent': '0', 'holiday_percent': '0',
    'holiday_region': 'NW', 'seasons': [], 'lead_time_tiers': [],
    'guest_tiers': [], 'minimum_price': '0', 'rounding_mode': 'nearest',
    'rounding_step': '1',
}


def _data(request):
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None
    data = request.POST
    try:
        config = {field: json.loads(data[field]) if field in ('seasons', 'lead_time_tiers', 'guest_tiers', 'setup_minutes', 'teardown_minutes')
                  else data[field] for field in _FIELDS}
        return {'name': data.get('name'), 'config_json': config,
                'expected_profile_id': json.loads(data.get('expected_profile_id', 'null')),
                'expected_version': json.loads(data.get('expected_version', 'null'))}
    except (KeyError, ValueError, TypeError):
        return None


def _bad_body():
    return JsonResponse({'errors': {'config_json': 'Ungültige oder unvollständige Eingabe.'}}, status=400)


def _validated(config):
    try:
        return validate_profile_config(config), None
    except PricingValidationError as error:
        return None, JsonResponse({'errors': {error.field or 'config_json': error.message}}, status=400)


def _current(queryset):
    return queryset.order_by('-version', '-pk').first()


@staff_only
@require_http_methods(['GET', 'POST'])
def profile_page(request):
    if request.method == 'GET':
        profiles = list(PricingProfile.objects.order_by('-version', '-pk'))
        active = next((profile for profile in profiles if profile.status == 'active'), None)
        latest = profiles[0] if profiles else None
        source = latest or active
        config = source.config_json if source else INITIAL_CONFIG
        fields = [{'key': key, 'value': json.dumps(config.get(key, []), ensure_ascii=False)
                   if key in ('seasons', 'lead_time_tiers', 'guest_tiers') else config.get(key, '')}
                  for key in _FIELDS]
        return render(request, 'dj_admin/pricing_v2/profile.html', {
            'profiles': profiles, 'active': active, 'latest': latest,
            'fields': fields, 'name': source.name if source else 'Global',
            'initial_config': config,
        })
    data = _data(request)
    if data is None:
        return _bad_body()
    _, error = _validated(data.get('config_json'))
    if error:
        return error
    name = data.get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return JsonResponse({'errors': {'name': 'Ein gültiger Profilname ist erforderlich.'}}, status=400)
    try:
        with transaction.atomic():
            latest = _current(PricingProfile.objects.select_for_update())
            if (data.get('expected_profile_id'), data.get('expected_version')) != (
                latest.pk if latest else None, latest.version if latest else None,
            ):
                return JsonResponse({'error': STALE}, status=409)
            new = PricingProfile.objects.create(
                name=name.strip(), version=latest.version + 1 if latest else 1,
                status=PricingProfile.Status.DRAFT, valid_from=timezone.now(),
                config_json=data['config_json'], created_by=request.user,
            )
    except IntegrityError:
        return JsonResponse({'error': STALE}, status=409)
    return JsonResponse({'id': new.pk, 'version': new.version, 'status': new.status}, status=201)


@staff_only
@require_POST
def validate_profile(request):
    data = _data(request)
    if data is None:
        return _bad_body()
    normalized, error = _validated(data.get('config_json'))
    if error:
        return error
    return JsonResponse({'valid': True, 'preview': {key: str(getattr(normalized, key)) for key in _FIELDS}})


@staff_only
@require_POST
def activate_profile(request):
    data = _data(request)
    if data is None:
        return _bad_body()
    try:
        profile_id = data.get('profile_id')
        if isinstance(profile_id, bool) or not isinstance(profile_id, int):
            raise ValueError
        with transaction.atomic():
            # Lock the active row and target in a consistent order. The database's
            # conditional unique constraint is the last line of defense.
            profiles = list(PricingProfile.objects.select_for_update().order_by('pk'))
            active = next((p for p in profiles if p.status == 'active'), None)
            if (data.get('expected_profile_id'), data.get('expected_version')) != (
                active.pk if active else None, active.version if active else None,
            ):
                return JsonResponse({'error': STALE}, status=409)
            target = next((p for p in profiles if p.pk == profile_id and p.status == 'draft'), None)
            if target is None:
                return JsonResponse({'errors': {'profile_id': 'Entwurf nicht gefunden.'}}, status=400)
            _, error = _validated(target.config_json)
            if error:
                return error
            if active:
                active.status = PricingProfile.Status.RETIRED
                active.save(update_fields=['status'])
            target.status = PricingProfile.Status.ACTIVE
            target.valid_from = timezone.now()
            target.save(update_fields=['status', 'valid_from'])
    except IntegrityError:
        return JsonResponse({'error': STALE}, status=409)
    except ValueError:
        return JsonResponse({'errors': {'profile_id': 'Ungültige Profil-ID.'}}, status=400)
    return JsonResponse({'id': target.pk, 'version': target.version, 'status': target.status})


def _error(field, message='Ungültige Eingabe.'):
    return JsonResponse({'errors': {field: message}}, status=400)


def _body(request):
    if request.content_type == 'application/json':
        try:
            value = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None
    data = request.POST.dict()
    if 'included_item_ids' in request.POST and len(request.POST.getlist('included_item_ids')) > 1:
        data['included_item_ids'] = json.dumps([int(value) if value.isdigit() else value
                                                 for value in request.POST.getlist('included_item_ids')])
    for key in ('profile_id', 'expected_profile_version', 'expected_package_version',
                'included_item_ids', 'overrides_json', 'condition_json'):
        if key in data:
            try:
                if key == 'included_item_ids' and isinstance(data[key], str) and data[key].isdigit():
                    data[key] = [int(data[key])]
                else:
                    data[key] = json.loads(data[key])
            except (TypeError, ValueError):
                return None
    for key in ('is_active', 'is_public', 'is_default', 'is_required'):
        if key in data:
            data[key] = data[key] in ('true', 'on', '1')
    return data


def _money(value, field):
    if isinstance(value, (bool, list, dict)):
        raise ValidationError({field: 'Ungültiger Betrag.'})
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number.as_tuple().exponent < -2 or number >= 100000000:
            raise InvalidOperation
        return number
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({field: 'Ungültiger positiver Betrag.'})


def _validate_package(data, profile, pkg=None):
    name = data.get('name', pkg.name if pkg else None)
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValidationError({'name': 'Name erforderlich.'})
    price = _money(data.get('base_price', pkg.base_price if pkg else 0), 'base_price')
    for field in ('description', 'badge_label', 'badge_color'):
        if field in data and not isinstance(data[field], str):
            raise ValidationError({field: 'Ungültiger Text.'})
    for field in ('is_active', 'is_public'):
        if field in data and type(data[field]) is not bool:
            raise ValidationError({field: 'Ungültiger Wahrheitswert.'})
    ids = data.get('included_item_ids', list(pkg.included_items.values_list('pk', flat=True)) if pkg else [])
    if (not isinstance(ids, list) or any(type(i) is not int for i in ids)
            or len(ids) != len(set(ids)) or PriceItem.objects.filter(pk__in=ids).count() != len(ids)):
        raise ValidationError({'included_item_ids': 'Ungültige Posten.'})
    existing = PricingPackageOverride.objects.filter(profile=profile, package=pkg).first() if pkg else None
    overrides = data.get('overrides_json', existing.overrides_json if existing else {})
    if not isinstance(overrides, dict):
        raise ValidationError({'overrides_json': 'Ungültige Overrides.'})
    try:
        resolved = resolve_pricing_config(profile, SimpleNamespace(id=pkg.pk if pkg else 1,
            version=(pkg.version + 1 if pkg else 1), overrides_json=overrides))
        for field in ('saturday_percent', 'sunday_percent', 'holiday_percent'):
            factor = getattr(resolved, field)
            if factor.value is not None and not 0 <= factor.value <= 100:
                raise PricingValidationError('invalid_input', 'Prozentwert muss zwischen 0 und 100 liegen.', field)
    except (PricingValidationError, TypeError, AttributeError) as exc:
        if not isinstance(exc, PricingValidationError):
            raise ValidationError({'overrides_json': 'Ungültige Overrides.'}) from exc
        raise ValidationError({exc.field or 'overrides_json': exc.message}) from exc
    return name.strip(), price, ids, overrides


def _package_form_data(data):
    result = dict(data)
    if 'overrides_json' not in result:
        overrides = {}
        for field in _FIELDS:
            mode = data.get(field + '_mode')
            if mode:
                overrides[field] = {'mode': mode}
                if mode == 'override':
                    value = data.get(field + '_value')
                    if field in ('seasons', 'lead_time_tiers', 'guest_tiers', 'setup_minutes', 'teardown_minutes'):
                        try:
                            value = json.loads(value)
                        except (TypeError, ValueError):
                            raise ValidationError({field: 'Ungültiges JSON.'})
                    overrides[field]['value'] = value
        result['overrides_json'] = overrides
    return result


@staff_only
@require_http_methods(['GET', 'POST'])
def packages_page(request):
    if request.method == 'POST':
        return _save_package(request)
    profiles = list(PricingProfile.objects.order_by('-version', '-pk'))
    profile = PricingProfile.objects.filter(pk=request.GET.get('profile_id')).first() if request.GET.get('profile_id', '').isdigit() else (profiles[0] if profiles else None)
    pkg = PricingPackage.objects.filter(pk=request.GET.get('package_id')).first() if request.GET.get('package_id', '').isdigit() else None
    factors = []
    if profile:
        override = PricingPackageOverride.objects.filter(profile=profile, package=pkg).first() if pkg else None
        values = override.overrides_json if override else {}
        try:
            resolved = resolve_pricing_config(profile, SimpleNamespace(id=pkg.pk if pkg else 1,
                version=pkg.version if pkg else 1, overrides_json=values))
        except PricingValidationError:
            resolved = None
        for field in _FIELDS:
            factor = getattr(resolved, field) if resolved else None
            value = factor.value if factor else None
            editor_value = json.dumps([asdict(entry) for entry in value], ensure_ascii=False, default=str) if (
                field in ('seasons', 'lead_time_tiers', 'guest_tiers') and value is not None
            ) else str(value) if value is not None else None
            factors.append({'key': field, 'mode': values.get(field, {}).get('mode', 'inherit'),
                            'value': editor_value,
                            'source': factor.source if factor else 'invalid',
                            'profile_version': factor.profile_version if factor else None,
                            'package_version': factor.package_version if factor else None})
    return render(request, 'dj_admin/pricing_v2/packages.html', {
        'profiles': profiles, 'profile': profile, 'package': pkg, 'factors': factors,
        'packages': PricingPackage.objects.all(), 'items': PriceItem.objects.all(),
    })


def _save_package(request, pk=None):
    data = _body(request)
    if data is None:
        return _bad_body()
    try:
        data = _package_form_data(data)
        with transaction.atomic():
            profile = PricingProfile.objects.select_for_update().filter(pk=data.get('profile_id')).first()
            if profile is None or profile.status == 'retired':
                return _error('profile_id')
            pkg = get_object_or_404(PricingPackage.objects.select_for_update(), pk=pk) if pk else None
            if (type(data.get('expected_profile_version')) is not int or
                    data['expected_profile_version'] != profile.version or
                    (pk is not None and (type(data.get('expected_package_version')) is not int or
                                         data['expected_package_version'] != pkg.version)) or
                    (pk is None and data.get('expected_package_version') is not None)):
                return JsonResponse({'error': STALE}, status=409)
            name, price, ids, overrides = _validate_package(data, profile, pkg)
            previous = PricingPackageOverride.objects.filter(profile=profile, package=pkg).first() if pkg else None
            latest = _current(PricingProfile.objects.select_for_update())
            if latest.pk != profile.pk:
                return JsonResponse({'error': STALE}, status=409)
            publishing = pkg is not None and not pkg.is_public and data.get('is_public') is True
            if publishing:
                if profile.status != PricingProfile.Status.ACTIVE:
                    return _error('is_public', 'Paket erst nach Aktivierung des Profils veröffentlichen.')
                if (PricingPackageOverride.objects.filter(package=pkg).exists() and
                        not PricingPackageOverride.objects.filter(package=pkg, profile=profile).exists()):
                    return _error('is_public', 'Paket gehört zu einem anderen Profil; passendes Profil aktivieren.')
                if (pkg.name != name or pkg.base_price != price or
                        set(pkg.included_items.values_list('pk', flat=True)) != set(ids) or
                        overrides != (previous.overrides_json if previous else {}) or
                        any(getattr(pkg, field) != data[field] for field in
                            ('description', 'badge_label', 'badge_color', 'is_active', 'sort_order') if field in data)):
                    return _error('is_public', 'Veröffentlichung und Preisänderung getrennt speichern.')
                workflows = list(PricingWorkflow.objects.select_for_update().filter(
                    is_active=True, is_default=True)[:2])
                if len(workflows) != 1 or not isinstance(workflows[0].workflow_json, list):
                    return _error('is_public', 'Aktiven Standard-Workflow zuerst für dieses Paket konfigurieren.')
                blocks = [block for block in workflows[0].workflow_json
                          if isinstance(block, dict) and block.get('type') == 'package']
                if (not blocks or any(not isinstance(block.get('config', {}), dict) or
                        block.get('config', {}).get('package_id') not in (None, '', pkg.pk, str(pkg.pk))
                        for block in blocks)):
                    return _error('is_public', 'Aktiven Standard-Workflow zuerst auf diese Paket-ID umstellen.')
                pkg.is_public = True
                pkg.version += 1
                pkg.full_clean(exclude=['included_items'])
                pkg.save(update_fields=['is_public', 'version'])
                return JsonResponse({'id': pkg.pk, 'version': pkg.version, 'profile_id': profile.pk})
            core_changed = pkg is not None and (pkg.name != name or pkg.base_price != price or
                set(pkg.included_items.values_list('pk', flat=True)) != set(ids) or
                any(getattr(pkg, field) != data[field] for field in
                    ('description', 'badge_label', 'badge_color', 'is_active', 'is_public', 'sort_order')
                    if field in data))
            references = _references('packages', pkg.pk) if pkg else None
            shared = pkg is not None and ((pkg.is_public and pkg.is_active and
                PricingProfile.objects.filter(status='active').exists()) or
                PricingPackageOverride.objects.filter(package=pkg, profile__status='active').exists() or
                bool(references['workflows'] or references['revisions']))
            clone_package = core_changed and (shared or profile.status == 'active')
            override_changed = overrides != (previous.overrides_json if previous else {})
            if (profile.status == 'active' and (core_changed or override_changed)
                    or profile.status == 'draft' and shared and (core_changed or override_changed)):
                latest = _current(PricingProfile.objects.select_for_update())
                profile = PricingProfile.objects.create(name=latest.name, version=latest.version + 1,
                    status='draft', valid_from=timezone.now(), config_json=profile.config_json,
                    created_by=request.user)
                for existing in PricingPackageOverride.objects.filter(profile_id=data['profile_id']):
                    PricingPackageOverride.objects.create(profile=profile, package=existing.package,
                                                          overrides_json=existing.overrides_json)
            if clone_package:
                pkg.pk = None
                pkg._state.adding = True
            if pkg is None:
                pkg = PricingPackage()
            elif core_changed or override_changed and not shared:
                pkg.version += 1
            if not core_changed and not clone_package and pk is not None:
                if override_changed and not shared:
                    pkg.save(update_fields=['version'])
                PricingPackageOverride.objects.update_or_create(profile=profile, package=pkg,
                    defaults={'overrides_json': overrides})
                return JsonResponse({'id': pkg.pk, 'version': pkg.version,
                                     'profile_id': profile.pk})
            pkg.name, pkg.base_price = name, price
            for field in ('description', 'badge_label', 'badge_color', 'is_active', 'is_public', 'sort_order'):
                if field in data:
                    setattr(pkg, field, data[field])
            if clone_package:
                # Until the workflow explicitly selects this ID, never offer it publicly.
                pkg.is_public = False
            if pk is None and profile.status != PricingProfile.Status.ACTIVE:
                pkg.is_public = False
            pkg.full_clean(exclude=['included_items'])
            pkg.save()
            pkg.included_items.set(ids)
            PricingPackageOverride.objects.update_or_create(profile=profile, package=pkg,
                defaults={'overrides_json': overrides})
    except ValidationError as exc:
        return JsonResponse({'errors': exc.message_dict if hasattr(exc, 'message_dict') else {'package': exc.messages}}, status=400)
    except (IntegrityError, ValueError, TypeError, OverflowError):
        return _error('package')
    return JsonResponse({'id': pkg.pk, 'version': pkg.version, 'profile_id': profile.pk}, status=200 if pk else 201)


@staff_only
@require_http_methods(['GET', 'PUT', 'DELETE'])
def package_detail(request, pk):
    pkg = get_object_or_404(PricingPackage, pk=pk)
    if request.method == 'PUT':
        return _save_package(request, pk)
    if request.method == 'GET':
        return JsonResponse({'id': pkg.pk, 'name': pkg.name, 'version': pkg.version,
                             'base_price': str(pkg.base_price), 'is_public': pkg.is_public,
                             'is_active': pkg.is_active,
                             'included_item_ids': list(pkg.included_items.values_list('pk', flat=True))})
    references = _references('packages', pkg.pk)
    if any(references.values()) or PricingPackageOverride.objects.filter(package=pkg).exists():
        return JsonResponse({'error': 'Paket wird verwendet. Bitte deaktivieren.', 'references': references}, status=409)
    pkg.delete()
    return JsonResponse({'success': True})


_COMPONENTS = {'items': PriceItem, 'rules': PricingRule, 'formulas': PricingFormula}
_EDITABLE = {
    'items': ('name', 'category', 'description', 'price', 'is_default', 'is_required',
              'sort_order', 'is_active', 'is_public'),
    'rules': ('name', 'description', 'condition_json', 'effect_type', 'effect_value',
              'applies_to', 'sort_order', 'is_active'),
    'formulas': ('name', 'description', 'expression', 'mode', 'is_active'),
}


def _references(kind, pk):
    """References are snapshots/JSON identifiers, never mutable FK rewrites."""
    key = {'items': ('item_ids', 'selected_item_ids'),
           'rules': ('rule_ids', 'rule_ids'),
           'formulas': ('formula_id', 'formula_id'),
           'packages': ('package_id', 'package_id')}[kind]

    def contains(node, field):
        if isinstance(node, dict):
            for k, value in node.items():
                if k == field and (str(pk) == str(value) or
                                   isinstance(value, list) and any(str(pk) == str(v) for v in value)):
                    return True
                if contains(value, field):
                    return True
        elif isinstance(node, list):
            return any(contains(value, field) for value in node)
        return False

    included_packages = {str(identity) for identity in PricingPackage.objects.filter(
        included_items__pk=pk).values_list('pk', flat=True)} if kind == 'items' else set()
    required_item = kind == 'items' and PriceItem.objects.filter(pk=pk, is_required=True).exists()
    workflows = []
    for workflow in PricingWorkflow.objects.filter(is_active=True):
        blocks = workflow.workflow_json if isinstance(workflow.workflow_json, list) else []
        explicit = contains(blocks, key[0])
        implicit_rules = kind == 'rules' and any(
            isinstance(block, dict) and block.get('type') == 'rules'
            and 'rule_ids' not in (block.get('config') or {}) for block in blocks)
        included_item = kind == 'items' and any(
            isinstance(block, dict) and block.get('type') == 'package'
            and isinstance(block.get('config'), dict)
            and str(block['config'].get('package_id')) in included_packages
            for block in blocks)
        required = required_item and any(isinstance(block, dict) and block.get('type') == 'items'
                                         for block in blocks)
        if explicit or implicit_rules or included_item or required:
            workflows.append(workflow.pk)
    snapshot_section = {'items': 'items', 'rules': 'rules', 'formulas': 'formulas',
                        'packages': 'packages'}[kind]

    def in_snapshot(data):
        if not isinstance(data, dict):
            return False
        snapshot = data.get('catalog_snapshot', {})
        if not isinstance(snapshot, dict):
            return False
        section = snapshot.get(snapshot_section, {})
        if isinstance(section, dict) and str(pk) in section:
            return True
        if kind == 'items' and isinstance(snapshot.get('packages'), dict):
            return any(isinstance(package, dict) and (
                contains(package, 'included_item_ids') or contains(package, 'required_item_ids'))
                for package in snapshot['packages'].values())
        return False

    revisions = [r.pk for r in PriceCalculationRevision.objects.filter(
        status__in=('released', 'sent', 'send_failed', 'superseded'))
        if (kind == 'packages' and r.package_id_snapshot == pk or
            contains(r.input_json, key[1]) or contains(r.result_json, key[1])
            or in_snapshot(r.input_json) or in_snapshot(r.result_json))]
    packages = ([p.pk for p in PricingPackage.objects.filter(included_items__pk=pk)]
                if kind == 'items' else [])
    return {'workflows': workflows, 'revisions': revisions, 'packages': packages}


def _valid_conditions(value):
    from .price_engine import CONDITION_OPS
    fields = {'guest_count', 'duration_hours', 'date_weekday', 'event_type',
              'distance_km', 'date_month'}
    if not isinstance(value, list):
        return False
    for condition in value:
        if not isinstance(condition, dict) or set(condition) != {'field', 'op', 'value'}:
            return False
        if (not isinstance(condition['field'], str) or condition['field'] not in fields or
                not isinstance(condition['op'], str) or condition['op'] not in CONDITION_OPS):
            return False
        op, val = condition['op'], condition['value']
        if op == 'between' and (not isinstance(val, list) or len(val) != 2):
            return False
        if op == 'in' and not isinstance(val, list):
            return False
        if isinstance(val, (dict, list)) and op not in ('in', 'between'):
            return False
    return True


def _validate_component(kind, data, instance=None):
    model = _COMPONENTS[kind]
    instance = instance or model()
    for field in _EDITABLE[kind]:
        if field in data:
            setattr(instance, field, data[field])
    for field in ('is_active', 'is_public', 'is_default', 'is_required'):
        if field in data and type(data[field]) is not bool:
            raise ValidationError({field: 'Ungültiger Wahrheitswert.'})
    if kind == 'items':
        instance.price = _money(instance.price, 'price')
    if kind == 'rules':
        instance.effect_value = _money(instance.effect_value, 'effect_value') if instance.effect_type != 'percent_add' else instance.effect_value
        if instance.effect_type == 'percent_add':
            instance.effect_value = _money(instance.effect_value, 'effect_value')
            if instance.effect_value > 100:
                raise ValidationError({'effect_value': 'Prozentwert muss zwischen 0 und 100 liegen.'})
        if not _valid_conditions(instance.condition_json):
            raise ValidationError({'condition_json': 'Ungültige Bedingungen.'})
    if kind == 'formulas':
        from .price_engine import SafeFormulaEvaluator, FORMULA_VARS
        try:
            SafeFormulaEvaluator(FORMULA_VARS).evaluate(instance.expression,
                {key: 1 for key in FORMULA_VARS})
        except (ValueError, SyntaxError, TypeError, ZeroDivisionError, OverflowError) as exc:
            raise ValidationError({'expression': 'Ungültige Formel.'}) from exc
    instance.full_clean(exclude=['condition_json'] if kind == 'rules' else None)
    return instance


@staff_only
@require_http_methods(['GET', 'POST'])
def components_page(request):
    if request.method == 'GET':
        return render(request, 'dj_admin/pricing_v2/components.html', {
            'items': PriceItem.objects.all(), 'rules': PricingRule.objects.all(),
            'formulas': PricingFormula.objects.all(),
        })
    data = _body(request)
    if data is None:
        return _bad_body()
    kind = data.get('kind')
    if kind not in _COMPONENTS:
        return _error('kind')
    try:
        obj = _validate_component(kind, data)
        obj.save()
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        return JsonResponse({'errors': exc.message_dict if isinstance(exc, ValidationError) and hasattr(exc, 'message_dict') else {'component': 'Ungültige Eingabe.'}}, status=400)
    return JsonResponse({'id': obj.pk, 'kind': kind}, status=201)


@staff_only
@require_http_methods(['GET', 'PUT', 'DELETE'])
def component_detail(request, pk):
    data = request.GET if request.method == 'GET' else _body(request)
    if data is None:
        return _bad_body()
    kind = data.get('kind')
    if kind not in _COMPONENTS:
        return _error('kind')
    obj = get_object_or_404(_COMPONENTS[kind], pk=pk)
    references = _references(kind, pk)
    if request.method == 'GET':
        return JsonResponse({'id': pk, 'kind': kind, 'references': references,
                             'is_active': obj.is_active})
    if request.method == 'DELETE':
        if any(references.values()):
            return JsonResponse({'error': 'Komponente wird verwendet. Aktiven Workflow zuerst sicher ändern; sonst nur deaktivieren.',
                                 'references': references}, status=409)
        obj.delete()
        return JsonResponse({'success': True})
    if data.get('is_active') is False and obj.is_active and references['workflows']:
        return JsonResponse({'error': 'Aktiver Workflow benötigt diese Komponente.',
                             'references': references}, status=409)
    try:
        obj = _validate_component(kind, data, obj)
        obj.save()
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        return JsonResponse({'errors': exc.message_dict if isinstance(exc, ValidationError) and hasattr(exc, 'message_dict') else {'component': 'Ungültige Eingabe.'}}, status=400)
    return JsonResponse({'id': obj.pk, 'references': references})
