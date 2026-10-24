"""只解释已有运行证据；不读取对照聊天正文，不把逻辑前缀当作厂商缓存。"""
import json
import hashlib


def object_value(value):
    try:
        data = json.loads(value) if isinstance(value, str) else value
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError, RecursionError):
        return {}


def capture_versions(instance, snapshot, settings):
    character = object_value(instance['character_config'])
    profile, binding = snapshot.model.profile, settings['binding']
    def checksum(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    return {
        'character': {'id': instance['character_id'], 'name': character.get('name'),
            'revision': instance['character_revision'], 'adjustment_revision': instance.get('adjustment_revision', 0),
            'worldbooks': [{'id': item['id'], 'revision': item['revision']} for item in character.get('worldbooks', [])],
            'effective_hash': checksum(character)},
        'preset': {'id': profile.preset_id if profile else None, 'revision': profile.revision if profile else None,
            'local_overrides': bool(profile and profile.overrides), 'effective_hash': checksum(snapshot.preset_config.model_dump())},
        'strategy': {'id': binding['strategy_id'], 'revision': binding['strategy_revision'],
            'binding_revision': binding['revision'], 'rule_revision': settings['revision'],
            'mode': settings['effective']['strategy'], 'local_overrides': bool(settings['overrides']),
            'effective_hash': checksum(settings['effective'])}}


def number(value):
    return value if type(value) is int and value >= 0 else None


def explain_run(database, run, steps):
    report, model = object_value(run['context_json']), object_value(run['model_json'])
    versions, trace = object_value(model.get('versions')), object_value(report.get('change_trace'))
    previous = None
    baseline = number(trace.get('baseline_run_id'))
    if baseline and baseline < run['id']:
        previous = database.one('''SELECT id,created_at,
            CASE WHEN json_valid(model_json) THEN json_extract(model_json,'$.versions') END AS versions
            FROM rp_runs WHERE id=? AND instance_id=? AND status='completed' AND context_deleted_at IS NULL''',
            (baseline, run['instance_id']))
    changes, operations = [], []
    old = object_value(previous['versions']) if previous else {}
    if versions and old:
        for key, label in (('character', '角色与世界书'), ('preset', '模型预设'), ('strategy', '上下文策略')):
            if old.get(key) != versions.get(key):
                changes.append({'label': label, 'before': old.get(key), 'after': versions.get(key)})
    if previous:
        # 只展示本线期间的明确操作，不将时间相近推断为缓存失效的根因。
        operations = database.all('''SELECT id,created_at,action FROM audit_events
            WHERE created_at>? AND created_at<=?
            AND CASE WHEN json_valid(details_json) THEN json_extract(details_json,'$.instance_id') END=?
            AND action IN ('memory.saved','memory.protection','memory.superseded','memory.proposal_reviewed',
                'evidence.status','conversation.deleted','context.rules_saved','life.extensions_saved','instance.character_synced')
            ORDER BY id DESC LIMIT 21''', (previous['created_at'], run['created_at'], run['instance_id']))
    inputs, outputs, paired_inputs, cached, reported, pairs, output_reports = 0, 0, 0, 0, 0, 0, 0
    repairs, attempts, budgets = [], [], []
    for step in steps:
        response = object_value(step['response_json'])
        usage = object_value(response.get('token_usage'))
        incoming, outgoing, read = (number(usage.get(key)) for key in ('input_tokens', 'output_tokens', 'cache_read_tokens'))
        if incoming is not None:
            inputs += incoming; reported += 1
        if outgoing is not None:
            outputs += outgoing; output_reports += 1
        if incoming is not None and incoming > 0 and read is not None and read <= incoming:
            paired_inputs += incoming; cached += read; pairs += 1
        repair = number(response.get('repair_attempt'))
        if repair is not None: repairs.append(repair)
        started = number(object_value(response.get('attempts')).get('started'))
        if started is not None: attempts.append(started)
        budget = object_value(response.get('context_budget'))
        if budget:
            budgets.append({'step': step['step'], 'method': object_value(budget.get('budget_calibration')).get('method', 'coarse'),
                'estimated_input_tokens': budget.get('estimated_input_tokens'), 'budget_input_tokens': budget.get('budget_input_tokens'),
                'reason': object_value(budget.get('budget_calibration')).get('reason')})
    transition = object_value(report.get('history_transition')) or None
    return {'versions': versions or None, 'baseline_run_id': previous['id'] if previous else None,
        'version_changes': changes, 'trace': trace or None, 'first_changed_layer': report.get('first_changed_layer'),
        'estimated_reusable_prefix_tokens': report.get('estimated_reusable_prefix_tokens'),
        'transition': transition, 'adopted': run['status'] == 'completed' and run['context_deleted_at'] is None,
        'memory_outcomes': report.get('memory_outcomes', []),
        'planning': {'reason': report.get('trim_reason'), 'would_trim': report.get('would_trim'),
            'would_summarize': report.get('would_summarize')},
        'operations': operations[:20], 'operations_limited': len(operations) > 20, 'budgets': budgets,
        'usage': {'requests': len(steps), 'reported_input_requests': reported, 'reported_output_requests': output_reports,
            'input_tokens': inputs if reported else None, 'output_tokens': outputs if output_reports else None,
            'paired_cache_requests': pairs, 'paired_input_tokens': paired_inputs if pairs else None,
            'cache_read_tokens': cached if pairs else None, 'cache_ratio': cached / paired_inputs if paired_inputs else None,
            'repair_attempts': max(repairs) if repairs else None,
            'started_attempts': sum(attempts) if attempts else None, 'attempt_reports': len(attempts)},
        'boundary': '逻辑前缀是本地估算；真实命中仅来自接口用量。接口TTL、缓存路由及未返回用量不能由此推断。'}
