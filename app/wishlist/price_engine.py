import ast
import operator
import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}

CONDITION_OPS = {
    'eq': lambda a, b: a == b,
    'neq': lambda a, b: a != b,
    'gt': lambda a, b: a > b,
    'gte': lambda a, b: a >= b,
    'lt': lambda a, b: a < b,
    'lte': lambda a, b: a <= b,
    'in': lambda a, b: a in b if isinstance(b, (list, tuple)) else False,
    'between': lambda a, b: b[0] <= a <= b[1] if isinstance(b, (list, tuple)) and len(b) == 2 else False,
    'is_weekend': lambda a, b: a in (5, 6),
}


class SafeFormulaEvaluator:
    MAX_LEN = 500

    def __init__(self, allowed_vars):
        self.allowed_vars = set(allowed_vars)

    def evaluate(self, expression, variables):
        if len(expression) > self.MAX_LEN:
            raise ValueError(f"Formel zu lang (max {self.MAX_LEN} Zeichen)")
        tree = ast.parse(expression, mode='eval')
        return self._eval_node(tree.body, variables)

    def _eval_node(self, node, variables):
        if isinstance(node, ast.Expression):
            return self._eval_node(node.body, variables)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            if node.id not in self.allowed_vars:
                raise ValueError(f"Unbekannte Variable: {node.id}")
            return Decimal(str(variables.get(node.id, 0)))
        if isinstance(node, ast.BinOp):
            op_func = ALLOWED_OPERATORS.get(type(node.op))
            if not op_func:
                raise ValueError(f"Unerlaubter Operator: {type(node.op).__name__}")
            left = self._eval_node(node.left, variables)
            right = self._eval_node(node.right, variables)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("Division durch Null")
            return op_func(left, right)
        if isinstance(node, ast.UnaryOp):
            op_func = ALLOWED_OPERATORS.get(type(node.op))
            if not op_func:
                raise ValueError(f"Unerlaubter Operator: {type(node.op).__name__}")
            return op_func(self._eval_node(node.operand, variables))
        if isinstance(node, ast.IfExp):
            test = self._eval_node(node.test, variables)
            return self._eval_node(node.body, variables) if test else self._eval_node(node.orelse, variables)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            left = self._eval_node(node.left, variables)
            right = self._eval_node(node.comparators[0], variables)
            op = node.ops[0]
            if isinstance(op, ast.Gt): return Decimal(1) if left > right else Decimal(0)
            if isinstance(op, ast.GtE): return Decimal(1) if left >= right else Decimal(0)
            if isinstance(op, ast.Lt): return Decimal(1) if left < right else Decimal(0)
            if isinstance(op, ast.LtE): return Decimal(1) if left <= right else Decimal(0)
            if isinstance(op, ast.Eq): return Decimal(1) if left == right else Decimal(0)
            if isinstance(op, ast.NotEq): return Decimal(1) if left != right else Decimal(0)
        raise ValueError(f"Unerlaubter Ausdruck: {type(node).__name__}")


class RuleEvaluator:

    @staticmethod
    def build_context(event, offer=None):
        duration = Decimal('0')
        if event.time_start and event.time_end:
            from datetime import datetime, timedelta
            start_dt = datetime.combine(event.date or datetime.today(), event.time_start)
            end_dt = datetime.combine(event.date or datetime.today(), event.time_end)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            duration = Decimal(str(round((end_dt - start_dt).total_seconds() / 3600, 1)))

        guest_count = 0
        if offer and offer.guest_count:
            guest_count = offer.guest_count
        elif event.guest_count:
            guest_count = event.guest_count

        return {
            'guest_count': guest_count,
            'duration_hours': float(duration),
            'date_weekday': event.date.weekday() if event.date else 0,
            'event_type': event.event_type or '',
            'distance_km': float(event.distance_km or 0),
            'date_month': event.date.month if event.date else 0,
        }

    @staticmethod
    def evaluate(rule, context):
        conditions = rule.condition_json or []
        if not conditions:
            return True
        for cond in conditions:
            field = cond.get('field', '')
            op_name = cond.get('op', '')
            value = cond.get('value')
            actual = context.get(field)
            if actual is None:
                return False
            op_func = CONDITION_OPS.get(op_name)
            if not op_func:
                return False
            try:
                if not op_func(actual, value):
                    return False
            except (TypeError, ValueError):
                return False
        return True


