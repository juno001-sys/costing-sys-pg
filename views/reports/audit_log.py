import json
from flask import g, request, session


def log_event(
    db,
    action,
    module=None,
    entity_table=None,
    entity_id=None,
    message=None,
    old_data=None,
    new_data=None,
    meta=None,
    store_id=None,
    company_id=None,
    status_code=None,
):
    # actor from auth loader (views/auth/login.py before_request)
    cu = getattr(g, "current_user", None)
    actor_user_id = cu.get("id") if cu else None
    actor_email = cu.get("email") if cu else None
    actor_name = cu.get("name") if cu else None

    # session token is sys_sessions.id
    session_id = session.get("session_token") if hasattr(session, "get") else None

    # company default (optional)
    if company_id is None:
        company_id = getattr(g, "current_company_id", None)

    request_id = getattr(g, "request_id", None)

    db.execute(
        """
        INSERT INTO sys_work_logs
          (company_id, store_id,
           actor_user_id, actor_email, actor_name,
           request_id, session_id, method, path, status_code, ip, user_agent,
           action, module, entity_table, entity_id, message,
           old_data, new_data, meta)
        VALUES
          (%s, %s,
           %s, %s, %s,
           %s, %s, %s, %s, %s, %s, %s,
           %s, %s, %s, %s, %s,
           %s::jsonb, %s::jsonb, %s::jsonb)
        """,
        [
            company_id, store_id,
            actor_user_id, actor_email, actor_name,
            request_id, session_id,
            request.method, request.path, status_code,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            request.headers.get("User-Agent"),
            action, module, entity_table,
            str(entity_id) if entity_id is not None else None, message,
            json.dumps(old_data, ensure_ascii=False) if old_data is not None else None,
            json.dumps(new_data, ensure_ascii=False) if new_data is not None else None,
            json.dumps(meta, ensure_ascii=False) if meta is not None else None,
        ],
    )