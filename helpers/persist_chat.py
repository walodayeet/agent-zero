import json
import os
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any

from agent import Agent, AgentConfig, AgentContext, AgentContextType
from helpers import files, history, settings, turn_journal
from helpers.litellm_transport import delete_stored_response_ids
from helpers.localization import Localization
from initialize import initialize_agent

from helpers.log import Log, LogItem

CHATS_FOLDER = "usr/chats"
LOG_SIZE = 1000
CHAT_FILE_NAME = "chat.json"
SAVED_CHAT_CONTEXT_DATA_KEY = "_persist_chat_saved"


def _fallback_datetime_iso() -> str:
    return datetime.fromtimestamp(0, tz=Localization.get().get_tzinfo()).isoformat()


def _parse_persisted_datetime(value: str | None) -> datetime:
    raw_value = value or _fallback_datetime_iso()
    dt = datetime.fromisoformat(raw_value)
    if dt.tzinfo is None:
        dt = Localization.get().localize_naive_datetime(dt)
    return dt


def get_chat_folder_path(ctxid: str):
    """
    Get the folder path for any context (chat or task).

    Args:
        ctxid: The context ID

    Returns:
        The absolute path to the context folder
    """
    return files.get_abs_path(CHATS_FOLDER, ctxid)

def get_chat_msg_files_folder(ctxid: str):
    return files.get_abs_path(get_chat_folder_path(ctxid), "messages")

def save_tmp_chat(context: AgentContext):
    """Save context to the chats folder"""
    # Skip saving BACKGROUND contexts as they should be ephemeral
    if context.type == AgentContextType.BACKGROUND:
        return

    path = _get_chat_file_path(context.id)
    data = _serialize_context(context)
    js = _safe_json_serialize(data, ensure_ascii=False)
    _write_atomic(path, js)
    update_chat_index(context)
    mark_chat_saved(context)


def save_tmp_chats():
    """Save all contexts to the chats folder"""
    for context in AgentContext.all():
        # Skip BACKGROUND contexts as they should be ephemeral
        if context.type == AgentContextType.BACKGROUND:
            continue
        save_tmp_chat(context)


def load_tmp_chats():
    """Load all contexts from the chats folder"""
    _convert_v080_chats()
    folders = files.list_files(CHATS_FOLDER, "*")
    json_files = []
    for folder_name in folders:
        chat_file = _get_chat_file_path(folder_name)
        if files.exists(chat_file):
            json_files.append(chat_file)

    ctxids = []
    with settings.version_cache_scope():
        for file in json_files:
            try:
                js = files.read_file(file)
                data = json.loads(js)
                ctx = _deserialize_context(data)
                if turn_journal.incomplete(ctx.id):
                    ctx.data["turn_journal_incomplete"] = True
                    if str(settings.get_settings().get("turn_journal_resume") or "report") == "report":
                        turn_journal.append(ctx.id, "turn_resumed")
                mark_chat_saved(ctx)
                ctxids.append(ctx.id)
            except Exception as e:
                print(f"Error loading chat {file}: {e}")
    return ctxids


CHAT_INDEX_FILE_NAME = ".a0_chat_index.json"
_INDEX_LOCK = threading.RLock()
_INDEX_META_CACHE: dict[str, dict[str, Any]] | None = None


def _chat_index_path() -> str:
    return files.get_abs_path("usr", CHAT_INDEX_FILE_NAME)


def _chat_meta_from_data(data: dict[str, Any]) -> dict[str, Any]:
    """Compact sidebar metadata, without histories/log bodies."""
    ctxid = str(data.get("id") or "")
    created = data.get("created_at") or _fallback_datetime_iso()
    log = data.get("log") or {}
    logs = log.get("logs") if isinstance(log, dict) else None
    output_data = data.get("output_data") if isinstance(data.get("output_data"), dict) else {}
    return {
        "id": ctxid,
        "name": data.get("name") or ctxid,
        "created_at": created,
        "no": 0,
        "log_guid": (log.get("guid") if isinstance(log, dict) else "") or "",
        "log_version": len(logs) if isinstance(logs, list) else 0,
        "log_length": len(logs) if isinstance(logs, list) else 0,
        "paused": False,
        "last_message": data.get("last_message") or created,
        "type": data.get("type") or AgentContextType.USER.value,
        "running": False,
        "agent_profile": data.get("agent_profile") or "",
        **output_data,
    }


