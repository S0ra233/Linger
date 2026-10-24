"""只汇总已有运行证据；价格是管理员当前假设，不回填厂商账单。"""
from collections import defaultdict
from decimal import Decimal
import json
from time import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..storage.configuration import RevisionConflict
from .usage import TokenUsage, aggregate_attempt_usage


FIELDS = tuple(TokenUsage.__dataclass_fields__)
COST_PARTS = ('output', 'cache_read', 'uncached_input', 'cache_write', 'flat_input')
MAX_RECORDS = 10000
SOURCES = ('reply', 'life', 'summary', 'check', 'embedding_document', 'embedding_query', 'cache_keepalive')


class PriceRule(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    connection_id: str = Field(min_length=1, max_length=80)
    model_id: str = Field(min_length=1, max_length=80)
    mode: Literal['flat', 'read_discount', 'split'] = 'read_discount'
    input_per_million: str | None = None
    output_per_million: str | None = None
    cache_read_per_million: str | None = None
    cache_write_per_million: str | None = None

    @field_validator('input_per_million', 'output_per_million', 'cache_read_per_million', 'cache_write_per_million')
    @classmethod
    def rate(cls, value):
        if value is None or value.strip() == '':
            return None
        value = value.strip()
        import re
        if re.fullmatch(r'\d{1,9}(\.\d{1,8})?', value) is None:
            raise ValueError('单价需为非负十进制数，最多 9 位整数和 8 位小数；留空表示未知。')
        return format(Decimal(value), 'f')


class PricesInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=0)
    currency: str = Field(default='CNY', pattern='^[A-Z]{3}$')
    rules: list[PriceRule] = Field(default_factory=list, max_length=300)

    @model_validator(mode='after')
    def unique(self):
        keys = [(rule.connection_id, rule.model_id) for rule in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError('同一连接的模型条目不能重复设置价格。')
        return self


def obj(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value) if value else {}
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def number(value):
    return value if type(value) is int and value >= 0 else None


def embedding_usage(raw, protocol):
    # 协议来自已保存空间／检查快照，不从模型名字或当前连接猜历史协议。
    raw = obj(raw)
    key = {'embedding_openai': 'prompt_tokens', 'embedding_ollama': 'input_tokens',
           'embedding_gemini': 'promptTokenCount'}.get(protocol)
    input_tokens = number(raw.get(key)) if key else None
    total_key = {'embedding_openai': 'total_tokens', 'embedding_gemini': 'totalTokenCount'}.get(protocol)
    total = number(raw.get(total_key)) if total_key else None
    # Embedding 没有生成输出；有明确输入数时合计可由输入得出，不宣称接口报告了输出零。
    return {'input_tokens': input_tokens, 'total_tokens': total if total is not None else input_tokens}


def estimate(record, rule):
    result = {'estimated_cost': None, 'cost_reason': None, 'cost_parts': None, 'cache_comparison': None}
    if record['attempts'] == 0:
        return {**result, 'estimated_cost': '0', 'cost_parts': {}}
    if rule is None:
        return {**result, 'cost_reason': '未设置价格'}
    usage, parts = record['usage'], []
    inp, out = usage['input_tokens'], usage['output_tokens']
    if inp is None or not record['is_embedding'] and out is None:
        return {**result, 'cost_reason': '输入或输出用量缺失'}
    if record['is_embedding'] and rule['mode'] != 'flat':
        return {**result, 'cost_reason': 'Embedding 仅支持输入总量计价'}
    read, write = usage['cache_read_tokens'], usage['cache_write_tokens']
    if (read is not None and read > inp or write is not None and write > inp
            or read is not None and write is not None and read + write > inp):
        return {**result, 'cost_reason': '缓存计数与输入总量不一致'}
    if rule['mode'] == 'flat':
        parts.append(('flat_input', inp, rule['input_per_million']))
    elif rule['mode'] == 'read_discount':
        if read is None:
            return {**result, 'cost_reason': '缓存读取用量缺失'}
        parts.extend((('uncached_input', inp - read, rule['input_per_million']),
                      ('cache_read', read, rule['cache_read_per_million'])))
    else:
        if read is None or write is None:
            return {**result, 'cost_reason': '缓存读写用量缺失'}
        parts.extend((('uncached_input', inp - read - write, rule['input_per_million']),
                      ('cache_read', read, rule['cache_read_per_million']),
                      ('cache_write', write, rule['cache_write_per_million'])))
    if not record['is_embedding']:
        parts.append(('output', out, rule['output_per_million']))
    if any(rate is None and tokens != 0 for _, tokens, rate in parts):
        return {**result, 'cost_reason': '所需单价缺失'}
    amounts = {name: Decimal(tokens) * Decimal(rate) / Decimal(1000000) if tokens else Decimal(0)
               for name, tokens, rate in parts}
    result.update(estimated_cost=format(sum(amounts.values(), Decimal(0)), 'f'),
                  cost_parts={name: format(value, 'f') for name, value in amounts.items()})
    # 仅对同量已返回输入作反事实计价；不是关闭缓存／保活后的整段净收益预测。
    if not record['is_embedding'] and rule['mode'] != 'flat' and (inp == 0 or rule['input_per_million'] is not None):
        cold = Decimal(inp) * Decimal(rule['input_per_million']) / Decimal(1000000) if inp else Decimal(0)
        actual = sum((value for name, value in amounts.items() if name != 'output'), Decimal(0))
        result['cache_comparison'] = {'input_cost': format(actual, 'f'), 'uncached_input_cost': format(cold, 'f'),
                                      'difference': format(cold - actual, 'f')}
    return result


def totals(records):
    result = {'records': len(records), 'usage': {}}
    attempts = [row['attempts'] for row in records if row['attempts'] is not None]
    # 有逐尝试用量时按实际覆盖计数；旧记录的未报告失败仍保持未知。
    unknown_attempts = sum(row['unreported_attempts'] for row in records if row['unreported_attempts'] is not None)
    full_attempt_coverage = len(attempts) == len(records) and unknown_attempts == 0
    for field in FIELDS:
        applicable = [row for row in records if field in {'input_tokens', 'total_tokens'} or not row['is_embedding']]
        known = [row['usage'][field] for row in applicable if row['usage'][field] is not None]
        result['usage'][field] = {'known': sum(known) if known else None, 'reported': len(known),
                                  'missing': len(applicable) - len(known), 'applicable': len(applicable),
                                  'total': sum(known) if known and len(known) == len(applicable) and full_attempt_coverage else None}
    result['attempts'] = {'known': sum(attempts) if attempts else None,
                          'untracked_records': len(records) - len(attempts), 'unreported': unknown_attempts}
    amounts = [Decimal(row['estimated_cost']) for row in records if row['estimated_cost'] is not None]
    missing = len(records) - len(amounts)
    amount = format(sum(amounts, Decimal(0)), 'f') if amounts else None
    result['cost'] = {'known': amount, 'priced_records': len(amounts), 'missing_records': missing,
                      'total': amount if amounts and missing == 0 and full_attempt_coverage else None}
    components = {}
    for name in COST_PARTS:
        values = [Decimal(row['cost_parts'][name]) for row in records if row['cost_parts'] is not None and name in row['cost_parts']]
        components[name] = format(sum(values, Decimal(0)), 'f') if values else None
    result['cost']['parts'] = components
    compared = [row['cache_comparison'] for row in records if row['cache_comparison'] is not None]
    result['cache_comparison'] = {'records': len(compared), 'excluded_records': len(records) - len(compared),
        **{name: format(sum((Decimal(row[name]) for row in compared), Decimal(0)), 'f') if compared else None
           for name in ('input_cost', 'uncached_input_cost', 'difference')}}
    paired = [row for row in records if not row['is_embedding'] and row['usage']['input_tokens'] is not None
              and row['usage']['cache_read_tokens'] is not None and row['usage']['cache_read_tokens'] <= row['usage']['input_tokens']]
    denominator = sum(row['usage']['input_tokens'] for row in paired)
    result['cache'] = {'paired_records': len(paired),
                       'ratio': sum(row['usage']['cache_read_tokens'] for row in paired) / denominator if denominator else None}
    return result


class UsageReports:
    def __init__(self, database, events):
        self.database, self.events = database, events

    def prices(self):
        row = self.database.one("SELECT * FROM configurations WHERE key='usage_prices'")
        return {**(obj(row['value_json']) if row else {'currency': 'CNY', 'rules': []}),
                'revision': row['revision'] if row else 0, 'updated_at': row['updated_at'] if row else None}

    def save_prices(self, body):
        now = time()
        with self.database.transaction() as tx:
            row = tx.execute("SELECT revision FROM configurations WHERE key='usage_prices'").fetchone()
            if (row['revision'] if row else 0) != body.revision:
                raise RevisionConflict('单价已被另一页面修改，请重新载入后再保存。')
            tx.execute("""INSERT INTO configurations VALUES ('usage_prices',?,?,?) ON CONFLICT(key)
                DO UPDATE SET value_json=excluded.value_json,revision=excluded.revision,updated_at=excluded.updated_at""",
                       (body.model_dump_json(exclude={'revision'}), body.revision + 1, now))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (now, 'usage.prices_saved', json.dumps({'revision': body.revision + 1, 'rules': len(body.rules), 'currency': body.currency})))
        self.events.publish('usage')
        return self.prices()

    def catalog(self):
        result = []
        for row in self.database.all('SELECT id,config_json,archived FROM model_connections ORDER BY created_at'):
            config = obj(row['config_json'])
            for model in config.get('models', []):
                result.append({'connection_id': row['id'], 'model_id': model['id'], 'connection_name': config['name'],
                               'model': model['model'], 'protocol': config['protocol'], 'archived': bool(row['archived'])})
        return result

    def recent_cache(self, instance_id, binding, limit=20):
        """只取当前主叙事条目的实际对话请求；不混入摘要、保活和检查。"""
        result = {'requests': 0, 'paired_requests': 0, 'missing_requests': 0, 'ratio': None,
                  'input_tokens': None, 'cache_read_tokens': None, 'latest_at': None,
                  'unreported_attempts': 0, 'untracked_requests': 0, 'limit': limit, 'model': None,
                  'binding': binding, 'status': 'no_model' if binding is None else 'no_usage'}
        if binding is None:
            return result
        current = next((item for item in self.catalog() if item['connection_id'] == binding['connection_id']
                        and item['model_id'] == binding['model_id']), None)
        if current is None:
            return result
        result['model'] = current['model']
        rows = self.database.all('''SELECT s.created_at,json_extract(s.response_json,'$.token_usage') AS usage_json,
            json_extract(s.response_json,'$.attempts') AS attempts_json,json_extract(r.model_json,'$.model') AS model
            FROM rp_steps s JOIN rp_runs r ON r.id=s.run_id WHERE r.instance_id=? AND r.trigger_kind='input'
            AND json_extract(r.model_json,'$.connection_id')=? AND json_extract(r.model_json,'$.model_id')=?
            AND json_extract(r.model_json,'$.model')=? AND json_extract(r.model_json,'$.protocol')=?
            AND (json_extract(s.response_json,'$.attempts.started')>0 OR
                (json_extract(s.response_json,'$.attempts.tracked') IS NOT 1 AND s.response_json IS NOT NULL))
            ORDER BY s.created_at DESC,s.id DESC LIMIT ?''', (instance_id, binding['connection_id'], binding['model_id'], current['model'], current['protocol'], limit))
        denominator = numerator = paired = 0
        for row in rows:
            attempts = obj(row['attempts_json'])
            usage, reported = aggregate_attempt_usage(obj(row['usage_json']), attempts)
            inp, read = number(usage.get('input_tokens')), number(usage.get('cache_read_tokens'))
            if inp is not None and read is not None and read <= inp:
                denominator += inp; numerator += read; paired += 1
            count = number(attempts.get('started')) if attempts.get('tracked') is True else None
            if count is None:
                result['untracked_requests'] += 1
            else:
                result['unreported_attempts'] += max(0, count - reported)
        result.update(requests=len(rows), paired_requests=paired, missing_requests=len(rows) - paired,
            ratio=numerator / denominator if denominator else None, input_tokens=denominator if paired else None,
            cache_read_tokens=numerator if paired else None, latest_at=rows[0]['created_at'] if rows else None,
            status='reported' if denominator else 'no_usage')
        return result

    def report(self, start, end, source=None, connection_id=None, model_id=None, page=1, instance_id=None):
        if not start < end or end - start > 31 * 86400:
            raise ValueError('请选择不超过 31 天的起止时间。')
        if source and source not in SOURCES:
            raise ValueError('用量来源无效。')
        prices = self.prices()
        # SQL 只抽取数值用量和身份字段，不把正文、提示词或工具结果装入统计内存。
        sql = '''SELECT * FROM (
            SELECT 'rp' AS origin,s.id AS source_id,s.created_at AS at,s.status,r.instance_id,s.run_id,
                CASE r.trigger_kind WHEN 'life' THEN 'life' ELSE 'reply' END AS purpose,
                json_extract(r.model_json,'$.connection_id') AS connection_id,json_extract(r.model_json,'$.model_id') AS model_id,
                json_extract(r.model_json,'$.model') AS model,json_extract(r.model_json,'$.protocol') AS protocol,
                json_extract(s.response_json,'$.token_usage') AS normalized,json_extract(s.response_json,'$.attempts') AS attempts,
                NULL AS raw_usage,0 AS is_embedding
            FROM rp_steps s JOIN rp_runs r ON r.id=s.run_id WHERE s.created_at>=? AND s.created_at<?
            UNION ALL
            SELECT 'summary',s.id,s.created_at,s.status,s.instance_id,s.run_id,'summary',
                json_extract(s.model_json,'$.connection_id'),json_extract(s.model_json,'$.model_id'),
                json_extract(s.model_json,'$.model'),json_extract(s.model_json,'$.protocol'),
                json_extract(s.usage_json,'$.token_usage'),json_extract(s.usage_json,'$.attempts'),NULL,0
            FROM rp_summaries s WHERE s.created_at>=? AND s.created_at<?
            UNION ALL
            SELECT 'check',c.id,c.created_at,c.status,NULL,NULL,'check',c.connection_id,c.model_id,
                json_extract(c.result_json,'$.model.model'),json_extract(c.result_json,'$.model.protocol'),
                CASE WHEN json_type(c.result_json,'$.dimensions') IS NULL THEN json_extract(c.result_json,'$.usage') ELSE NULL END,
                json_extract(c.result_json,'$.attempts'),json_extract(c.result_json,'$.usage'),
                CASE WHEN json_type(c.result_json,'$.dimensions') IS NOT NULL OR json_extract(c.result_json,'$.model.protocol') LIKE 'embedding_%' THEN 1 ELSE 0 END
            FROM model_checks c WHERE c.created_at>=? AND c.created_at<?
            UNION ALL
            SELECT 'embedding',c.id,c.created_at,c.status,c.instance_id,c.run_id,
                CASE c.purpose WHEN 'document' THEN 'embedding_document' ELSE 'embedding_query' END,
                json_extract(s.config_json,'$.connection_id'),json_extract(s.config_json,'$.model_id'),
                json_extract(s.config_json,'$.model'),'embedding_'||json_extract(s.config_json,'$.protocol'),NULL,
                json_extract(c.usage_json,'$.attempts'),
                CASE WHEN json_type(c.usage_json,'$.attempts') IS NOT NULL THEN json_extract(c.usage_json,'$.usage') ELSE c.usage_json END,1
            FROM embedding_calls c LEFT JOIN embedding_spaces s ON s.id=c.space_id WHERE c.created_at>=? AND c.created_at<?
            UNION ALL
            SELECT 'cache',c.id,c.created_at,c.status,c.instance_id,NULL,'cache_keepalive',
                c.connection_id,c.model_id,c.model,c.protocol,json_extract(c.usage_json,'$.token_usage'),
                json_extract(c.usage_json,'$.attempts'),NULL,0
            FROM cache_calls c WHERE c.created_at>=? AND c.created_at<?
        ) WHERE (? IS NULL OR purpose=?) AND (? IS NULL OR connection_id=?) AND (? IS NULL OR model_id=?)
        AND (? IS NULL OR instance_id=?)
        ORDER BY at DESC,origin,source_id DESC LIMIT ?'''
        rows = self.database.all(sql, (start, end) * 5 + (source, source, connection_id, connection_id, model_id, model_id, instance_id, instance_id, MAX_RECORDS + 1))
        if len(rows) > MAX_RECORDS:
            raise ValueError('所选范围超过一万条调用记录，请缩短时间或筛选来源／模型；未返回截断的总数。')
        rules = {(rule['connection_id'], rule['model_id']): rule for rule in prices['rules']}
        groups, records = defaultdict(list), []
        for row in rows:
            embedding = bool(row['is_embedding'])
            raw = embedding_usage(row['raw_usage'], row['protocol']) if embedding else obj(row['normalized'])
            attempt = obj(row['attempts'])
            usage, reported = aggregate_attempt_usage(raw, attempt)
            count = number(attempt.get('started')) if attempt.get('tracked') is True else None
            record = {key: value for key, value in row.items() if key not in {'normalized', 'raw_usage', 'attempts'}}
            record.update(usage=usage, is_embedding=embedding, attempts=count)
            record.update(estimate(record, rules.get((row['connection_id'], row['model_id']))))
            record['unreported_attempts'] = max(0, count - reported) if count is not None else None
            records.append(record)
            groups[(row['connection_id'], row['model_id'], row['model'], row['protocol'])].append(record)
        group_rows = []
        for (connection, model_id, model, protocol), items in groups.items():
            group_rows.append({'connection_id': connection, 'model_id': model_id, 'model': model, 'protocol': protocol,
                               **totals(items)})
        return {'start': start, 'end': end, 'price_revision': prices['revision'], 'currency': prices['currency'],
                'calculated_at': time(), 'summary': totals(records), 'groups': group_rows,
                'items': records[(page - 1) * 50:page * 50], 'page': page, 'page_size': 50,
                'has_more': page * 50 < len(records)}
