"""从有效来源定位完整原文轮次；只读、有限返回，不改写对话或检索工作集。"""
import json

from ..roleplay.budget import _estimate
from ..roleplay.conversations import conversation_cte


class ConversationWindows:
    def __init__(self, evidence):
        self.evidence, self.store, self.database = evidence, evidence.store, evidence.database

    def bounds(self, instance_id, through=None, run_id=None):
        self.store.instance(instance_id)
        cutoff = None
        if run_id is not None:
            run = self.database.one('SELECT instance_id,input_through,created_at FROM rp_runs WHERE id=?', (run_id,))
            if not run or run['instance_id'] != instance_id or run['input_through'] != through:
                raise ValueError('原文读取不属于本轮运行范围。')
            cutoff = run['created_at']
        maximum = self.database.one('''SELECT
            (SELECT coalesce(max(id),0) FROM inbound_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL) AS input_id,
            (SELECT coalesce(max(id),0) FROM sent_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL) AS sent_id''',
            (instance_id, instance_id))
        maximum['input_id'] = min(maximum['input_id'], through) if through is not None else maximum['input_id']
        return maximum, cutoff

    def read(self, instance_id, query, *, through=None, run_id=None, page=None, paged=False,
             anchor_input=None, anchor_sent=None):
        bounds, cutoff = self.bounds(instance_id, through, run_id)
        if (anchor_input is None) != (anchor_sent is None):
            raise ValueError('查看范围锚点须同时提供。')
        if anchor_input is not None:
            bounds = {'input_id': min(bounds['input_id'], anchor_input), 'sent_id': min(bounds['sent_id'], anchor_sent)}
        source = self.evidence.read(instance_id, query.kind, query.id, through)
        if source['validity']['invalid'] or source.get('deleted'):
            raise ValueError('来源已失效或删除，不能用它读取附近原文。')
        # 已送达才是对话事实；本轮不读取启动之后的回执或更晚输入产生的回复。
        scope = '''AND EXISTS (SELECT 1 FROM rp_outbox o JOIN rp_runs r ON r.id=o.run_id
            WHERE o.id=sent_messages.outbox_id AND r.instance_id=sent_messages.instance_id
            AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL'''
        extras = ()
        if through is not None:
            scope += ' AND r.input_through<=?'
            extras += (through,)
        if cutoff is not None:
            scope += ' AND sent_messages.confirmed_at<=?'
            extras += (cutoff,)
        cte = conversation_cte(scope + ')')
        args = (instance_id, bounds['input_id'], instance_id, bounds['sent_id'], *extras)
        anchor = self.database.one(cte + 'SELECT turn FROM numbered WHERE kind=? AND id=?', (*args, query.kind, query.id))
        if not anchor:
            raise ValueError('来源不在当前可读取的对话范围。')
        total = self.database.one(cte + 'SELECT max(turn) AS n FROM numbered', args)['n']
        size = query.before_turns + query.after_turns + 1
        if paged:
            page = min(page or ((anchor['turn'] - 1) // size + 1), (total + size - 1) // size)
            lower, upper = (page - 1) * size + 1, min(total, page * size)
        else:
            lower, upper = max(1, anchor['turn'] - query.before_turns), min(total, anchor['turn'] + query.after_turns)
        # 分组时保留失效消息的位置，整轮略过，不能过滤之后将两轮拼成新的一轮。
        metadata = self.database.all(cte + '''SELECT n.turn,count(*) AS messages,
            sum(length(CAST(CASE WHEN n.kind='input' THEN m.parts_json ELSE coalesce(s.text,'')||coalesce(s.parts_json,'') END AS BLOB))) AS bytes,
            max(coalesce(es.invalid,0)) AS invalid
            FROM numbered n LEFT JOIN inbound_messages m ON n.kind='input' AND m.id=n.id
            LEFT JOIN sent_messages s ON n.kind='sent' AND s.id=n.id
            LEFT JOIN evidence_status es ON es.instance_id=? AND es.kind=n.kind AND es.source_id=n.id
            WHERE n.turn BETWEEN ? AND ? GROUP BY n.turn''', (*args, instance_id, lower, upper))
        report = {'anchor': {'kind': query.kind, 'id': query.id, 'revision': 1}, 'anchor_turn': anchor['turn'],
            'anchor_input': bounds['input_id'], 'anchor_sent': bounds['sent_id'], 'total': total,
            'from_turn': lower, 'to_turn': upper, 'page': page if paged else None, 'page_size': size,
            'items': [], 'omitted': [], 'token_budget': query.token_budget, 'estimated_tokens': 0,
            'meaning': '用户输入和确认送达原文；不证明现实属实或用户已读。图片和表情包只保留占位，不代表已查看。'}
        candidates, eligible = {}, []
        for item in metadata:
            # 先看数字再加载正文，单轮异常大也不无界加载；不截断成伪完整轮次。
            reason = 'source_invalid' if item['invalid'] else 'message_limit' if item['messages'] > 256 else 'token_budget' if item['bytes'] > query.token_budget * 2 else None
            if reason:
                report['omitted'].append({'number': item['turn'], 'reason': reason})
                continue
            eligible.append(item['turn'])
        if eligible:
            rows = self.database.all(cte + '''SELECT n.*,m.parts_json AS input_parts,m.event_at,
                s.text,s.parts_json AS sent_parts FROM numbered n
                LEFT JOIN inbound_messages m ON n.kind='input' AND m.id=n.id
                LEFT JOIN sent_messages s ON n.kind='sent' AND s.id=n.id
                WHERE n.turn IN (SELECT value FROM json_each(?)) ORDER BY n.at,n.kind,n.id''', (*args, json.dumps(eligible)))
            for row in rows:
                turn = candidates.setdefault(row['turn'], {'number': row['turn'], 'messages': []})
                turn['messages'].append(self.message(row, query))
        # 优先锚点、再按距离选择完整轮次，最终仍按时间顺序呈现。
        for number in sorted(candidates, key=lambda number: (abs(number - anchor['turn']), number)):
            proposed = sorted([*report['items'], candidates[number]], key=lambda item: item['number'])
            candidate = {**report, 'items': proposed}
            # 为后续略过标记留空间，最终返回仍重新计量。
            if _estimate(candidate) + 512 > query.token_budget:
                report['omitted'].append({'number': number, 'reason': 'token_budget'})
            else:
                report['items'] = proposed
        report['omitted'].sort(key=lambda item: item['number'])
        report['estimated_tokens'] = _estimate(report)
        return report

    @staticmethod
    def message(row, query):
        incoming = row['kind'] == 'input'
        parts = json.loads(row['input_parts'] if incoming else row['sent_parts'] or '[]')
        text = ''.join(part.get('text', '') if part.get('type') == 'text' else '[图片／表情包／非文字消息]' for part in parts)
        if not incoming and row['text']:
            text = row['text'] + ''.join('[图片／表情包]' for part in parts if part.get('type') != 'text')
        return {'kind': row['kind'], 'id': row['id'], 'revision': 1,
            'at': row['event_at'] if incoming else row['at'], 'text': text,
            'is_anchor': row['kind'] == query.kind and row['id'] == query.id}

    def attach_anchors(self, instance_id, results, *, through=None, run_id=None):
        """批量补最多8个对话来源编号，不读取原文、不建立额外检索快照。"""
        identifiers = list({item['memory_id'] for item in results if item.get('memory_id')})
        if not identifiers:
            return results
        bounds, cutoff = self.bounds(instance_id, through, run_id)
        rows = self.database.all('''WITH refs AS (SELECT me.memory_id,me.kind,me.source_id AS id,me.revision,
            row_number() OVER (PARTITION BY me.memory_id ORDER BY coalesce(i.received_at,s.attempted_at) DESC,me.kind,me.source_id DESC) AS n
            FROM memory_evidence me JOIN memories m ON m.id=me.memory_id
            LEFT JOIN inbound_messages i ON me.kind='input' AND i.instance_id=m.instance_id AND i.id=me.source_id
            LEFT JOIN sent_messages s ON me.kind='sent' AND s.instance_id=m.instance_id AND s.id=me.source_id
            LEFT JOIN rp_outbox o ON o.id=s.outbox_id
            LEFT JOIN rp_runs r ON r.id=o.run_id AND r.instance_id=m.instance_id
            LEFT JOIN evidence_status es ON es.instance_id=m.instance_id AND es.kind=me.kind AND es.source_id=me.source_id
            WHERE m.instance_id=? AND m.id IN (SELECT value FROM json_each(?))
            AND me.kind IN ('input','sent') AND coalesce(es.invalid,0)=0
            AND ((me.kind='input' AND i.id IS NOT NULL AND i.deleted_at IS NULL AND i.archived_at IS NULL AND i.id<=?)
              OR (me.kind='sent' AND s.id IS NOT NULL AND s.deleted_at IS NULL AND s.archived_at IS NULL AND s.id<=?
                AND r.id IS NOT NULL AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL
                AND (? IS NULL OR r.input_through<=?) AND (? IS NULL OR s.confirmed_at<=?))))
            SELECT * FROM refs WHERE n<=9 ORDER BY memory_id,n''',
            (instance_id, json.dumps(identifiers), bounds['input_id'], bounds['sent_id'], through, through, cutoff, cutoff))
        grouped = {}
        for row in rows:
            grouped.setdefault(row['memory_id'], []).append({key: row[key] for key in ('kind', 'id', 'revision')})
        return [{**item, 'conversation_anchors': grouped.get(item.get('memory_id'), [])[:8],
                 'anchors_limited': len(grouped.get(item.get('memory_id'), [])) > 8} for item in results]
