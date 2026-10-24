"""SQLite 的统一连接和短事务入口；事务内不得等待外部网络。"""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import json
from threading import RLock
from time import time
from uuid import uuid4


SCHEMA_VERSION = 23
MIGRATIONS = {
    1: (
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE administrator (
            id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL,
            password_hash TEXT NOT NULL, created_at REAL NOT NULL, password_changed_at REAL NOT NULL)""",
        """CREATE TABLE login_sessions (
            id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, csrf_hash TEXT NOT NULL,
            created_at REAL NOT NULL, last_seen_at REAL NOT NULL, expires_at REAL NOT NULL,
            peer TEXT NOT NULL, user_agent TEXT NOT NULL)""",
        "CREATE INDEX login_sessions_expiry ON login_sessions(expires_at)",
        """CREATE TABLE configurations (
            key TEXT PRIMARY KEY, value_json TEXT NOT NULL, revision INTEGER NOT NULL,
            updated_at REAL NOT NULL)""",
        """CREATE TABLE encrypted_secrets (
            id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
        """CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
            action TEXT NOT NULL, details_json TEXT NOT NULL)""",
        "CREATE INDEX audit_events_time ON audit_events(created_at)",
    ),
    2: (
        """CREATE TABLE model_connections (
            id TEXT PRIMARY KEY, config_json TEXT NOT NULL, credential_ref TEXT,
            revision INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            FOREIGN KEY(credential_ref) REFERENCES encrypted_secrets(id))""",
        """CREATE TABLE model_checks (
            id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, model_id TEXT NOT NULL,
            connection_revision INTEGER NOT NULL, status TEXT NOT NULL,
            created_at REAL NOT NULL, finished_at REAL,
            result_json TEXT, error TEXT,
            FOREIGN KEY(connection_id) REFERENCES model_connections(id))""",
        "CREATE INDEX model_checks_connection ON model_checks(connection_id,created_at)",
    ),
    3: (
        """CREATE TABLE characters (
            id TEXT PRIMARY KEY, config_json TEXT NOT NULL, revision INTEGER NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
        """CREATE TABLE character_versions (
            character_id TEXT NOT NULL REFERENCES characters(id), revision INTEGER NOT NULL,
            config_json TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(character_id,revision))""",
        """CREATE TABLE qq_accounts (
            id TEXT PRIMARY KEY, bot_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            enabled INTEGER NOT NULL, credential_ref TEXT NOT NULL REFERENCES encrypted_secrets(id),
            revision INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
        """CREATE TABLE role_instances (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES qq_accounts(id),
            peer_id TEXT NOT NULL, character_id TEXT NOT NULL REFERENCES characters(id),
            character_revision INTEGER NOT NULL, input_version INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            UNIQUE(account_id,peer_id,character_id),
            FOREIGN KEY(character_id,character_revision) REFERENCES character_versions(character_id,revision))""",
        """CREATE TABLE contacts (
            account_id TEXT NOT NULL REFERENCES qq_accounts(id), peer_id TEXT NOT NULL,
            nickname TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '',
            instance_id TEXT REFERENCES role_instances(id), enabled INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 0, last_seen_at REAL NOT NULL,
            PRIMARY KEY(account_id,peer_id))""",
        """CREATE TABLE onebot_receipts (
            account_id TEXT NOT NULL REFERENCES qq_accounts(id), message_id TEXT NOT NULL,
            peer_id TEXT NOT NULL, outcome TEXT NOT NULL, received_at REAL NOT NULL,
            PRIMARY KEY(account_id,message_id))""",
        """CREATE TABLE inbound_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, instance_id TEXT NOT NULL REFERENCES role_instances(id),
            account_id TEXT NOT NULL, message_id TEXT NOT NULL, event_at REAL NOT NULL,
            received_at REAL NOT NULL, parts_json TEXT NOT NULL,
            FOREIGN KEY(account_id,message_id) REFERENCES onebot_receipts(account_id,message_id),
            UNIQUE(account_id,message_id))""",
        "CREATE INDEX inbound_instance ON inbound_messages(instance_id,id)",
        """CREATE TABLE inbound_images (
            id TEXT PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES inbound_messages(id),
            part_index INTEGER NOT NULL, source_url TEXT NOT NULL, status TEXT NOT NULL,
            attachment_id TEXT, byte_size INTEGER, error TEXT, updated_at REAL NOT NULL,
            UNIQUE(message_id,part_index))""",
        "CREATE INDEX inbound_images_pending ON inbound_images(status,updated_at)",
    ),
    4: (
        'ALTER TABLE role_instances ADD COLUMN paused INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE role_instances ADD COLUMN handled_input_id INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE role_instances ADD COLUMN attempted_version INTEGER NOT NULL DEFAULT -1',
        '''CREATE TABLE rp_states (
            instance_id TEXT PRIMARY KEY REFERENCES role_instances(id), state_json TEXT NOT NULL,
            life_fragment TEXT NOT NULL, read_state INTEGER NOT NULL, next_wakeup_at REAL, updated_at REAL NOT NULL)''',
        '''CREATE TABLE rp_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, instance_id TEXT NOT NULL REFERENCES role_instances(id),
            input_version INTEGER NOT NULL, input_through INTEGER NOT NULL, status TEXT NOT NULL,
            settings_json TEXT NOT NULL, model_json TEXT, decision_json TEXT, error TEXT,
            created_at REAL NOT NULL, finished_at REAL)''',
        'CREATE INDEX rp_runs_instance ON rp_runs(instance_id,id)',
        '''CREATE TABLE rp_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES rp_runs(id),
            step INTEGER NOT NULL, request_json TEXT NOT NULL, response_json TEXT,
            tools_json TEXT, status TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL,
            UNIQUE(run_id,step))''',
        '''CREATE TABLE rp_outbox (
            id TEXT PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES rp_runs(id),
            instance_id TEXT NOT NULL REFERENCES role_instances(id), input_version INTEGER NOT NULL,
            position INTEGER NOT NULL, text TEXT NOT NULL, due_at REAL NOT NULL, expires_at REAL NOT NULL,
            status TEXT NOT NULL, started_at REAL, finished_at REAL, message_id TEXT, error TEXT, confirmation_source TEXT,
            UNIQUE(run_id,position))''',
        'CREATE INDEX rp_outbox_due ON rp_outbox(status,due_at)',
        '''CREATE TABLE sent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, instance_id TEXT NOT NULL REFERENCES role_instances(id),
            outbox_id TEXT NOT NULL UNIQUE REFERENCES rp_outbox(id), message_id TEXT NOT NULL,
            text TEXT NOT NULL, attempted_at REAL NOT NULL, confirmed_at REAL NOT NULL,
            confirmation_source TEXT NOT NULL)''',
        'CREATE INDEX sent_messages_instance ON sent_messages(instance_id,id)',
    ),
    5: (
        '''CREATE TABLE worldbooks (id TEXT PRIMARY KEY,config_json TEXT NOT NULL,revision INTEGER NOT NULL,
            created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
        '''CREATE TABLE worldbook_versions (worldbook_id TEXT NOT NULL REFERENCES worldbooks(id),
            revision INTEGER NOT NULL,config_json TEXT NOT NULL,created_at REAL NOT NULL,
            PRIMARY KEY(worldbook_id,revision))''',
        '''CREATE TABLE material_imports (id TEXT PRIMARY KEY,kind TEXT NOT NULL,filename TEXT NOT NULL,
            format TEXT NOT NULL,source_hash TEXT NOT NULL,raw_json TEXT NOT NULL,normalized_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL,created_at REAL NOT NULL,committed_id TEXT)''',
        '''CREATE TABLE character_sources (character_id TEXT NOT NULL,revision INTEGER NOT NULL,
            import_id TEXT NOT NULL REFERENCES material_imports(id),PRIMARY KEY(character_id,revision),
            FOREIGN KEY(character_id,revision) REFERENCES character_versions(character_id,revision))''',
        '''CREATE TABLE worldbook_sources (worldbook_id TEXT NOT NULL,revision INTEGER NOT NULL,
            import_id TEXT NOT NULL REFERENCES material_imports(id),PRIMARY KEY(worldbook_id,revision),
            FOREIGN KEY(worldbook_id,revision) REFERENCES worldbook_versions(worldbook_id,revision))''',
        'ALTER TABLE rp_runs ADD COLUMN context_json TEXT',
        '''CREATE TABLE rp_summaries (id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id TEXT NOT NULL REFERENCES role_instances(id),run_id INTEGER NOT NULL REFERENCES rp_runs(id),
            input_version INTEGER NOT NULL,input_cursor INTEGER NOT NULL,sent_cursor INTEGER NOT NULL,
            status TEXT NOT NULL,text TEXT,source_json TEXT NOT NULL,source_hash TEXT NOT NULL,
            model_json TEXT NOT NULL,usage_json TEXT,error TEXT,created_at REAL NOT NULL,finished_at REAL)''',
        'CREATE INDEX rp_summaries_instance ON rp_summaries(instance_id,status,id)',
    ),
    6: (
        '''CREATE TABLE model_presets(id TEXT PRIMARY KEY,name TEXT NOT NULL,revision INTEGER NOT NULL,
            config_json TEXT NOT NULL,source_json TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
        '''CREATE TABLE model_preset_versions(preset_id TEXT NOT NULL REFERENCES model_presets(id),
            revision INTEGER NOT NULL,name TEXT NOT NULL,config_json TEXT NOT NULL,source_json TEXT,created_at REAL NOT NULL,
            PRIMARY KEY(preset_id,revision))''',
    ),
    7: (
        '''CREATE TABLE memories(id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            kind TEXT NOT NULL,text TEXT NOT NULL,digest TEXT NOT NULL,expires_at REAL,status TEXT NOT NULL,
            revision INTEGER NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
            UNIQUE(instance_id,kind,digest))''',
        '''CREATE TABLE memory_sources(memory_id TEXT NOT NULL REFERENCES memories(id),
            message_id INTEGER NOT NULL REFERENCES inbound_messages(id),PRIMARY KEY(memory_id,message_id))''',
        '''CREATE TABLE memory_versions(memory_id TEXT NOT NULL REFERENCES memories(id),revision INTEGER NOT NULL,
            config_json TEXT NOT NULL,run_id INTEGER REFERENCES rp_runs(id),created_at REAL NOT NULL,
            PRIMARY KEY(memory_id,revision))''',
        'CREATE INDEX memory_scope ON memories(instance_id,status,kind)',
        'CREATE INDEX memory_created ON memories(instance_id,created_at,id)',
        '''CREATE TABLE knowledge_libraries(id TEXT PRIMARY KEY,name TEXT NOT NULL,enabled INTEGER NOT NULL,
            revision INTEGER NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
        '''CREATE TABLE knowledge_documents(id TEXT PRIMARY KEY,library_id TEXT NOT NULL REFERENCES knowledge_libraries(id),
            title TEXT NOT NULL,text TEXT NOT NULL,digest TEXT NOT NULL,enabled INTEGER NOT NULL,
            revision INTEGER NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
        '''CREATE TABLE knowledge_versions(document_id TEXT NOT NULL REFERENCES knowledge_documents(id),revision INTEGER NOT NULL,
            title TEXT NOT NULL,text TEXT NOT NULL,digest TEXT NOT NULL,source_bytes BLOB NOT NULL,encoding TEXT NOT NULL,created_at REAL NOT NULL,
            PRIMARY KEY(document_id,revision))''',
        'CREATE INDEX knowledge_created ON knowledge_documents(library_id,created_at,id)',
        '''CREATE TABLE information_chunks(id TEXT PRIMARY KEY,memory_id TEXT REFERENCES memories(id),
            document_id TEXT REFERENCES knowledge_documents(id),revision INTEGER NOT NULL,ordinal INTEGER NOT NULL,
            start_char INTEGER NOT NULL,end_char INTEGER NOT NULL,start_line INTEGER NOT NULL,end_line INTEGER NOT NULL,
            text TEXT NOT NULL,token_count INTEGER NOT NULL)''',
        'CREATE INDEX information_memory ON information_chunks(memory_id,revision)',
        'CREATE INDEX information_document ON information_chunks(document_id,revision)',
        '''CREATE TABLE information_postings(term TEXT NOT NULL,chunk_id TEXT NOT NULL REFERENCES information_chunks(id),
            frequency INTEGER NOT NULL,PRIMARY KEY(term,chunk_id))''',
    ),

    8: (
        """CREATE TABLE embedding_spaces(id TEXT PRIMARY KEY,config_json TEXT NOT NULL,dimensions INTEGER,created_at REAL NOT NULL)""",
        """CREATE TABLE information_vectors(space_id TEXT NOT NULL REFERENCES embedding_spaces(id),
            chunk_id TEXT NOT NULL REFERENCES information_chunks(id),dimensions INTEGER NOT NULL,vector BLOB NOT NULL,
            PRIMARY KEY(space_id,chunk_id))""",
        """CREATE TABLE embedding_jobs(id TEXT PRIMARY KEY,space_id TEXT NOT NULL REFERENCES embedding_spaces(id),
            connection_revision INTEGER NOT NULL,scope_kind TEXT NOT NULL,scope_id TEXT NOT NULL,status TEXT NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0,saved INTEGER NOT NULL DEFAULT 0,skipped INTEGER NOT NULL DEFAULT 0,
            max_chunks INTEGER NOT NULL,batch_size INTEGER NOT NULL,error TEXT,created_at REAL NOT NULL,finished_at REAL)""",
        """CREATE TABLE embedding_calls(id INTEGER PRIMARY KEY AUTOINCREMENT,space_id TEXT NOT NULL,
            job_id TEXT REFERENCES embedding_jobs(id),instance_id TEXT,run_id INTEGER REFERENCES rp_runs(id),purpose TEXT NOT NULL,status TEXT NOT NULL,
            usage_json TEXT,error TEXT,created_at REAL NOT NULL,finished_at REAL)""",
        'CREATE INDEX embedding_call_job ON embedding_calls(job_id,id)',
    ),


    9: (
        """CREATE TABLE mcp_servers(id TEXT PRIMARY KEY,config_json TEXT NOT NULL,credential_ref TEXT,
            revision INTEGER NOT NULL,catalog_revision INTEGER NOT NULL DEFAULT 0,tools_json TEXT NOT NULL DEFAULT '[]',
            discovered_at REAL,last_error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL)""",
        """CREATE TABLE mcp_permissions(instance_id TEXT PRIMARY KEY REFERENCES role_instances(id),
            tools_json TEXT NOT NULL,revision INTEGER NOT NULL,updated_at REAL NOT NULL)""",
        """CREATE TABLE mcp_syncs(id TEXT PRIMARY KEY,server_id TEXT NOT NULL REFERENCES mcp_servers(id),
            server_revision INTEGER NOT NULL,status TEXT NOT NULL,tool_count INTEGER,error TEXT,created_at REAL NOT NULL,finished_at REAL)""",
        """CREATE TABLE mcp_calls(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL REFERENCES rp_runs(id),
            instance_id TEXT NOT NULL REFERENCES role_instances(id),server_id TEXT NOT NULL REFERENCES mcp_servers(id),
            tool_name TEXT NOT NULL,call_id TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT,error TEXT,
            created_at REAL NOT NULL,finished_at REAL,UNIQUE(run_id,call_id))""",
        'CREATE INDEX mcp_call_instance ON mcp_calls(instance_id,id)',
        'ALTER TABLE rp_runs ADD COLUMN mcp_json TEXT',
    ),

    10: (
        "ALTER TABLE rp_runs ADD COLUMN trigger_kind TEXT NOT NULL DEFAULT 'input'",
        'ALTER TABLE rp_runs ADD COLUMN life_json TEXT',
        '''CREATE TABLE life_policies(instance_id TEXT PRIMARY KEY REFERENCES role_instances(id),
            enabled INTEGER NOT NULL,revision INTEGER NOT NULL,updated_at REAL NOT NULL)''',
        '''CREATE TABLE life_intents(id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            kind TEXT NOT NULL,reason TEXT NOT NULL,due_at REAL NOT NULL,expires_at REAL NOT NULL,
            automatic INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,revision INTEGER NOT NULL,
            source_run_id INTEGER REFERENCES rp_runs(id),run_id INTEGER REFERENCES rp_runs(id),
            error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
        'CREATE INDEX life_intent_due ON life_intents(status,due_at,instance_id)',
        "CREATE UNIQUE INDEX life_auto ON life_intents(instance_id) WHERE automatic=1 AND status IN ('pending','running')",
        '''CREATE TABLE life_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id TEXT NOT NULL REFERENCES role_instances(id),run_id INTEGER REFERENCES rp_runs(id),
            kind TEXT NOT NULL,data_json TEXT NOT NULL,created_at REAL NOT NULL)''',
        'CREATE INDEX life_event_instance ON life_events(instance_id,id)',
        'CREATE INDEX life_run_budget ON rp_runs(trigger_kind,created_at,instance_id)',
        'CREATE INDEX life_run_instance ON rp_runs(instance_id,trigger_kind,created_at)',
        'CREATE INDEX sent_message_time ON sent_messages(instance_id,attempted_at)',
    ),
    11: (
        '''CREATE TABLE cache_calls(id INTEGER PRIMARY KEY AUTOINCREMENT,instance_id TEXT NOT NULL,
            connection_id TEXT NOT NULL,model_id TEXT NOT NULL,model TEXT NOT NULL,protocol TEXT NOT NULL,
            created_at REAL NOT NULL,finished_at REAL,status TEXT NOT NULL,usage_json TEXT,error TEXT)''',
        'CREATE INDEX cache_call_time ON cache_calls(created_at,instance_id)',
        '''CREATE TABLE media_assets(id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            kind TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL,mime TEXT,source BLOB,preview BLOB,
            native_json TEXT,source_message_id INTEGER REFERENCES inbound_messages(id),source_part INTEGER,
            revision INTEGER NOT NULL DEFAULT 1,archived INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL)''',
        'CREATE INDEX media_instance ON media_assets(instance_id,archived)',
        "ALTER TABLE rp_outbox ADD COLUMN parts_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE sent_messages ADD COLUMN parts_json TEXT NOT NULL DEFAULT '[]'",
    ),
    12: (
        '''CREATE TABLE memory_evidence(memory_id TEXT NOT NULL REFERENCES memories(id),
            kind TEXT NOT NULL CHECK(kind IN ('input','sent','life')),source_id INTEGER NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision=1),
            PRIMARY KEY(memory_id,kind,source_id))''',
        "INSERT INTO memory_evidence(memory_id,kind,source_id) SELECT memory_id,'input',message_id FROM memory_sources",
        '''CREATE TABLE rp_scenes(instance_id TEXT PRIMARY KEY REFERENCES role_instances(id),
            config_json TEXT NOT NULL,revision INTEGER NOT NULL,source_run_id INTEGER NOT NULL REFERENCES rp_runs(id),
            updated_at REAL NOT NULL)''',
    ),
    13: (
        "ALTER TABLE life_intents ADD COLUMN topic TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE life_intents ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE life_intents ADD COLUMN commitment_json TEXT",
        "ALTER TABLE life_intents ADD COLUMN abandon_if TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE life_intents ADD COLUMN deadline_at REAL",
        "ALTER TABLE life_intents ADD COLUMN max_evaluations INTEGER NOT NULL DEFAULT 3",
        "ALTER TABLE life_intents ADD COLUMN evaluations INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE life_intents ADD COLUMN fingerprint TEXT",
        "ALTER TABLE life_intents ADD COLUMN resolution TEXT",
        "ALTER TABLE life_intents ADD COLUMN outcome_reason TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE life_intents ADD COLUMN resolution_run_id INTEGER REFERENCES rp_runs(id)",
        "CREATE UNIQUE INDEX life_intent_identity ON life_intents(instance_id,fingerprint) WHERE fingerprint IS NOT NULL",
    ),
    14: (
        '''CREATE TABLE worldbook_clocks(instance_id TEXT NOT NULL REFERENCES role_instances(id),
            worldbook_id TEXT NOT NULL,revision INTEGER NOT NULL,turns INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(instance_id,worldbook_id,revision),
            FOREIGN KEY(worldbook_id,revision) REFERENCES worldbook_versions(worldbook_id,revision))''',
        '''CREATE TABLE worldbook_effects(instance_id TEXT NOT NULL,worldbook_id TEXT NOT NULL,
            revision INTEGER NOT NULL,entry_id TEXT NOT NULL,sticky_until INTEGER NOT NULL,cooldown_until INTEGER NOT NULL,
            PRIMARY KEY(instance_id,worldbook_id,revision,entry_id),
            FOREIGN KEY(instance_id,worldbook_id,revision) REFERENCES worldbook_clocks(instance_id,worldbook_id,revision))''',
        '''CREATE TABLE evidence_status(instance_id TEXT NOT NULL REFERENCES role_instances(id),
            kind TEXT NOT NULL CHECK(kind IN ('input','sent','life')),source_id INTEGER NOT NULL,
            invalid INTEGER NOT NULL,reason TEXT NOT NULL,revision INTEGER NOT NULL,updated_at REAL NOT NULL,
            PRIMARY KEY(instance_id,kind,source_id))''',
        'ALTER TABLE role_instances ADD COLUMN adjustment_revision INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE rp_states ADD COLUMN invalidated INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE rp_scenes ADD COLUMN invalidated INTEGER NOT NULL DEFAULT 0',
        '''CREATE TABLE character_proposals(id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id TEXT NOT NULL REFERENCES role_instances(id),run_id INTEGER UNIQUE REFERENCES rp_runs(id),
            character_revision INTEGER NOT NULL,base_revision INTEGER NOT NULL,reason TEXT NOT NULL,
            changes_json TEXT NOT NULL,before_json TEXT NOT NULL,sources_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
            revision INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,reviewed_at REAL)''',
        'CREATE INDEX character_proposal_scope ON character_proposals(instance_id,status,id)',
        '''CREATE TABLE instance_character_versions(instance_id TEXT NOT NULL REFERENCES role_instances(id),
            revision INTEGER NOT NULL,overrides_json TEXT NOT NULL,proposal_id INTEGER REFERENCES character_proposals(id),
            reason TEXT NOT NULL,sources_json TEXT NOT NULL,field_sources_json TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(instance_id,revision))''',
    ),
    15: (
        '''CREATE TABLE life_extensions(instance_id TEXT PRIMARY KEY REFERENCES role_instances(id),
            config_json TEXT NOT NULL,revision INTEGER NOT NULL,updated_at REAL NOT NULL)''',
        '''CREATE TABLE rp_schedules(instance_id TEXT PRIMARY KEY REFERENCES role_instances(id),
            config_json TEXT NOT NULL,revision INTEGER NOT NULL,intent_id TEXT REFERENCES life_intents(id),updated_at REAL NOT NULL)''',
        '''CREATE TABLE schedule_versions(instance_id TEXT NOT NULL REFERENCES role_instances(id),revision INTEGER NOT NULL,
            config_json TEXT NOT NULL,run_id INTEGER REFERENCES rp_runs(id),created_at REAL NOT NULL,PRIMARY KEY(instance_id,revision))''',
        '''CREATE TABLE world_events(id INTEGER PRIMARY KEY AUTOINCREMENT,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            config_json TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL,source_run_id INTEGER REFERENCES rp_runs(id),
            adopted_run_id INTEGER REFERENCES rp_runs(id),intent_id TEXT REFERENCES life_intents(id),due_at REAL NOT NULL,
            expires_at REAL NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
        'CREATE INDEX world_event_scope ON world_events(instance_id,status,due_at)',
        '''CREATE TABLE observations(id INTEGER PRIMARY KEY AUTOINCREMENT,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            source_id TEXT NOT NULL,source_url TEXT NOT NULL,policy_revision INTEGER NOT NULL,purpose TEXT NOT NULL,
            due_at REAL NOT NULL,expires_at REAL NOT NULL,status TEXT NOT NULL,source_run_id INTEGER REFERENCES rp_runs(id),
            started_at REAL,acquired_at REAL,text TEXT,body BLOB,source_hash TEXT,truncated INTEGER,error TEXT,created_at REAL NOT NULL)''',
        'CREATE INDEX observation_due ON observations(status,due_at)',
        'ALTER TABLE memory_evidence RENAME TO memory_evidence_v14',
        '''CREATE TABLE memory_evidence(memory_id TEXT NOT NULL REFERENCES memories(id),
            kind TEXT NOT NULL CHECK(kind IN ('input','sent','life','world_event','observation')),source_id INTEGER NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision=1),PRIMARY KEY(memory_id,kind,source_id))''',
        'INSERT INTO memory_evidence SELECT * FROM memory_evidence_v14',
        'DROP TABLE memory_evidence_v14',
        'ALTER TABLE evidence_status RENAME TO evidence_status_v14',
        '''CREATE TABLE evidence_status(instance_id TEXT NOT NULL REFERENCES role_instances(id),
            kind TEXT NOT NULL CHECK(kind IN ('input','sent','life','world_event','observation')),source_id INTEGER NOT NULL,
            invalid INTEGER NOT NULL,reason TEXT NOT NULL,revision INTEGER NOT NULL,updated_at REAL NOT NULL,
            PRIMARY KEY(instance_id,kind,source_id))''',
        'INSERT INTO evidence_status SELECT * FROM evidence_status_v14',
        'DROP TABLE evidence_status_v14',
        '''CREATE TABLE explicit_caches(id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            connection_id TEXT NOT NULL,model_id TEXT NOT NULL,model TEXT NOT NULL,fingerprint TEXT NOT NULL,
            prefix_json TEXT NOT NULL,name TEXT,expires_at REAL,status TEXT NOT NULL,revision INTEGER NOT NULL,
            token_count INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL,error TEXT)''',
        'CREATE INDEX explicit_cache_scope ON explicit_caches(instance_id,status)',
        '''CREATE TABLE explicit_cache_operations(id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_id TEXT NOT NULL REFERENCES explicit_caches(id),operation TEXT NOT NULL,status TEXT NOT NULL,
            created_at REAL NOT NULL,finished_at REAL,error TEXT)''',
    ),
    16: (
        '''CREATE TABLE context_worksets(instance_id TEXT PRIMARY KEY REFERENCES role_instances(id),
            state_json TEXT NOT NULL DEFAULT '{}',overrides_json TEXT NOT NULL DEFAULT '{}',
            revision INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL)''',
        '''CREATE TABLE context_candidates(run_id INTEGER PRIMARY KEY REFERENCES rp_runs(id),
            instance_id TEXT NOT NULL REFERENCES role_instances(id),input_version INTEGER NOT NULL,
            state_json TEXT NOT NULL,created_at REAL NOT NULL)''',
    ),
    17: (
        '''CREATE TABLE context_strategies(id TEXT PRIMARY KEY,name TEXT NOT NULL,revision INTEGER NOT NULL,
            config_json TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
        '''CREATE TABLE context_strategy_versions(strategy_id TEXT NOT NULL REFERENCES context_strategies(id),
            revision INTEGER NOT NULL,name TEXT NOT NULL,config_json TEXT NOT NULL,created_at REAL NOT NULL,reason TEXT NOT NULL,
            PRIMARY KEY(strategy_id,revision))''',
        '''CREATE TABLE context_bindings(instance_id TEXT PRIMARY KEY REFERENCES role_instances(id),
            strategy_id TEXT,strategy_revision INTEGER,overrides_json TEXT NOT NULL,revision INTEGER NOT NULL,updated_at REAL NOT NULL,
            FOREIGN KEY(strategy_id,strategy_revision) REFERENCES context_strategy_versions(strategy_id,revision))''',
        'CREATE INDEX context_binding_strategy ON context_bindings(strategy_id,strategy_revision)',
    ),
    18: (
        'ALTER TABLE inbound_messages ADD COLUMN deleted_at REAL',
        'ALTER TABLE sent_messages ADD COLUMN deleted_at REAL',
        'ALTER TABLE rp_runs ADD COLUMN context_deleted_at REAL',
        'CREATE INDEX inbound_visible ON inbound_messages(instance_id,id) WHERE deleted_at IS NULL',
        'CREATE INDEX sent_visible ON sent_messages(instance_id,id) WHERE deleted_at IS NULL',
    ),
    19: (
        # 不回填旧消息，避免仅升级显示格式就改写已用于缓存的历史包装。
        'ALTER TABLE inbound_messages ADD COLUMN context_time TEXT',
    ),
    20: (
        'ALTER TABLE memories ADD COLUMN locked INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE memories ADD COLUMN protection_revision INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE memories ADD COLUMN confirmed_at REAL',
        'ALTER TABLE memories ADD COLUMN valid_from REAL',
        'ALTER TABLE memories ADD COLUMN superseded_by TEXT REFERENCES memories(id)',
        'ALTER TABLE memories ADD COLUMN superseded_at REAL',
        '''CREATE TABLE memory_proposals (
            id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            memory_id TEXT REFERENCES memories(id),proposal_json TEXT NOT NULL,
            digest TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
            run_id INTEGER NOT NULL REFERENCES rp_runs(id),created_at REAL NOT NULL,reviewed_at REAL)''',
        'CREATE INDEX memory_proposals_scope ON memory_proposals(instance_id,status,created_at,id)',
        "CREATE UNIQUE INDEX memory_proposals_pending ON memory_proposals(instance_id,digest) WHERE status='pending'",
    ),
    21: (
        "ALTER TABLE rp_outbox ADD COLUMN plan_kind TEXT NOT NULL DEFAULT 'reply'",
        'ALTER TABLE rp_outbox ADD COLUMN plan_id TEXT',
        'ALTER TABLE rp_outbox ADD COLUMN plan_revision INTEGER',
        'ALTER TABLE rp_outbox ADD COLUMN after_seconds INTEGER',
        'ALTER TABLE rp_outbox ADD COLUMN anchor_at REAL',
        'ALTER TABLE rp_outbox ADD COLUMN minimum_gap INTEGER NOT NULL DEFAULT 1',
        '''CREATE TABLE rp_deliveries (
            id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            revision INTEGER NOT NULL,run_id INTEGER NOT NULL REFERENCES rp_runs(id),
            due_at REAL NOT NULL,text TEXT NOT NULL,parts_json TEXT NOT NULL,
            status TEXT NOT NULL,outbox_id TEXT REFERENCES rp_outbox(id),
            created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
        'CREATE INDEX rp_deliveries_scope ON rp_deliveries(instance_id,status,due_at)',
        'CREATE INDEX rp_outbox_plans ON rp_outbox(plan_kind,plan_id,status)',
        # 旧意图没有固定台词，不能伪造为新投递。历史保留，只退役执行。
        "UPDATE life_intents SET status='cancelled',resolution='cancelled',error='旧模型重评机制已退役，原事项保留供查阅。',revision=revision+1 WHERE status IN ('pending','running')",
        'UPDATE rp_states SET next_wakeup_at=NULL',
        "UPDATE rp_outbox SET status='cancelled',error='旧后台评估投递已退役。',finished_at=(julianday('now')-2440587.5)*86400.0 WHERE status='pending' AND run_id IN (SELECT id FROM rp_runs WHERE trigger_kind='life')",
    ),
    22: (
        'ALTER TABLE inbound_messages ADD COLUMN archived_at REAL',
        'ALTER TABLE sent_messages ADD COLUMN archived_at REAL',
        'ALTER TABLE rp_runs ADD COLUMN context_archived_at REAL',
        '''CREATE TABLE conversation_archives (
            id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES role_instances(id),
            created_at REAL NOT NULL,contact_name TEXT NOT NULL,character_name TEXT NOT NULL,
            rounds INTEGER NOT NULL,input_count INTEGER NOT NULL,sent_count INTEGER NOT NULL,
            from_at REAL NOT NULL,to_at REAL NOT NULL,
            UNIQUE(instance_id,created_at))''',
        'CREATE INDEX conversation_archives_scope ON conversation_archives(instance_id,created_at)',
        'CREATE INDEX inbound_archive ON inbound_messages(instance_id,archived_at,id)',
        'CREATE INDEX sent_archive ON sent_messages(instance_id,archived_at,id)',
    ),
    23: (
        'ALTER TABLE rp_deliveries ADD COLUMN resume_input_version INTEGER',
    ),
}


class Database:
    def __init__(self, path: Path, *, create: bool = False):
        self.path = path
        self._lock = RLock()
        if (path.parent / '.restore-incomplete').exists():
            raise ValueError('此目录的恢复尚未完成，请保留原数据并重新恢复到新目录。')
        if not create and not path.is_file():
            raise ValueError("数据库尚未初始化，请先运行 rp-server init。")
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=5, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

    def _migrate(self):
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError("数据库来自较新的服务版本；请使用相应版本，不可降级写入。")
        # 升级已有数据库前保存一致副本，不复制运行中的 WAL 文件。
        if 0 < version < SCHEMA_VERSION:
            target = self.path.with_name(f"{self.path.stem}.before-v{SCHEMA_VERSION}-{int(time())}-{uuid4().hex[:8]}.sqlite3")
            with closing(sqlite3.connect(target)) as backup:
                self._connection.backup(backup)
        with self.transaction() as connection:
            for number in range(version + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[number]:
                    connection.execute(statement)
                if number == 6:
                    self._migrate_model_profiles(connection)
                connection.execute(f"PRAGMA user_version={number}")

    @staticmethod
    def _migrate_model_profiles(tx):
        """旧全局配置保留为固定预设，各模型输出参数仍保持原值；不读取私人文件。"""
        rows = tx.execute('SELECT id,config_json FROM model_connections').fetchall()
        legacy = tx.execute("SELECT value_json FROM configurations WHERE key='rp_context'").fetchone()
        if not rows and not legacy:
            return
        context = json.loads(legacy['value_json']) if legacy else {}
        runtime = tx.execute("SELECT value_json FROM configurations WHERE key='rp_runtime'").fetchone()
        context['context_bytes'] = json.loads(runtime['value_json']).get('context_bytes', 64000) if runtime else 64000
        window = context.get('context_window')
        output = min(4096, window - context.get('safety_margin', 1024) - 1) if window else 4096
        content = json.dumps({'context': context, 'max_output_tokens': output, 'temperature': None}, ensure_ascii=False)
        target, now, name = uuid4().hex, time(), '迁移的上下文配置'
        tx.execute('INSERT INTO model_presets VALUES (?,?,1,?,NULL,?,?)', (target, name, content, now, now))
        tx.execute('INSERT INTO model_preset_versions VALUES (?,1,?,?,NULL,?)', (target, name, content, now))
        for row in rows:
            config = json.loads(row['config_json'])
            if config['protocol'].startswith('embedding_'):
                continue
            for model in config['models']:
                model['profile'] = {'preset_id': target, 'revision': 1, 'overrides': {
                    'max_output_tokens': model.get('max_output_tokens', 4096), 'temperature': model.get('temperature')}}
            tx.execute('UPDATE model_connections SET config_json=?,revision=revision+1 WHERE id=?', (json.dumps(config, ensure_ascii=False), row['id']))

    @contextmanager
    def transaction(self):
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.execute("COMMIT")
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def one(self, sql: str, parameters: tuple = ()) -> dict | None:
        with self._lock:
            row = self._connection.execute(sql, parameters).fetchone()
            return dict(row) if row is not None else None

    def all(self, sql: str, parameters: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._connection.execute(sql, parameters).fetchall()]

    def close(self):
        with self._lock:
            self._connection.close()