FORMULA_VARS = {'base', 'guests', 'hours', 'distance', 'items_total', 'package_price'}


class PriceEngine:

    @staticmethod
    def calculate(event, package_id=None, selected_item_ids=None,
                  formula_id=None, custom_items=None, discount_percent=0,
                  offer_data=None, context_override=None):
        from .models import PriceItem, PricingPackage, PricingFormula, PricingRule

        result = {
            'package': None,
            'items': [],
            'custom_items': [],
            'offer_breakdown': None,
            'rules_applied': [],
            'formula_result': None,
            'subtotal': Decimal('0'),
            'discount_percent': Decimal(str(discount_percent or 0)),
            'discount_amount': Decimal('0'),
            'grand_total': Decimal('0'),
        }

        subtotal = Decimal('0')
        package_item_ids = set()

        # 1. Paket
        if package_id:
            try:
                pkg = PricingPackage.objects.get(pk=package_id, is_active=True)
                result['package'] = {'name': pkg.name, 'price': pkg.base_price}
                subtotal += pkg.base_price
                package_item_ids = set(pkg.included_items.values_list('pk', flat=True))
            except PricingPackage.DoesNotExist:
                pass

        # 2. PriceItems (exkl. im Paket enthaltene)
        if selected_item_ids:
            items = PriceItem.objects.filter(pk__in=selected_item_ids, is_active=True)
            for item in items:
                if item.pk not in package_item_ids:
                    result['items'].append({
                        'name': item.name, 'price': item.price, 'category': item.category
                    })
                    subtotal += item.price

        # 3. Custom Items
        for ci in (custom_items or []):
            try:
                price = Decimal(str(ci.get('price', 0)))
                name = ci.get('name', 'Eigener Posten')
                result['custom_items'].append({'name': name, 'price': price})
                subtotal += price
            except (InvalidOperation, TypeError):
                pass

        # 4. EventOffer
        offer = getattr(event, 'offer', None) if hasattr(event, 'offer') else None
        if offer_data:
            from .models import EventOffer
            offer = EventOffer(**offer_data)
        if offer:
            breakdown = offer.get_detailed_breakdown()
            result['offer_breakdown'] = {
                k: float(v) if isinstance(v, Decimal) else v
                for k, v in breakdown.items()
            }
            subtotal += breakdown['grand_total']

        pre_rules_subtotal = subtotal

        # 5. PricingRules
        context = RuleEvaluator.build_context(event, offer)
        if context_override:
            context.update(context_override)
        rules = PricingRule.objects.filter(is_active=True).order_by('sort_order')
        for rule in rules:
            if RuleEvaluator.evaluate(rule, context):
                amount = Decimal('0')
                if rule.effect_type == 'percent_add':
                    amount = pre_rules_subtotal * rule.effect_value / Decimal('100')
                elif rule.effect_type == 'flat_add':
                    amount = rule.effect_value
                elif rule.effect_type == 'flat_set':
                    amount = rule.effect_value - pre_rules_subtotal
                result['rules_applied'].append({
                    'name': rule.name,
                    'effect': rule.get_effect_type_display(),
                    'amount': amount,
                    'rule_id': rule.pk,
                })
                subtotal += amount

        # 6. Formel
        if formula_id:
            try:
                formula = PricingFormula.objects.get(pk=formula_id, is_active=True)
                evaluator = SafeFormulaEvaluator(FORMULA_VARS)
                variables = {
                    'base': float(pre_rules_subtotal),
                    'guests': context['guest_count'],
                    'hours': context['duration_hours'],
                    'distance': context['distance_km'],
                    'items_total': float(sum(i['price'] for i in result['items'])),
                    'package_price': float(result['package']['price']) if result['package'] else 0,
                }
                formula_result = evaluator.evaluate(formula.expression, variables)
                result['formula_result'] = formula_result
                subtotal = formula_result
            except PricingFormula.DoesNotExist:
                pass
            except ValueError as e:
                logger.warning("Formula evaluation error: %s", e)
                result['formula_result'] = None

        result['subtotal'] = subtotal

        # 7. Rabatt
        discount_pct = Decimal(str(discount_percent or 0))
        if discount_pct > 0:
            result['discount_amount'] = subtotal * discount_pct / Decimal('100')
            result['grand_total'] = subtotal - result['discount_amount']
        else:
            result['grand_total'] = subtotal

        return result

    @staticmethod
    def calculate_workflow(event, workflow_id, package_id=None,
                           selected_item_ids=None, formula_id=None,
                           discount_percent=0, custom_items=None, offer_data=None,
                           context_override=None):
        from .models import PricingWorkflow, PriceItem, PricingPackage, PricingFormula, PricingRule, EventOffer

        try:
            workflow = PricingWorkflow.objects.get(pk=workflow_id, is_active=True)
        except PricingWorkflow.DoesNotExist:
            return PriceEngine.calculate(event, package_id=package_id,
                        selected_item_ids=selected_item_ids, formula_id=formula_id,
                        custom_items=custom_items, discount_percent=discount_percent,
                        offer_data=offer_data, context_override=context_override)

        blocks = workflow.workflow_json or []
        if not blocks:
            return PriceEngine.calculate(event, package_id=package_id,
                        selected_item_ids=selected_item_ids, formula_id=formula_id,
                        custom_items=custom_items, discount_percent=discount_percent,
                        offer_data=offer_data, context_override=context_override)

        calc = getattr(event, 'price_calculation', None)

        pkg = None
        if package_id:
            try:
                pkg = PricingPackage.objects.get(pk=package_id, is_active=True)
            except PricingPackage.DoesNotExist:
                pass
        elif calc and calc.package:
            pkg = calc.package

        offer = None
        if offer_data:
            offer = EventOffer(**offer_data)
        elif hasattr(event, 'offer'):
            offer = getattr(event, 'offer', None)

        formula = None
        if formula_id:
            try:
                formula = PricingFormula.objects.get(pk=formula_id, is_active=True)
            except PricingFormula.DoesNotExist:
                pass
        elif calc and calc.formula:
            formula = calc.formula

        disc_pct = Decimal(str(discount_percent or 0))
        if not disc_pct and calc:
            disc_pct = calc.discount_percent or Decimal('0')

        items_qs = None
        if selected_item_ids is not None:
            items_qs = PriceItem.objects.filter(pk__in=selected_item_ids, is_active=True)
        elif calc:
            items_qs = calc.selected_items.filter(is_active=True)

        context = RuleEvaluator.build_context(event, offer)
        if context_override:
            context.update(context_override)
        package_item_ids = set(pkg.included_items.values_list('pk', flat=True)) if pkg else set()

        subtotal = Decimal('0')
        result = {
            'workflow': workflow.name,
            'workflow_id': workflow.pk,
            'steps': [],
            'package': None,
            'items': [],
            'custom_items': [],
            'offer_breakdown': None,
            'rules_applied': [],
            'formula_result': None,
            'discount_percent': disc_pct,
            'discount_amount': Decimal('0'),
            'grand_total': Decimal('0'),
        }

        for block in blocks:
            btype = block.get('type', '')
            block_config = block.get('config', {}) or {}
            step = {'type': btype, 'label': block.get('label', btype)}

            if btype == 'package':
                block_pkg_id = block_config.get('package_id')
                if block_pkg_id and not pkg:
                    try:
                        pkg = PricingPackage.objects.get(pk=block_pkg_id, is_active=True)
                        package_item_ids = set(pkg.included_items.values_list('pk', flat=True))
                    except PricingPackage.DoesNotExist:
                        pass

            if btype == 'formula':
                block_formula_id = block_config.get('formula_id')
                if block_formula_id and not formula:
                    try:
                        formula = PricingFormula.objects.get(pk=block_formula_id, is_active=True)
                    except PricingFormula.DoesNotExist:
                        pass

            if btype == 'package' and pkg:
                step['amount'] = pkg.base_price
                step['detail'] = pkg.name
                subtotal += pkg.base_price
                result['package'] = {'name': pkg.name, 'price': pkg.base_price}

            elif btype == 'items':
                items_total = Decimal('0')
                items_list = []
                if items_qs:
                    for item in items_qs:
                        if item.pk not in package_item_ids:
                            items_list.append({'name': item.name, 'price': item.price, 'category': item.category})
                            items_total += item.price
                for ci in (custom_items or []):
                    try:
                        price = Decimal(str(ci.get('price', 0)))
                        name = ci.get('name', 'Eigener Posten')
                        items_list.append({'name': name, 'price': price})
                        result['custom_items'].append({'name': name, 'price': price})
                        items_total += price
                    except (InvalidOperation, TypeError):
                        pass
                step['amount'] = items_total
                step['detail'] = f"{len(items_list)} Posten"
                subtotal += items_total
                result['items'] = items_list

            elif btype == 'offer' and offer:
                breakdown = offer.get_detailed_breakdown()
                offer_total = breakdown['grand_total']
                step['amount'] = offer_total
                step['detail'] = f"{breakdown.get('guest_count', 0)} Gäste"
                subtotal += offer_total
                result['offer_breakdown'] = {
                    k: float(v) if isinstance(v, Decimal) else v
                    for k, v in breakdown.items()
                }

            elif btype == 'rules':
                rules_total = Decimal('0')
                pre_rules = subtotal
                for rule in PricingRule.objects.filter(is_active=True).order_by('sort_order'):
                    if RuleEvaluator.evaluate(rule, context):
                        amount = Decimal('0')
                        if rule.effect_type == 'percent_add':
                            amount = pre_rules * rule.effect_value / Decimal('100')
                        elif rule.effect_type == 'flat_add':
                            amount = rule.effect_value
                        elif rule.effect_type == 'flat_set':
                            amount = rule.effect_value - pre_rules
                        result['rules_applied'].append({
                            'name': rule.name,
                            'effect': rule.get_effect_type_display(),
                            'amount': amount,
                            'rule_id': rule.pk,
                        })
                        rules_total += amount
                step['amount'] = rules_total
                step['detail'] = f"{len(result['rules_applied'])} Regeln"
                subtotal += rules_total

            elif btype == 'formula' and formula:
                evaluator = SafeFormulaEvaluator(FORMULA_VARS)
                variables = {
                    'base': float(subtotal),
                    'guests': context['guest_count'],
                    'hours': context['duration_hours'],
                    'distance': context['distance_km'],
                    'items_total': float(sum(i['price'] for i in result['items'])),
                    'package_price': float(pkg.base_price) if pkg else 0,
                }
                try:
                    formula_val = evaluator.evaluate(formula.expression, variables)
                    step['amount'] = formula_val
                    step['detail'] = formula.name
                    result['formula_result'] = formula_val
                    subtotal = formula_val
                except ValueError:
                    step['amount'] = Decimal('0')

            elif btype == 'discount':
                block_pct = Decimal(str(block.get('config', {}).get('percent', 0) or 0))
                effective_pct = block_pct if block_pct > 0 else disc_pct
                if effective_pct > 0:
                    discount_amt = subtotal * effective_pct / Decimal('100')
                    step['amount'] = -discount_amt
                    step['detail'] = f"{effective_pct}%"
                    subtotal -= discount_amt
                    result['discount_amount'] = discount_amt
                else:
                    step['amount'] = Decimal('0')

            else:
                step['amount'] = Decimal('0')

            result['steps'].append(step)

        result['subtotal'] = subtotal + result['discount_amount']
        result['grand_total'] = subtotal
        return result
