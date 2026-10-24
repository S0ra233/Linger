"""酒馆资料导入：保留原始文件与未知字段，不执行脚本或资源 URI。"""
import base64
import asyncio
import binascii
import hashlib
import json
from pathlib import Path
import struct
from time import time
from uuid import uuid4
import zlib

from .characters import CharacterInput, Characters
from .worldbooks import WorldbookInput, Worldbooks


MAX_IMPORT_BYTES = 12 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024


def decode_json(source: bytes):
    if len(source) > MAX_JSON_BYTES:
        raise ValueError('资料 JSON 超过 2 MiB。')
    try:
        value = json.loads(source.decode('utf-8-sig'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        # JSON 的指数记法也可能被解析为无穷大，不能留到响应序列化才报错。
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('资料必须是有效的 UTF-8 JSON 对象。') from exc


def card_from_png(source: bytes):
    if not source.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('不是 PNG 角色卡。')
    cursor, chunks, found, ended, has_image = 8, 0, {}, False, False
    while cursor + 12 <= len(source):
        size = struct.unpack('>I', source[cursor:cursor + 4])[0]
        kind = source[cursor + 4:cursor + 8]
        end = cursor + 12 + size
        if end > len(source) or chunks > 10000:
            raise ValueError('PNG 数据块损坏或数量过多。')
        payload = source[cursor + 8:end - 4]
        crc = struct.unpack('>I', source[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ValueError('PNG 数据块校验失败。')
        if chunks == 0 and (kind != b'IHDR' or size != 13):
            raise ValueError('PNG 缺少图像头。')
        if kind == b'IDAT':
            has_image = True
        if kind == b'tEXt':
            key, separator, value = payload.partition(b'\x00')
            if separator and key in {b'chara', b'ccv3'}:
                if key in found or len(value) > MAX_JSON_BYTES * 2:
                    raise ValueError('角色卡元数据重复或过大。')
                found[key] = value
        if kind == b'IEND':
            if size != 0:
                raise ValueError('PNG 结束块无效。')
            ended = True
            break
        cursor, chunks = end, chunks + 1
    selected = found.get(b'ccv3', found.get(b'chara'))
    if not ended or not has_image or selected is None:
        raise ValueError('PNG 没有完整的 chara／ccv3 角色卡数据。')
    try:
        return decode_json(base64.b64decode(selected, validate=True))
    except (ValueError, binascii.Error) as exc:
        raise ValueError('角色卡 Base64 或 JSON 数据无效。') from exc


def normalize_book(raw: dict, warnings: list[str], fallback: str = '导入世界书'):
    data = raw.get('data') if raw.get('spec') == 'lorebook_v3' else raw
    if not isinstance(data, dict) or not isinstance(data.get('entries'), (dict, list)):
        raise ValueError('世界书需要 entries 数组或酒馆 entries 对象。')
    entries = data['entries']
    if isinstance(entries, dict):
        entries = list(entries.values())
    result = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError('世界书条目必须是对象。')
        ext = entry.get('extensions', {})
        if not isinstance(ext, dict):
            raise ValueError('世界书 extensions 必须是对象。')
        combined = {**ext, **entry}
        unsupported = []
        for key in ('use_regex', 'vectorized', 'group', 'triggers',
                    'automationId', 'automation_id', 'characterFilter', 'character_filter', 'matchWholeWords',
                    'match_whole_words', 'scanDepth', 'scan_depth', 'depth', 'outletName', 'outlet_name',
                    'matchCreatorNotes', 'match_creator_notes', 'matchCharacterDescription', 'match_character_description',
                    'delayUntilRecursion', 'delay_until_recursion', 'decorators'):
            if combined.get(key) not in (None, False, '', [], {}):
                unsupported.append(key)
        if combined.get('useProbability', True) and combined.get('probability', 100) != 100:
            unsupported.append('probability')
        keys = entry.get('keys', entry.get('key', []))
        secondary = entry.get('secondary_keys', entry.get('keysecondary', []))
        if isinstance(secondary, str):
            secondary = [key.strip() for key in secondary.split(',') if key.strip()]
        if not isinstance(keys, list) or not isinstance(secondary, list) or any(not isinstance(key, str) for key in keys + secondary):
            raise ValueError('世界书关键词必须是字符串数组。')
        if any(key.startswith('/') and key.rsplit('/', 1)[-1] in ('', 'i', 'g', 'gi') for key in keys + secondary):
            unsupported.append('regex_key')
        content = entry.get('content', '')
        if isinstance(content, str) and any(line.lstrip().startswith('@@') for line in content.splitlines()):
            unsupported.append('decorators')
        position = combined.get('position', 'after_char')
        if not isinstance(position, (str, int)) or isinstance(position, bool):
            raise ValueError('世界书插入位置无效。')
        position = {0: 'before_char', 1: 'after_char'}.get(position, position)
        if position not in {'before_char', 'after_char'}:
            unsupported.append('position')
            position = 'after_char'
        logic = combined.get('selectiveLogic', 0)
        if not isinstance(logic, int) or isinstance(logic, bool) or logic not in {0, 1, 2, 3}:
            unsupported.append('selectiveLogic')
            logic = 0
        if unsupported:
            warnings.append(f'条目 {index + 1} 含暂不支持的条件，默认关闭：' + '、'.join(unsupported))
        mapped_fields = {'id', 'uid', 'name', 'comment', 'content', 'keys', 'key', 'secondary_keys', 'keysecondary',
                         'constant', 'enabled', 'disable', 'case_sensitive', 'caseSensitive', 'insertion_order', 'order',
                         'priority', 'selective', 'selectiveLogic', 'position', 'extensions', 'useProbability', 'probability', 'run_types', 'sticky', 'cooldown', 'delay'}
        unknown = sorted(set(combined) - mapped_fields - set(unsupported))
        if unknown:
            warnings.append(f'条目 {index + 1} 的其他字段仅保留：' + '、'.join(unknown))
        enabled = entry.get('enabled', not entry.get('disable', False))
        if not isinstance(enabled, bool) or not isinstance(entry.get('disable', False), bool) or not isinstance(entry.get('selective', False), bool):
            raise ValueError('世界书启用状态与条件开关必须是布尔值。')
        result.append({'id': str(index), 'name': entry.get('name') or entry.get('comment') or '', 'content': content,
                       'keys': keys, 'secondary_keys': secondary, 'constant': entry.get('constant', False),
                       'enabled': enabled and not unsupported,
                       'case_sensitive': entry.get('case_sensitive', combined.get('caseSensitive') or False),
                       'insertion_order': entry.get('insertion_order', entry.get('order', 100)),
                       'priority': entry.get('priority', entry.get('order', entry.get('insertion_order', 100))),
                       'selective_logic': {0: 'and_any', 1: 'not_all', 2: 'not_any', 3: 'and_all'}[logic] if entry.get('selective', False) else 'none',
                       'position': position, 'run_types': entry.get('run_types', []),
                       **{key: combined.get(key, 0) or 0 for key in ('sticky', 'cooldown', 'delay')}})
        if any(result[-1][key] for key in ('sticky', 'cooldown', 'delay')):
            warnings.append(f'条目 {index + 1} 的时效按本系统有效 RP 提交轮数计算；不按酒馆消息条数计算。')
    if data.get('recursive_scanning'):
        warnings.append('递归扫描暂不支持，仅对真实近期对话扫描一次。')
    if data.get('extensions'):
        warnings.append('世界书扩展原样保留；不执行扩展脚本。')
    return WorldbookInput.model_validate({'name': data.get('name') or fallback, 'description': data.get('description') or '',
        'scan_depth': data.get('scan_depth', 20), 'token_budget': data.get('token_budget', 2048), 'entries': result}).model_dump()


def normalize_card(raw: dict, warnings: list[str]):
    spec = raw.get('spec')
    if spec not in {'chara_card_v2', 'chara_card_v3'} or not isinstance(raw.get('data'), dict):
        raise ValueError('仅支持明确声明 chara_card_v2 或 chara_card_v3 的角色卡。')
    data = raw['data']
    expected = '3.0' if spec == 'chara_card_v3' else '2.0'
    if raw.get('spec_version') != expected:
        warnings.append('角色卡版本与当前规范版本不同；按已知字段读取，其他内容原样保留。')
    mapping = {'name': 'name', 'description': 'description', 'personality': 'personality', 'scenario': 'scenario',
               'dialogue_examples': 'mes_example', 'first_message': 'first_mes', 'nickname': 'nickname',
               'system_prompt': 'system_prompt', 'post_history_instructions': 'post_history_instructions',
               'alternate_greetings': 'alternate_greetings'}
    fields = {target: data[source] for target, source in mapping.items() if source in data}
    character = CharacterInput.model_validate(fields).model_dump()
    unknown = sorted(set(data) - set(mapping.values()) - {'character_book', 'creator_notes', 'creator_notes_multilingual',
        'creator', 'character_version', 'tags', 'extensions', 'assets', 'source', 'group_only_greetings', 'creation_date', 'modification_date'})
    if unknown:
        warnings.append('未映射字段原样保留：' + '、'.join(unknown))
    if data.get('extensions') or data.get('assets') or data.get('group_only_greetings'):
        warnings.append('扩展、外部资源与群聊开场白仅保留，不执行或自动下载。')
    metadata = {key: data[key] for key in ('creator_notes', 'creator_notes_multilingual', 'creator', 'character_version', 'tags') if key in data}
    book = normalize_book(data['character_book'], warnings, data.get('name', '角色') + '的世界书') if data.get('character_book') is not None else None
    return {'character': character, 'worldbook': book, 'metadata': metadata}


class MaterialImports:
    def __init__(self, database, events, directory: Path, characters: Characters, worldbooks: Worldbooks):
        self.database, self.events, self.directory = database, events, directory
        self.characters, self.worldbooks = characters, worldbooks

    @staticmethod
    def parse(source: bytes, kind: str):
        if not source or len(source) > MAX_IMPORT_BYTES:
            raise ValueError('导入文件不能为空或超过 12 MiB。')
        is_png = source.startswith(b'\x89PNG\r\n\x1a\n')
        if kind == 'worldbook' and is_png:
            raise ValueError('独立世界书使用 JSON 文件。')
        raw = card_from_png(source) if is_png else decode_json(source)
        warnings = []
        normalized = normalize_card(raw, warnings) if kind == 'character' else normalize_book(raw, warnings)
        return raw, normalized, warnings, is_png

    async def preview(self, source: bytes, kind: str, filename: str):
        # 可取消的工作线程只解析字节，不持有数据库或业务写入权限。
        raw, normalized, warnings, is_png = await asyncio.to_thread(self.parse, source, kind)
        target = uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / (target + '.source')).open('xb') as output:
            output.write(source)
        with self.database.transaction() as tx:
            tx.execute('INSERT INTO material_imports VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                       (target, kind, filename[:200], 'png' if is_png else 'json', hashlib.sha256(source).hexdigest(),
                        json.dumps(raw, ensure_ascii=False), json.dumps(normalized, ensure_ascii=False),
                        json.dumps(warnings, ensure_ascii=False), time()))
        return self.get(target)

    def get(self, import_id: str):
        row = self.database.one('SELECT * FROM material_imports WHERE id=?', (import_id,))
        if not row:
            raise LookupError('导入资料不存在。')
        return {key: value for key, value in row.items() if key not in {'raw_json', 'normalized_json', 'warnings_json'}} | {
            'raw': json.loads(row['raw_json']), 'normalized': json.loads(row['normalized_json']), 'warnings': json.loads(row['warnings_json'])}

    def source_path(self, import_id: str):
        self.get(import_id)
        if len(import_id) != 32 or any(char not in '0123456789abcdef' for char in import_id):
            raise ValueError('导入编号无效。')
        path = self.directory / (import_id + '.source')
        if not path.is_file():
            raise LookupError('原始文件缺失，请检查导入资料备份。')
        return path

    def commit(self, import_id: str):
        with self.database.transaction() as tx:
            row = tx.execute('SELECT * FROM material_imports WHERE id=?', (import_id,)).fetchone()
            if not row:
                raise LookupError('导入资料不存在。')
            if row['committed_id']:
                return {'kind': row['kind'], 'id': row['committed_id']}
            data = json.loads(row['normalized_json'])
            if row['kind'] == 'character':
                body = data['character']
                if data['worldbook']:
                    book = self.worldbooks.write(tx, WorldbookInput.model_validate(data['worldbook']), import_id=import_id)
                    body['worldbooks'] = [{'id': book['id'], 'revision': book['revision']}]
                result = self.characters.write(tx, CharacterInput.model_validate(body), import_id=import_id)
            else:
                result = self.worldbooks.write(tx, WorldbookInput.model_validate(data), import_id=import_id)
            tx.execute('UPDATE material_imports SET committed_id=? WHERE id=?', (result['id'], import_id))
        self.events.publish('materials')
        return {'kind': row['kind'], 'id': result['id']}