def _read_chat_index() -> dict[str, Any]:
    path = _chat_index_path()
    if not files.exists(path):
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(files.read_file(path))
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except Exception:
        pass
    return {"version": 1, "entries": {}}


def _write_chat_index(index: dict[str, Any]) -> None:
    _write_atomic(_chat_index_path(), json.dumps(index, ensure_ascii=False))


def refresh_chat_index() -> dict[str, Any]:
    """Build/refresh the sidebar metadata index by stat-ing chat files.

    Only chats whose file changed since the indexed size/mtime are parsed. The
    index lets startup render the sidebar without deserializing every history.
    """
    with _INDEX_LOCK:
        return _refresh_chat_index_locked()


def _refresh_chat_index_locked() -> dict[str, Any]:
    global _INDEX_META_CACHE
    index = _read_chat_index()
    entries: dict[str, Any] = index.get("entries") or {}
    root = files.get_abs_path(CHATS_FOLDER)
    seen: set[str] = set()
    dirty = False
    try:
        folders = [entry for entry in os.scandir(root) if entry.is_dir(follow_symlinks=False)]
    except OSError:
        folders = []
    for folder in folders:
        ctxid = folder.name
        chat_file = os.path.join(folder.path, CHAT_FILE_NAME)
        try:
            stat = os.stat(chat_file)
        except OSError:
            continue
        seen.add(ctxid)
        cached = entries.get(ctxid) or {}
        signature = [stat.st_mtime_ns, stat.st_size]
        if cached.get("signature") == signature and isinstance(cached.get("meta"), dict):
            continue
        try:
            data = json.loads(files.read_file(chat_file))
            meta = _chat_meta_from_data(data)
        except Exception:
            continue
        entries[ctxid] = {"signature": signature, "meta": meta}
        dirty = True
    for ctxid in list(entries):
        if ctxid not in seen:
            entries.pop(ctxid, None)
            dirty = True
    if dirty:
        index = {"version": 1, "entries": entries}
        _write_chat_index(index)
    _INDEX_META_CACHE = {
        ctxid: dict(entry["meta"])
        for ctxid, entry in entries.items()
        if isinstance(entry.get("meta"), dict)
    }
    return index


def indexed_chat_outputs() -> list[dict[str, Any]]:
    """Return cached sidebar metadata. No per-push disk scan or JSON refresh."""
    global _INDEX_META_CACHE
    with _INDEX_LOCK:
        if _INDEX_META_CACHE is None:
            index = _read_chat_index()
            _INDEX_META_CACHE = {
                ctxid: dict(entry["meta"])
                for ctxid, entry in (index.get("entries") or {}).items()
                if isinstance(entry.get("meta"), dict)
            }
        return [dict(row) for row in _INDEX_META_CACHE.values()]


def _cache_index_meta(ctxid: str, meta: dict[str, Any] | None) -> None:
    global _INDEX_META_CACHE
    with _INDEX_LOCK:
        if _INDEX_META_CACHE is None:
            _INDEX_META_CACHE = {}
        if meta is None:
            _INDEX_META_CACHE.pop(ctxid, None)
        else:
            _INDEX_META_CACHE[ctxid] = dict(meta)


def _chat_meta_from_context(context: AgentContext) -> dict[str, Any]:
    output_data = {
        k: v
        for k, v in (getattr(context, "output_data", None) or {}).items()
        if not k.startswith("_")
    }
    log = getattr(context, "log", None)
    profile = ""
    agent = getattr(context, "agent0", None)
    if agent is not None and getattr(agent, "config", None) is not None:
        profile = str(getattr(agent.config, "profile", "") or "")
    elif getattr(context, "config", None) is not None:
        profile = str(getattr(context.config, "profile", "") or "")
    return {
        "id": context.id,
        "name": getattr(context, "name", None) or context.id,
        "created_at": (
            Localization.get().serialize_datetime(getattr(context, "created_at", None))
            if getattr(context, "created_at", None)
            else _fallback_datetime_iso()
        ),
        "no": getattr(context, "no", 0),
        "log_guid": getattr(log, "guid", "") or "",
        "log_version": len(getattr(log, "updates", []) or []),
        "log_length": len(getattr(log, "logs", []) or []),
        "paused": bool(getattr(context, "paused", False)),
        "last_message": (
            Localization.get().serialize_datetime(getattr(context, "last_message", None))
            if getattr(context, "last_message", None)
            else _fallback_datetime_iso()
        ),
        "type": getattr(getattr(context, "type", None), "value", AgentContextType.USER.value),
        "running": False,
        "agent_profile": profile,
        **output_data,
    }


