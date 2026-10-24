"""平台适配器只提交统一输入；身份绑定、去重和持久化由此负责。"""
from dataclasses import dataclass
from datetime import datetime
import json
from time import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from ..events import EventBus
from ..storage.configuration import RevisionConflict
from ..storage.database import Database
from .store import invalidate
from .conversations import CONVERSATION_CTE


def qq_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('QQ 号码格式无效。')
    text = str(value)
    if not text.isascii() or not text.isdigit() or not 1 <= len(text) <= 20 or int(text) < 1:
        raise ValueError('QQ 号码格式无效。')
    return str(int(text))


@dataclass(frozen=True)
class InputEvent:
    account_id: str
    bot_id: str
    peer_id: str
    message_id: str
    event_at: float
    nickname: str
    parts: tuple[dict, ...]


class BindingInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    character_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    display_name: str = Field(default='', max_length=80)
    enabled: bool = True
    revision: int = Field(default=0, ge=0)


class InputService:
    def __init__(self, database: Database, events: EventBus):
        self.database, self.events = database, events

    def contacts(self, account_id: str):
        return self.database.all('''SELECT c.*,v.config_json AS character_config,
            r.character_id,r.character_revision,r.input_version,
            (SELECT count(*) FROM inbound_messages m WHERE m.instance_id=c.instance_id AND m.deleted_at IS NULL AND m.archived_at IS NULL) AS message_count
            FROM contacts c LEFT JOIN role_instances r ON r.id=c.instance_id
            LEFT JOIN character_versions v ON v.character_id=r.character_id AND v.revision=r.character_revision
            WHERE c.account_id=? ORDER BY c.last_seen_at DESC LIMIT 1000''', (account_id,))

    def bind(self, account_id: str, peer_id: str, body: BindingInput):
        peer_id, now = qq_id(peer_id), time()
        with self.database.transaction() as tx:
            account = tx.execute('SELECT bot_id FROM qq_accounts WHERE id=?', (account_id,)).fetchone()
            character = tx.execute('SELECT revision FROM characters WHERE id=?', (body.character_id,)).fetchone()
            if not account or not character:
                raise LookupError('账号或角色不存在。')
            if peer_id == account['bot_id']:
                raise ValueError('不能将机器人自身绑定为联系人。')
            contact = tx.execute('SELECT * FROM contacts WHERE account_id=? AND peer_id=?',
                                 (account_id, peer_id)).fetchone()
            if (contact['revision'] if contact else 0) != body.revision:
                raise RevisionConflict('联系人绑定已变化，请刷新后重试。')
            if not contact and tx.execute('SELECT count(*) FROM contacts WHERE account_id=?',
                                         (account_id,)).fetchone()[0] >= 1000:
                raise ValueError('该账号联系人数量已达 1000 个。')
            instance = tx.execute('SELECT id FROM role_instances WHERE account_id=? AND peer_id=? AND character_id=?',
                                  (account_id, peer_id, body.character_id)).fetchone()
            instance_id = instance['id'] if instance else uuid4().hex
            if not instance:
                tx.execute('''INSERT INTO role_instances
                    (id,account_id,peer_id,character_id,character_revision,created_at) VALUES (?,?,?,?,?,?)''',
                           (instance_id, account_id, peer_id, body.character_id, character['revision'], now))
            if contact:
                # 换角色或停用时也使旧运行版本失效；未来的生成不能跨绑定提交。
                if contact['instance_id']:
                    invalidate(tx, contact['instance_id'], '联系人绑定已变化。')
                if instance_id != contact['instance_id']:
                    invalidate(tx, instance_id, '生活线重新绑定。')
                tx.execute('''UPDATE contacts SET instance_id=?,display_name=?,enabled=?,revision=revision+1
                    WHERE account_id=? AND peer_id=?''',
                           (instance_id, body.display_name.strip(), int(body.enabled), account_id, peer_id))
            else:
                tx.execute('''INSERT INTO contacts
                    (account_id,peer_id,nickname,display_name,instance_id,enabled,revision,last_seen_at)
                    VALUES (?,?,?,?,?,?,1,?)''',
                           (account_id, peer_id, '', body.display_name.strip(), instance_id, int(body.enabled), now))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (now, 'contact.bound', json.dumps({'account_id': account_id, 'peer_id': peer_id,
                                                        'instance_id': instance_id, 'enabled': body.enabled})))
        self.events.publish('contacts')
        return {'instance_id': instance_id, 'revision': body.revision + 1}

    def sync_character(self, instance_id: str, revision: int):
        with self.database.transaction() as tx:
            row = tx.execute('''SELECT r.*,c.revision AS latest FROM role_instances r
                JOIN characters c ON c.id=r.character_id WHERE r.id=?''', (instance_id,)).fetchone()
            if not row:
                raise LookupError('角色生活线不存在。')
            if row['character_revision'] != revision:
                raise RevisionConflict('该生活线的设定已变化，请刷新。')
            # 旧客户端仍可请求同步；已自动同步时不再取消运行或推进输入版本。
            if row['character_revision'] == row['latest']:
                return {'character_revision': row['latest']}
            tx.execute('UPDATE role_instances SET character_revision=? WHERE id=?',
                       (row['latest'], instance_id))
            invalidate(tx, instance_id, '生活线采用了新的设定。')
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (time(), 'instance.character_synced', json.dumps({'instance_id': instance_id, 'revision': row['latest']})))
        self.events.publish('contacts')
        return {'character_revision': row['latest']}

    def ingest(self, event: InputEvent) -> str:
        """先提交输入事实，再由附件工作器处理网络；取消不撤回已收到的输入。"""
        now = time()
        with self.database.transaction() as tx:
            account = tx.execute('SELECT bot_id,enabled FROM qq_accounts WHERE id=?', (event.account_id,)).fetchone()
            if not account or account['bot_id'] != event.bot_id or not account['enabled']:
                return 'disabled'
            if tx.execute('SELECT 1 FROM onebot_receipts WHERE account_id=? AND message_id=?',
                          (event.account_id, event.message_id)).fetchone():
                return 'duplicate'
            contact = tx.execute('SELECT * FROM contacts WHERE account_id=? AND peer_id=?',
                                 (event.account_id, event.peer_id)).fetchone()
            if not contact:
                if tx.execute('SELECT count(*) FROM contacts WHERE account_id=?',
                              (event.account_id,)).fetchone()[0] >= 1000:
                    return 'contact_limit'
                tx.execute('INSERT INTO contacts(account_id,peer_id,nickname,last_seen_at) VALUES (?,?,?,?)',
                           (event.account_id, event.peer_id, event.nickname, now))
            else:
                tx.execute('UPDATE contacts SET nickname=?,last_seen_at=? WHERE account_id=? AND peer_id=?',
                           (event.nickname, now, event.account_id, event.peer_id))
            accepted = bool(contact and contact['enabled'] and contact['instance_id'])
            outcome = 'received' if accepted else 'unbound'
            tx.execute('INSERT INTO onebot_receipts VALUES (?,?,?,?,?)',
                       (event.account_id, event.message_id, event.peer_id, outcome, now))
            if accepted:
                preferences = tx.execute("SELECT value_json FROM configurations WHERE key='preferences'").fetchone()
                timezone = json.loads(preferences['value_json'])['timezone'] if preferences else 'Asia/Shanghai'
                # 平台消息时间与请求当前时间分开；显示时区之后变化也不重算历史文字。
                context_time = datetime.fromtimestamp(event.event_at, ZoneInfo(timezone)).isoformat(sep=' ', timespec='minutes')
                parts = [dict(part) for part in event.parts]
                for index, part in enumerate(parts):
                    if part['type'] == 'image':
                        part['image_id'] = uuid4().hex
                cursor = tx.execute('''INSERT INTO inbound_messages
                    (instance_id,account_id,message_id,event_at,received_at,parts_json,context_time) VALUES (?,?,?,?,?,?,?)''',
                    (contact['instance_id'], event.account_id, event.message_id, event.event_at, now,
                     json.dumps([{k: v for k, v in part.items() if k != 'url'} for part in parts], ensure_ascii=False), context_time))
                for index, part in enumerate(parts):
                    if part['type'] == 'image':
                        tx.execute('''INSERT INTO inbound_images
                            (id,message_id,part_index,source_url,status,error,updated_at) VALUES (?,?,?,?,?,?,?)''',
                            (part['image_id'], cursor.lastrowid, index, part['url'],
                             'pending' if part['url'] else 'failed',
                             None if part['url'] else '网关未提供可下载的图片 URL。', now))
                invalidate(tx, contact['instance_id'], '收到新消息，取消尚未发送的旧计划。', input_received=True)
        self.events.publish('messages' if accepted else 'contacts')
        return outcome

    def instances(self):
        return self.database.all('''SELECT r.*,a.name AS account_name,c.nickname,c.display_name,
            CASE WHEN c.instance_id=r.id AND a.enabled=1 THEN c.enabled ELSE 0 END AS receiving,
            v.config_json AS character_config,t.revision AS latest_character_revision,
            (SELECT count(*) FROM inbound_messages m WHERE m.instance_id=r.id AND m.id>r.handled_input_id AND m.deleted_at IS NULL AND m.archived_at IS NULL) AS pending_inputs,
            (SELECT min(o.due_at) FROM rp_outbox o WHERE o.instance_id=r.id AND o.status='pending') AS next_delivery,
            (SELECT count(*) FROM inbound_messages m WHERE m.instance_id=r.id AND m.deleted_at IS NULL AND m.archived_at IS NULL) AS message_count
            FROM role_instances r JOIN qq_accounts a ON a.id=r.account_id
            JOIN contacts c ON c.account_id=r.account_id AND c.peer_id=r.peer_id
            JOIN character_versions v ON v.character_id=r.character_id AND v.revision=r.character_revision
            JOIN characters t ON t.id=r.character_id ORDER BY r.created_at DESC LIMIT 2000''')

    def messages(self, instance_id: str, before: int | None = None):
        if not self.database.one('SELECT id FROM role_instances WHERE id=?', (instance_id,)):
            raise LookupError('角色生活线不存在。')
        rows = self.database.all('''SELECT * FROM inbound_messages WHERE instance_id=? AND id<? AND deleted_at IS NULL AND archived_at IS NULL
            ORDER BY id DESC LIMIT 31''', (instance_id, before or 9223372036854775807))
        result = []
        for row in rows[:30]:
            parts = self.input_parts(instance_id, row['id'], row.pop('parts_json'))
            result.append({**row, 'parts': parts})
        return {'items': result, 'next_before': rows[29]['id'] if len(rows) > 30 else None}

    def input_parts(self, instance_id, message_id, raw):
        parts = json.loads(raw)
        images = {image['id']: image for image in self.database.all('''SELECT id,status,error
            FROM inbound_images WHERE message_id=?''', (message_id,))}
        for part in parts:
            if part['type'] == 'image':
                part['market_available'] = bool(part.pop('market', None))
                part.update(images.get(part['image_id'], {'status': 'failed', 'error': '图片记录不可用。'}))
                if part['status'] == 'ready':
                    part['url'] = f"/api/v1/roleplay/instances/{instance_id}/images/{part['image_id']}"
        return parts

    def conversation(self, instance_id, page, page_size, anchor_input=None, anchor_sent=None):
        if not self.database.one('SELECT id FROM role_instances WHERE id=?', (instance_id,)):
            raise LookupError('角色生活线不存在。')
        if (anchor_input is None) != (anchor_sent is None):
            raise ValueError('查看锚点须同时提供。')
        if anchor_input is None:
            anchor = self.database.one('''SELECT
                (SELECT coalesce(max(id),0) FROM inbound_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL) AS input_id,
                (SELECT coalesce(max(id),0) FROM sent_messages WHERE instance_id=? AND deleted_at IS NULL AND archived_at IS NULL) AS sent_id''', (instance_id, instance_id))
            anchor_input, anchor_sent = anchor['input_id'], anchor['sent_id']
        # 仅对元数据分组，再取这一页正文；待发和失败草稿不加入聊天事实。
        cte = CONVERSATION_CTE
        args = (instance_id, anchor_input, instance_id, anchor_sent)
        return self.conversation_page(instance_id, cte, args, page, page_size, anchor_input, anchor_sent)

    def conversation_page(self, instance_id, cte, args, page, page_size, anchor_input, anchor_sent):
        """正常记录和只读归档共用完整轮次分页；查询范围由调用方在服务端固定。"""
        total = self.database.one(cte + 'SELECT coalesce(max(turn),0) AS n FROM numbered', args)['n']
        page = min(page, max(1, (total + page_size - 1) // page_size))
        upper = total - (page - 1) * page_size
        rows = self.database.all(cte + '''SELECT n.*,m.parts_json AS input_parts,m.event_at,
            s.text,s.parts_json AS sent_parts,coalesce(m.message_id,s.message_id) AS message_id
            FROM numbered n LEFT JOIN inbound_messages m ON n.kind='input' AND m.id=n.id
            LEFT JOIN sent_messages s ON n.kind='sent' AND s.id=n.id
            WHERE n.turn>? AND n.turn<=? ORDER BY n.turn DESC,n.at,n.kind,n.id''', (*args, max(0, upper - page_size), upper))
        turns = []
        for row in rows:
            if not turns or turns[-1]['number'] != row['turn']:
                turns.append({'number': row['turn'], 'messages': []})
            incoming = row['kind'] == 'input'
            turns[-1]['messages'].append({'id': row['id'], 'role': 'user' if incoming else 'assistant',
                'message_id': row['message_id'], 'at': row['event_at'] if incoming else row['at'],
                'text': '' if incoming else row['text'],
                'parts': self.input_parts(instance_id, row['id'], row['input_parts']) if incoming else json.loads(row['sent_parts'] or '[]')})
        return {'items': turns, 'total': total, 'page': page, 'page_size': page_size,
                'anchor_input': anchor_input, 'anchor_sent': anchor_sent}
