import json
from flask import g, request, session

def log_event(db,
              action: str,
              module: str = None,
              entity_table: str = None,
              entity_id: str = None,
              message: str = None,
              old_data=None,
              new_data=None,
              meta=None,
              store_id=None,
              company_id=None,
              status_code=None):
    # best-effort actor
    actor_email = session.get("user_email") if hasattr(session, "get") else None
    actor_name = session.get("user_name") if hasattr(session, "get") else None

    request_id = getattr(g, "request_id", None)

    db.execute(
        """
        INSERT INTO sys_work_logs
          (company_id, store_id,
           actor_email, actor_name,
           request_id, session_id, method, path, status_code, ip, user_agent,
           action, module, entity_table, entity_id, message,
           old_data, new_data, meta)
        VALUES
          (%s, %s,
           %s, %s,
           %s, %s, %s, %s, %s, %s, %s,
           %s, %s, %s, %s, %s,
           %s::jsonb, %s::jsonb, %s::jsonb)
        """,
        [
            company_id, store_id,
            actor_email, actor_name,
            request_id, session.get("_id") if hasattr(session, "get") else None,
            request.method, request.path, status_code,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            request.headers.get("User-Agent"),
            action, module, entity_table, str(entity_id) if entity_id is not None else None, message,
            json.dumps(old_data, ensure_ascii=False) if old_data is not None else None,
            json.dumps(new_data, ensure_ascii=False) if new_data is not None else None,
            json.dumps(meta, ensure_ascii=False) if meta is not None else None,
        ],
    )