def update_chat_index(context: AgentContext) -> None:
    try:
        chat_file = _get_chat_file_path(context.id)
        stat = os.stat(chat_file)
    except Exception:
        return
    try:
        meta = _chat_meta_from_context(context)
        with _INDEX_LOCK:
            index = _read_chat_index()
            entries = index.setdefault("entries", {})
            entries[context.id] = {
                "signature": [stat.st_mtime_ns, stat.st_size],
                "meta": meta,
            }
            _write_chat_index({"version": 1, "entries": entries})
            _cache_index_meta(context.id, meta)
    except Exception:
        return


def remove_chat_index_entry(ctxid: str) -> None:
    with _INDEX_LOCK:
        index = _read_chat_index()
        entries = index.get("entries") or {}
        if ctxid in entries:
            entries.pop(ctxid, None)
            _write_chat_index({"version": 1, "entries": entries})
        _cache_index_meta(ctxid, None)


_HYDRATE_LOCKS: dict[str, threading.Lock] = {}
_HYDRATE_LOCKS_GUARD = threading.Lock()


def load_chat(ctxid: str) -> AgentContext | None:
    """Hydrate one saved chat on demand (single-flight, no-op if in memory)."""
    existing = AgentContext.get(ctxid)
    if existing:
        return existing
    with _HYDRATE_LOCKS_GUARD:
        lock = _HYDRATE_LOCKS.setdefault(ctxid, threading.Lock())
    with lock:
        existing = AgentContext.get(ctxid)
        if existing:
            return existing
        path = _get_chat_file_path(ctxid)
        if not files.exists(path):
            return None
        try:
            data = json.loads(files.read_file(path))
        except Exception:
            return None
        context = _deserialize_context(data)
        if turn_journal.incomplete(ctxid):
            context.data["turn_journal_incomplete"] = True
            if str(settings.get_settings().get("turn_journal_resume") or "report") == "report":
                turn_journal.append(ctxid, "turn_resumed")
        mark_chat_saved(context)
        update_chat_index(context)
        return context


def _get_chat_file_path(ctxid: str):
    return files.get_abs_path(CHATS_FOLDER, ctxid, CHAT_FILE_NAME)


def chat_file_exists(ctxid: str) -> bool:
    """True if this context has a persisted chat.json on disk."""
    return files.exists(_get_chat_file_path(ctxid))


