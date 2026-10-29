from django.db import migrations


def scope_rules(apps, schema_editor):
    Package = apps.get_model('wishlist', 'PricingPackage')
    Rule = apps.get_model('wishlist', 'PricingRule')
    owners = {}
    for package in Package.objects.exclude(workflow=None).select_related('workflow'):
        for block in package.workflow.workflow_json:
            if block.get('type') == 'rules':
                for pk in block.get('config', {}).get('rule_ids', []):
                    owners.setdefault(pk, set()).add(package.workflow_id)
    for pk, workflows in owners.items():
        rule = Rule.objects.filter(pk=pk).first()
        if rule and not any(c.get('field') == 'workflow_id' for c in rule.condition_json):
            rule.condition_json = [{'field': 'workflow_id', 'op': 'in', 'value': sorted(workflows)}, *rule.condition_json]
            rule.save(update_fields=['condition_json'])


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0026_calculator_workflow')]
    operations = [migrations.RunPython(scope_rules, migrations.RunPython.noop)]
