"""资料有效性的共用SQL条件；只接受源码内的列引用，不接收模型或浏览器SQL。"""


def source_valid_sql(instance, kind, identifier, now):
    """单读与记忆召回共用；运行截止、完整轮次及资料库授权由调用方另行检查。"""
    return f'''(NOT EXISTS (SELECT 1 FROM evidence_status es WHERE es.instance_id={instance}
        AND es.kind={kind} AND es.source_id={identifier} AND es.invalid=1)
        AND CASE {kind}
        WHEN 'input' THEN EXISTS (SELECT 1 FROM inbound_messages i WHERE i.instance_id={instance}
            AND i.id={identifier} AND i.deleted_at IS NULL)
        WHEN 'sent' THEN EXISTS (SELECT 1 FROM sent_messages s WHERE s.instance_id={instance}
            AND s.id={identifier} AND s.deleted_at IS NULL)
        WHEN 'life' THEN EXISTS (SELECT 1 FROM life_events e JOIN rp_runs r ON r.id=e.run_id
            WHERE e.instance_id={instance} AND e.id={identifier} AND e.kind='decision'
            AND r.status='completed' AND r.context_deleted_at IS NULL)
        WHEN 'world_event' THEN EXISTS (SELECT 1 FROM world_events e JOIN rp_runs r ON r.id=e.adopted_run_id
            WHERE e.instance_id={instance} AND e.id={identifier} AND e.status='adopted'
            AND r.status='completed' AND r.context_deleted_at IS NULL)
        WHEN 'observation' THEN EXISTS (SELECT 1 FROM observations o JOIN life_extensions p ON p.instance_id=o.instance_id
            WHERE o.instance_id={instance} AND o.id={identifier} AND o.status='observed'
            AND o.expires_at>{now} AND o.policy_revision=p.revision
            AND json_extract(p.config_json,'$.observations_enabled')=1
            AND (o.source_run_id IS NULL OR EXISTS (SELECT 1 FROM rp_runs r WHERE r.id=o.source_run_id
                AND r.instance_id=o.instance_id AND r.context_deleted_at IS NULL)))
        ELSE 0 END)'''


# 生效关系／来源完整性与“active且未过期”分开：管理员可显式将到期记忆转为长期画像。
VALID_MEMORY = '''m.superseded_by IS NULL
    AND NOT EXISTS (SELECT 1 FROM memory_versions v JOIN rp_runs r ON r.id=v.run_id
        WHERE v.memory_id=m.id AND v.revision=m.revision AND r.context_deleted_at IS NOT NULL)
    AND NOT EXISTS (SELECT 1 FROM memory_evidence me WHERE me.memory_id=m.id AND NOT ''' + source_valid_sql(
        'm.instance_id', 'me.kind', 'me.source_id', "((julianday('now')-2440587.5)*86400.0)") + ')'


def active_memory_sql(now='?'):
    return f"(m.status='active' AND m.kind<>'temporary' AND ({VALID_MEMORY}) AND (m.expires_at IS NULL OR m.expires_at>{now}))"