def _write_atomic(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content.encode("utf-8", "replace").decode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        files.fsync_directory(directory)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def mark_chat_saved(context: AgentContext) -> None:
    context.data[SAVED_CHAT_CONTEXT_DATA_KEY] = True


_saved_chat_ids_cache: tuple[Any, set[str]] | None = None


def saved_chat_ids() -> set[str]:
    """Chat folders that contain a chat file; cached until the chats dir changes."""
    global _saved_chat_ids_cache

    root = files.get_abs_path(CHATS_FOLDER)
    try:
        root_mtime: Any = os.stat(root).st_mtime_ns
    except OSError:
        root_mtime = None

    # Root mtime alone misses a deleted/added chat file inside an existing folder.
    children: list[tuple[str, int, int, int]] = []
    if root_mtime is not None:
        try:
            with os.scandir(root) as iterator:
                for entry in iterator:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        dir_mtime = entry.stat(follow_symlinks=False).st_mtime_ns
                        chat_file = os.path.join(entry.path, CHAT_FILE_NAME)
                        try:
                            chat_stat = os.stat(chat_file)
                            chat_stamp = (chat_stat.st_mtime_ns, chat_stat.st_size)
                        except OSError:
                            chat_stamp = (-1, -1)
                        children.append((entry.name, dir_mtime, *chat_stamp))
                    except OSError:
                        continue
        except OSError:
            pass
    signature: Any = (root_mtime, tuple(sorted(children)))

    if _saved_chat_ids_cache is not None and _saved_chat_ids_cache[0] == signature:
        return set(_saved_chat_ids_cache[1])

    ids = {
        files.basename(files.dirname(path))
        for path in files.find_existing_paths_by_pattern(
            files.get_abs_path(CHATS_FOLDER, "*", CHAT_FILE_NAME)
        )
    }
    _saved_chat_ids_cache = (signature, ids)
    return set(ids)


def _convert_v080_chats():
    json_files = files.list_files(CHATS_FOLDER, "*.json")
    for file in json_files:
        path = files.get_abs_path(CHATS_FOLDER, file)
        name = file.rstrip(".json")
        new = _get_chat_file_path(name)
        files.move_file(path, new)


def load_json_chats(jsons: list[str]):
    """Load contexts from JSON strings"""
    ctxids = []
    for js in jsons:
        data = json.loads(js)
        if "id" in data:
            del data["id"]  # remove id to get new
        ctx = _deserialize_context(data)
        ctxids.append(ctx.id)
    return ctxids


def export_json_chat(context: AgentContext):
    """Export context as JSON string"""
    data = _serialize_context(context)
    js = _safe_json_serialize(data, ensure_ascii=False)
    return js


def remove_chat(ctxid):
    """Remove a chat or task context"""
    if not isinstance(ctxid, str) or not ctxid.strip():
        raise ValueError("remove_chat: context id must not be empty")
    _delete_provider_responses_for_chat(ctxid)
    path = get_chat_folder_path(ctxid)
    files.delete_dir(path)
    remove_chat_index_entry(ctxid)


def remove_msg_files(ctxid):
    """Remove all message files for a chat or task context"""
    if not isinstance(ctxid, str) or not ctxid.strip():
        raise ValueError("remove_msg_files: context id must not be empty")
    path = get_chat_msg_files_folder(ctxid)
    files.delete_dir(path)


def _serialize_context(context: AgentContext):
    profile = str(
        getattr(context.agent0.config, "profile", None)
        or getattr(context.config, "profile", None)
        or ""
    )

    # serialize agents
    agents = []
    agent = context.agent0
    while agent:
        agents.append(_serialize_agent(agent))
        agent = agent.data.get(Agent.DATA_NAME_SUBORDINATE, None)


    data = {k: v for k, v in context.data.items() if not k.startswith("_")}
    output_data = {k: v for k, v in context.output_data.items() if not k.startswith("_")}

    return {
        "id": context.id,
        "name": context.name,
        "created_at": (
            Localization.get().serialize_datetime(context.created_at)
            if context.created_at
            else _fallback_datetime_iso()
        ),
        "type": context.type.value,
        "last_message": (
            Localization.get().serialize_datetime(context.last_message)
            if context.last_message
            else _fallback_datetime_iso()
        ),
        "agents": agents,
        "streaming_agent": (
            context.streaming_agent.number if context.streaming_agent else 0
        ),
        "agent_profile": profile,
        "log": _serialize_log(context.log),
        "data": data,
        "output_data": output_data,
    }


def _serialize_agent(agent: Agent):
    data = {k: v for k, v in agent.data.items() if not k.startswith("_")}

    history = agent.history.serialize()

    return {
        "number": agent.number,
        "agent_profile": str(getattr(agent.config, "profile", "") or ""),
        "data": data,
        "history": history,
    }


def _serialize_log(log: Log):
    # Guard against concurrent log mutations while serializing.
    with log._lock:
        logs = [item.output() for item in log.logs[-LOG_SIZE:]]  # serialize LogItem objects
        guid = log.guid
        progress = log.progress
        progress_no = log.progress_no
    return {
        "guid": guid,
        "logs": logs,
        "progress": progress,
        "progress_no": progress_no,
    }


def _deserialize_context(data):
    profile = data.get("agent_profile")
    override_settings = {"agent_profile": profile} if profile else None
    config = initialize_agent(override_settings=override_settings)
    log = _deserialize_log(data.get("log", None))

    context = AgentContext(
        config=config,
        id=data.get("id", None),  # get new id
        name=data.get("name", None),
        created_at=(
            _parse_persisted_datetime(data.get("created_at"))
        ),
        type=AgentContextType(data.get("type", AgentContextType.USER.value)),
        last_message=(
            _parse_persisted_datetime(data.get("last_message"))
        ),
        log=log,
        paused=False,
        data=data.get("data", {}),
        output_data=data.get("output_data", {}),
        # agent0=agent0,
        # streaming_agent=straming_agent,
    )

    agents = data.get("agents", [])
    agent0 = _deserialize_agents(agents, config, context)
    streaming_agent = agent0
    while streaming_agent and streaming_agent.number != data.get("streaming_agent", 0):
        streaming_agent = streaming_agent.data.get(Agent.DATA_NAME_SUBORDINATE, None)

    context.agent0 = agent0
    context.config = agent0.config
    context.streaming_agent = streaming_agent

    return context


def _deserialize_agent_config(
    agent_data: dict[str, Any], fallback_config: AgentConfig
) -> AgentConfig:
    fallback_profile = str(getattr(fallback_config, "profile", "") or "")
    profile = str(agent_data.get("agent_profile") or fallback_profile).strip()
    if profile == fallback_profile:
        return fallback_config
    override_settings = {"agent_profile": profile} if profile else None
    return initialize_agent(override_settings=override_settings)


def _deserialize_agents(
    agents: list[dict[str, Any]], config: AgentConfig, context: AgentContext
) -> Agent:
    prev: Agent | None = None
    zero: Agent | None = None

    for ag in agents:
        current = Agent(
            number=ag["number"],
            config=_deserialize_agent_config(ag, config),
            context=context,
        )
        current.data = ag.get("data", {})
        current.history = history.deserialize_history(
            ag.get("history", ""), agent=current
        )
        if not zero:
            zero = current

        if prev:
            prev.set_data(Agent.DATA_NAME_SUBORDINATE, current)
            current.set_data(Agent.DATA_NAME_SUPERIOR, prev)
        prev = current

    return zero or Agent(0, config, context)


# def _deserialize_history(history: list[dict[str, Any]]):
#     result = []
#     for hist in history:
#         content = hist.get("content", "")
#         msg = (
#             HumanMessage(content=content)
#             if hist.get("type") == "human"
#             else AIMessage(content=content)
#         )
#         result.append(msg)
#     return result


def _deserialize_log(data: dict[str, Any]) -> "Log":
    log = Log()
    log.guid = data.get("guid", str(uuid.uuid4()))
    log.set_initial_progress()

    # Deserialize the list of LogItem objects
    i = 0
    for item_data in data.get("logs", []):
        agentno = item_data.get("agentno")
        if agentno is None:
            agentno = item_data.get("agent_number", 0)
        log.logs.append(
            LogItem(
                log=log,  # restore the log reference
                no=i,  # item_data["no"],
                type=item_data["type"],
                heading=item_data.get("heading", ""),
                content=item_data.get("content", ""),
                kvps=OrderedDict(item_data["kvps"]) if item_data["kvps"] else None,
                timestamp=item_data.get("timestamp", 0.0),
                agentno=agentno,
                id=item_data.get("id"),
            )
        )
        log.updates.append(i)
        i += 1

    return log


def _safe_json_serialize(obj, **kwargs):
    def serializer(o):
        if isinstance(o, dict):
            return {k: v for k, v in o.items() if is_json_serializable(v)}
        elif isinstance(o, (list, tuple)):
            return [item for item in o if is_json_serializable(item)]
        elif is_json_serializable(o):
            return o
        else:
            return None  # Skip this property

    def is_json_serializable(item):
        try:
            json.dumps(item)
            return True
        except (TypeError, OverflowError):
            return False

    return json.dumps(obj, default=serializer, **kwargs)


def _delete_provider_responses_for_chat(ctxid: str) -> None:
    try:
        data = json.loads(files.read_file(_get_chat_file_path(ctxid)))
    except Exception:
        return
    if _responses_delete_disabled(data):
        return
    response_ids = _collect_response_ids(data)
    if not response_ids:
        return
    delete_stored_response_ids(response_ids)


def _responses_delete_disabled(data: dict[str, Any]) -> bool:
    if data.get("responses_delete_on_chat_delete") is False:
        return True
    context_data = data.get("data")
    if isinstance(context_data, dict) and context_data.get("responses_delete_on_chat_delete") is False:
        return True
    for agent_data in data.get("agents", []) or []:
        if not isinstance(agent_data, dict):
            continue
        state = agent_data.get("data")
        if isinstance(state, dict) and state.get("responses_delete_on_chat_delete") is False:
            return True
    return False


def _collect_response_ids(data: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        response_id = str(value or "").strip()
        if response_id and response_id not in seen:
            seen.add(response_id)
            found.append(response_id)

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            state = obj.get(Agent.DATA_NAME_RESPONSES_STATE)
            if isinstance(state, dict):
                add(state.get("response_id"))
                for response_id in state.get("response_ids") or []:
                    add(response_id)

            metadata = obj.get("metadata")
            if isinstance(metadata, dict):
                responses = metadata.get("responses")
                if isinstance(responses, dict):
                    add(responses.get("response_id"))

            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
        elif isinstance(obj, str) and '"response_id"' in obj:
            try:
                walk(json.loads(obj))
            except Exception:
                return

    walk(data)
    return found
