import asyncio, contextvars, json, random, re, string, threading, time

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Coroutine, Dict, Literal
from enum import Enum
import models

from helpers import (
    extract_tools,
    files,
    errors,
    history,
    responses_history,
    tokens,
    context as context_helper,
    dirty_json,
    subagents,
)
from helpers import extension
from helpers.turn_metrics import TurnMetrics
from helpers import turn_journal
from helpers.print_style import PrintStyle

from langchain_core.prompts import (
    ChatPromptTemplate,
)
from langchain_core.messages import SystemMessage, BaseMessage

import helpers.log as Log
from helpers.dirty_json import DirtyJson
from helpers.defer import DeferredTask
from typing import Callable
from helpers.localization import Localization
from helpers import extension
from helpers.errors import RepairableException, InterventionException, HandledException
from helpers.llm_result import (
    LLMResult,
    RESPONSE_METADATA_KEY,
    function_call_output_item,
    metadata_from_llm_result,
    result_from_metadata,
)
from helpers.litellm_transport import ResponsesTransport, TransportMode
from helpers.responses_tools import build_responses_function_tools, original_tool_name

_RESPONSE_STREAM_UPDATE_CHARS = 128
_RESPONSE_STREAM_UPDATE_SECONDS = 0.05


class AgentContextType(Enum):
    USER = "user"
    TASK = "task"
    BACKGROUND = "background"


class AgentContext:

    _contexts: dict[str, "AgentContext"] = {}
    _contexts_lock = threading.RLock()
    _counter: int = 0
    _notification_manager = None

    @extension.extensible
    def __init__(
        self,
        config: "AgentConfig",
        id: str | None = None,
        name: str | None = None,
        agent0: "Agent|None" = None,
        log: Log.Log | None = None,
        paused: bool = False,
        streaming_agent: "Agent|None" = None,
        created_at: datetime | None = None,
        type: AgentContextType = AgentContextType.USER,
        last_message: datetime | None = None,
        data: dict | None = None,
        output_data: dict | None = None,
        set_current: bool = False,
    ):
        # initialize context
        self.id = id or AgentContext.generate_id()
        existing = None
        with AgentContext._contexts_lock:
            existing = AgentContext._contexts.get(self.id, None)
            if existing:
                AgentContext._contexts.pop(self.id, None)
            AgentContext._contexts[self.id] = self
        if existing and existing.task:
            existing.task.kill()
        if set_current:
            AgentContext.set_current(self.id)

        # initialize state
        self.name = name
        self.config = config
        self.data = data or {}
        self.output_data = output_data or {}
        self.log = log or Log.Log()
        self.log.context = self
        self.paused = paused
        self.streaming_agent = streaming_agent
        self.task: DeferredTask | None = None
        self.created_at = created_at or Localization.get().now()
        self.type = type
        AgentContext._counter += 1
        self.no = AgentContext._counter
        self.last_message = last_message or Localization.get().now()

        # initialize agent at last (context is complete now)
        self.agent0 = agent0 or Agent(0, self.config, self)
        if last_message is not None:
            self.last_message = last_message

    @staticmethod
    def get(id: str):
        with AgentContext._contexts_lock:
            return AgentContext._contexts.get(id, None)

    @staticmethod
    def use(id: str):
        context = AgentContext.get(id)
        if not context and id:
            # Lazy chats: hydrate a saved chat before anything creates a new
            # context with the same id (which would overwrite it on save).
            from helpers import persist_chat

            context = persist_chat.load_chat(id)
        if context:
            AgentContext.set_current(id)
        else:
            AgentContext.set_current("")
        return context

    @staticmethod
    def current():
        ctxid = context_helper.get_context_data("agent_context_id", "")
        if not ctxid:
            return None
        return AgentContext.get(ctxid)

    @staticmethod
    def set_current(ctxid: str):
        context_helper.set_context_data("agent_context_id", ctxid)

    @staticmethod
    def first():
        with AgentContext._contexts_lock:
            if not AgentContext._contexts:
                return None
            return list(AgentContext._contexts.values())[0]

    @staticmethod
    def all():
        with AgentContext._contexts_lock:
            return list(AgentContext._contexts.values())

    @staticmethod
    def generate_id():
        def generate_short_id():
            return "".join(random.choices(string.ascii_letters + string.digits, k=8))

        from helpers import persist_chat

        while True:
            short_id = generate_short_id()
            with AgentContext._contexts_lock:
                if short_id not in AgentContext._contexts:
                    # Lazy chats: never reuse an id that still has a saved chat.
                    if not persist_chat.chat_file_exists(short_id):
                        return short_id

    @classmethod
    def get_notification_manager(cls):
        if cls._notification_manager is None:
            from helpers.notification import NotificationManager  # type: ignore

            cls._notification_manager = NotificationManager()
        return cls._notification_manager

    @staticmethod
    @extension.extensible
    def remove(id: str):
        with AgentContext._contexts_lock:
            context = AgentContext._contexts.pop(id, None)
        if context and context.task:
            context.task.kill()
        return context

    def get_data(self, key: str, recursive: bool = True):
        # recursive is not used now, prepared for context hierarchy
        return self.data.get(key, None)

    def set_data(self, key: str, value: Any, recursive: bool = True):
        # recursive is not used now, prepared for context hierarchy
        self.data[key] = value

    def get_output_data(self, key: str, recursive: bool = True):
        # recursive is not used now, prepared for context hierarchy
        return self.output_data.get(key, None)

    def set_output_data(self, key: str, value: Any, recursive: bool = True):
        # recursive is not used now, prepared for context hierarchy
        self.output_data[key] = value

    # @extension.extensible
    def output(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": (
                Localization.get().serialize_datetime(self.created_at)
                if self.created_at
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "no": self.no,
            "log_guid": self.log.guid,
            "log_version": len(self.log.updates),
            "log_length": len(self.log.logs),
            "paused": self.paused,
            "last_message": (
                Localization.get().serialize_datetime(self.last_message)
                if self.last_message
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "type": self.type.value,
            "running": self.is_running(),
            **self.output_data,
        }

    @staticmethod
    def log_to_all(
        type: Log.Type,
        heading: str | None = None,
        content: str | None = None,
        kvps: dict | None = None,
        update_progress: Log.ProgressUpdate | None = None,
        id: str | None = None,  # Add id parameter
        **kwargs,
    ) -> list[Log.LogItem]:
        items: list[Log.LogItem] = []
        for context in AgentContext.all():
            items.append(
                context.log.log(
                    type, heading, content, kvps, update_progress, id, **kwargs
                )
            )
        return items

    @extension.extensible
    def kill_process(self):
        if self.task:
            self.task.kill()

    @extension.extensible
    def reset(self):
        self.kill_process()
        self.log.reset()
        self.agent0 = Agent(0, self.config, self)
        self.streaming_agent = None
        self.paused = False

    @extension.extensible
    def nudge(self):
        self.kill_process()
        self.paused = False
        self.task = self.communicate(
            UserMessage("", system_message=[self.agent0.read_prompt("fw.msg_nudge.md")])
        )
        return self.task

    @extension.extensible
    def get_agent(self):
        return self.streaming_agent or self.agent0

    def is_running(self) -> bool:
        return (self.task and self.task.is_alive()) or False

    @extension.extensible
    def communicate(self, msg: "UserMessage", broadcast_level: int = 1):
        self.paused = False  # unpause if paused

        current_agent = self.get_agent()

        if self.task and self.task.is_alive():
            # set intervention messages to agent(s):
            intervention_agent = current_agent
            while intervention_agent and broadcast_level != 0:
                intervention_agent.intervention = msg
                broadcast_level -= 1
                intervention_agent = intervention_agent.data.get(
                    Agent.DATA_NAME_SUPERIOR, None
                )
        else:
            self.task = self.run_task(self._process_chain, current_agent, msg)

        return self.task

    @extension.extensible
    def run_task(
        self, func: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kwargs: Any
    ):
        if not self.task:
            self.task = DeferredTask(
                thread_name=self.__class__.__name__,
            )
        self.task.start_task(func, *args, **kwargs)
        return self.task

    # this wrapper ensures that superior agents are called back if the chat was loaded from file and original callstack is gone
    @extension.extensible
    async def _process_chain(self, agent: "Agent", msg: "UserMessage|str", user=True):
        try:
            msg_template = (
                agent.hist_add_user_message(msg)  # type: ignore
                if user
                else agent.hist_add_tool_result(
                    tool_name="call_subordinate", tool_result=msg  # type: ignore
                )
            )
            response = await agent.monologue()  # type: ignore
            superior = agent.data.get(Agent.DATA_NAME_SUPERIOR, None)
            if superior:
                response = await self._process_chain(superior, response, False)  # type: ignore

            # call end of process extensions
            await extension.call_extensions_async("process_chain_end", agent=self.get_agent(), data={})

            return response
        except Exception as e:
            await self.handle_exception("process_chain", e)

    @extension.extensible
    async def handle_exception(self, location: str, exception: Exception):
        if exception:
            raise exception # exception handling is done by extensions


@dataclass
class AgentConfig:
    mcp_servers: str
    profile: str = ""
    knowledge_subdirs: list[str] = field(default_factory=lambda: ["default", "custom"])
    additional: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UserMessage:
    message: str
    attachments: list[str] = field(default_factory=list[str])
    system_message: list[str] = field(default_factory=list[str])
    id: str = ""


class LoopData:
    @property
    def current_tool(self):
        return self._current_tool.get()

    @current_tool.setter
    def current_tool(self, tool):
        self._current_tool.set(tool)

    def __init__(self, **kwargs):
        self._current_tool = contextvars.ContextVar(
            f"a0_current_tool_{id(self)}", default=None
        )
        self.iteration = -1
        self.system = []
        self.user_message: history.Message | None = None
        self.history_output: list[history.OutputMessage] = []
        self.protocol_temporary: OrderedDict[str, history.MessageContent] = OrderedDict()
        self.protocol_persistent: OrderedDict[str, history.MessageContent] = OrderedDict()
        self.extras_temporary: OrderedDict[str, history.MessageContent] = OrderedDict()
        self.extras_persistent: OrderedDict[str, history.MessageContent] = OrderedDict()
        self.last_response = ""
        self.params_temporary: dict = {}
        self.params_persistent: dict = {}
        self.current_tool = None

        # override values with kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


def _is_fallback_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (401, 402, 403, 408, 429) or status >= 500
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    try:
        import openai

        if isinstance(
            exc,
            (openai.APIConnectionError, openai.APITimeoutError),
        ):
            return True
    except Exception:
        pass
    return False


class Agent:

    DATA_NAME_SUPERIOR = "_superior"
    DATA_NAME_SUBORDINATE = "_subordinate"
    DATA_NAME_CTX_WINDOW = "ctx_window"
    DATA_NAME_RESPONSES_STATE = "responses_state"
    DATA_NAME_RESPONSES_TOOL_NAME_MAP = "responses_tool_name_map"
    DATA_NAME_RESPONSES_COMPUTER_SESSION = "responses_computer_session_id"

    @extension.extensible
    def __init__(
        self, number: int, config: AgentConfig, context: AgentContext | None = None
    ):

        # agent config
        self.config = config

        # agent context
        self.context = context or AgentContext(config=config, agent0=self)

        # non-config vars
        self.number = number
        self.agent_name = f"A{self.number}"

        self.history = history.History(self)  # type: ignore[abstract]
        self.last_user_message: history.Message | None = None
        self.intervention: UserMessage | None = None
        self.data: dict[str, Any] = {}  # free data object all the tools can use

        extension.call_extensions_sync("agent_init", self)

    def _journal_ctx(self) -> str:
        return getattr(getattr(self, "context", None), "id", "")

    @extension.extensible
    async def monologue(self):
        # The static system prompt is stable for the duration of one turn.
        # Reuse it so tool-result -> next-model-call does not re-render tools,
        # skills, presets and prompt includes every iteration.
        self._system_prompt_cache = None
        self._turn_started = time.perf_counter()
        try:
            _metrics_model = getattr(self.get_chat_model(), "model_name", "")
        except Exception:
            _metrics_model = ""
        self._turn_metrics = TurnMetrics(self._journal_ctx(), _metrics_model)
        turn_journal.append(self._journal_ctx(), "turn_start")
        _turn_status = "ok"
        while True:
            try:
                # loop data dictionary to pass to extensions
                self.loop_data = LoopData(user_message=self.last_user_message)
                # call monologue_start extensions
                await extension.call_extensions_async(
                    "monologue_start", self, loop_data=self.loop_data
                )

                printer = PrintStyle(italic=True, font_color="#b3ffd9", padding=False)

                # let the agent run message loop until he stops it with a response tool
                while True:

                    self.context.streaming_agent = self  # mark self as current streamer
                    self.loop_data.iteration += 1
                    self.loop_data.params_temporary = {}  # clear temporary params
                    last_response_stream_full = ""
                    last_response_stream_chars = 0
                    last_response_stream_at = time.monotonic()
                    response_stream_pending = False

                    # call message_loop_start extensions
                    await extension.call_extensions_async(
                        "message_loop_start", self, loop_data=self.loop_data
                    )
                    await self.handle_intervention()

                    try:
                        try:
                            from helpers import settings as _settings

                            _max_turns = int(_settings.get_settings().get("agent_max_turns") or 0) or int(__import__("os").environ.get("A0_MAX_TURNS", "0"))
                        except Exception:
                            _max_turns = int(__import__("os").environ.get("A0_MAX_TURNS", "0"))
                        if _max_turns > 0 and self.loop_data.iteration >= _max_turns:
                            _turn_status = "max_turns"
                            turn_journal.append(self._journal_ctx(), "max_turns", limit=_max_turns)
                            result = await self._execute_tool_request(
                                tool_name="response",
                                tool_args={
                                    "text": f"Stopped after {_max_turns} model turns (agent_max_turns)."
                                },
                                message="",
                            )
                            return result

                        try:
                            _turn_timeout = int(
                                _settings.get_settings().get("agent_turn_timeout_seconds") or 0
                            ) or int(__import__("os").environ.get("A0_TURN_TIMEOUT", "0"))
                        except Exception:
                            _turn_timeout = int(__import__("os").environ.get("A0_TURN_TIMEOUT", "0"))
                        if _turn_timeout > 0 and time.perf_counter() - self._turn_started > _turn_timeout:
                            _turn_status = "timeout"
                            turn_journal.append(self._journal_ctx(), "turn_timeout", limit=_turn_timeout)
                            return await self._execute_tool_request(
                                tool_name="response",
                                tool_args={
                                    "text": f"Stopped after {_turn_timeout}s (agent_turn_timeout_seconds)."
                                },
                                message="",
                            )

                        # prepare LLM chain (model, system, history)
                        prompt = await self.prepare_prompt(loop_data=self.loop_data)

                        # call before_main_llm_call extensions
                        await extension.call_extensions_async(
                            "before_main_llm_call", self, loop_data=self.loop_data
                        )
                        await self.handle_intervention()


                        async def reasoning_callback(chunk: str, full: str):
                            await self.handle_intervention()
                            if chunk == full:
                                printer.print("Reasoning: ")  # start of reasoning
                            # Pass chunk and full data to extensions for processing
                            stream_data = {"chunk": chunk, "full": full}
                            await extension.call_extensions_async(
                                "reasoning_stream_chunk",
                                self,
                                loop_data=self.loop_data,
                                stream_data=stream_data,
                            )
                            # Stream masked chunk after extensions processed it
                            if stream_data.get("chunk"):
                                printer.stream(stream_data["chunk"])
                            # Use the potentially modified full text for downstream processing
                            await self.handle_reasoning_stream(stream_data["full"])

                        async def stream_callback(chunk: str, full: str):
                            nonlocal last_response_stream_full, last_response_stream_chars
                            nonlocal last_response_stream_at, response_stream_pending
                            await self.handle_intervention()
                            # output the agent response stream
                            if chunk == full:
                                printer.print("Response: ")  # start of response
                            # Pass chunk and full data to extensions for processing
                            stream_data = {"chunk": chunk, "full": full}
                            await extension.call_extensions_async(
                                "response_stream_chunk",
                                self,
                                loop_data=self.loop_data,
                                stream_data=stream_data,
                            )
                            tool_request = extract_tools.extract_tool_request(full)
                            if tool_request is not None:
                                try:
                                    await self.validate_tool_request(tool_request)
                                except Exception:
                                    pass
                                else:
                                    await self.handle_response_stream(stream_data["full"])
                                    response_stream_pending = False
                                    return full.strip()

                            # Stream masked chunk after extensions processed it
                            if stream_data.get("chunk"):
                                printer.stream(stream_data["chunk"])
                            last_response_stream_full = stream_data["full"]
                            response_stream_pending = True
                            now = time.monotonic()
                            if (
                                len(full) - last_response_stream_chars
                                >= _RESPONSE_STREAM_UPDATE_CHARS
                                or now - last_response_stream_at
                                >= _RESPONSE_STREAM_UPDATE_SECONDS
                            ):
                                await self.handle_response_stream(last_response_stream_full)
                                last_response_stream_chars = len(full)
                                last_response_stream_at = time.monotonic()
                                response_stream_pending = False

                        # call main LLM
                        llm_result = await self.call_chat_model_turn(
                            messages=prompt,
                            response_callback=stream_callback,
                            reasoning_callback=reasoning_callback,
                        )
                        agent_response = llm_result.response
                        await self.handle_intervention(agent_response)

                        if response_stream_pending:
                            await self.handle_response_stream(last_response_stream_full)

                        # Notify extensions to finalize their stream filters
                        await extension.call_extensions_async(
                            "reasoning_stream_end", self, loop_data=self.loop_data
                        )
                        await self.handle_intervention(agent_response)

                        await extension.call_extensions_async(
                            "response_stream_end", self, loop_data=self.loop_data
                        )

                        await self.handle_intervention(agent_response)

                        result_data = {"llm_result": llm_result}
                        await extension.call_extensions_async(
                            "message_loop_result",
                            self,
                            loop_data=self.loop_data,
                            result_data=result_data,
                        )
                        if result_data.get("skip_default_processing"):
                            continue

                        agent_response = llm_result.response
                        log_item = self.loop_data.params_temporary.get("log_item_generating")
                        assistant_message = self.hist_add_ai_response(
                            agent_response,
                            id=log_item.id if log_item else "",
                            llm_result=llm_result,
                        )
                        tools_result = await self.process_llm_result_tools(llm_result)
                        if tools_result:  # final response of message loop available
                            return tools_result  # break the execution if the task is done

                    # exceptions inside message loop:
                    except Exception as e:
                        await self.handle_exception("message_loop", e)

                    finally:
                        # call message_loop_end extensions
                        if self.context.task and self.context.task.is_alive(): # don't call extensions post mortem
                            await extension.call_extensions_async(
                                "message_loop_end", self, loop_data=self.loop_data
                            )



            # exceptions outside message loop:
            except Exception as e:
                _turn_status = "error"
                await self.handle_exception("monologue", e)
            finally:
                self.context.streaming_agent = None  # unset current streamer
                # call monologue_end extensions
                if self.context.task and self.context.task.is_alive(): # don't call extensions post mortem
                    await extension.call_extensions_async(
                        "monologue_end", self, loop_data=self.loop_data
                    )  # type: ignore
                try:
                    await extension.call_extensions_async(
                        "turn_metrics", self, metrics=self._turn_metrics
                    )
                except Exception:
                    pass
                turn_journal.append(self._journal_ctx(), "turn_end", status=_turn_status)
                turn_journal.compact(self._journal_ctx())
                self._turn_metrics.end(_turn_status)

    @extension.extensible
    async def prepare_prompt(self, loop_data: LoopData) -> list[BaseMessage]:
        self.context.log.set_progress("Building prompt")
        import os as _os
        _timing = _os.environ.get("A0_PROMPT_TIMING") == "1"
        _t0 = time.perf_counter()
        def _pt(stage):
            if not _timing:
                return
            try:
                with open(__import__("tempfile").gettempdir() + "/a0-prompt-timing.log", "a") as fh:
                    fh.write(f"{time.time():.3f} it={loop_data.iteration} {stage} +{time.perf_counter()-_t0:.3f}s\n")
            except Exception:
                pass

        # call extensions before setting prompts
        await extension.call_extensions_async(
            "message_loop_prompts_before", self, loop_data=loop_data
        )
        _pt("prompts_before done")

        # set system prompt and message history
        responses_history.start_prompt(loop_data)
        cached_system = getattr(self, "_system_prompt_cache", None)
        if cached_system is None:
            cached_system = await self.get_system_prompt(self.loop_data)
            self._system_prompt_cache = list(cached_system)
        loop_data.system = list(cached_system)
        loop_data.history_output = self.history.output()
        _pt("system+history done")

        # and allow extensions to edit them
        await extension.call_extensions_async(
            "message_loop_prompts_after", self, loop_data=loop_data
        )
        _pt("prompts_after done")

        # concatenate system prompt and remove JSON fence markers from examples
        system_text = files.remove_code_fences(
            "\n\n".join(loop_data.system), language="json"
        )

        # join protocol and extras
        protocol = self._build_context_message(
            "agent.context.protocol.md",
            "protocol",
            {**loop_data.protocol_persistent, **loop_data.protocol_temporary},
            include_empty=False,
        )
        extras = self._build_context_message(
            "agent.context.extras.md",
            "extras",
            {**loop_data.extras_persistent, **loop_data.extras_temporary},
            include_empty=True,
        )
        loop_data.protocol_temporary.clear()
        loop_data.extras_temporary.clear()

        # convert protocol + history + extras to LLM format
        history_langchain: list[BaseMessage] = history.output_langchain(
            protocol + loop_data.history_output + extras
        )

        # build full prompt from system prompt, protocol, message history and extras
        full_prompt: list[BaseMessage] = [
            SystemMessage(content=system_text),
            *history_langchain,
        ]
        full_text = ChatPromptTemplate.from_messages(full_prompt).format()
        responses_history.remember_prompt(loop_data, full_text, full_prompt[0], protocol)

        # store as last context window content
        _pt("render done")
        self.set_data(
            Agent.DATA_NAME_CTX_WINDOW,
            {
                "text": full_text,
                "tokens": tokens.approximate_prompt_tokens(full_text),
            },
        )

        return full_prompt

    def _build_context_message(
        self,
        prompt_file: str,
        variable_name: str,
        values: dict[str, history.MessageContent],
        include_empty: bool,
    ) -> list[history.OutputMessage]:
        if not include_empty and not values:
            return []

        return history.Message(  # type: ignore[abstract]
            False,
            content=self.read_prompt(
                prompt_file,
                **{variable_name: dirty_json.stringify(values, separators=(",", ":"))},
            ),
        ).output()

    @extension.extensible
    async def handle_exception(self, location: str, exception: Exception):
        if exception:
            raise exception # exception handling is done by extensions

        # exception_data = {"exception": exception}
        # await self.call_extensions(
        #     "message_loop_exception", exception_data=exception_data
        # )

        # # If extensions cleared the exception, continue.
        # if not exception_data.get("exception"):
        #     return

        # # Backwards-compatible fallback (should normally be handled by _90 extension).
        # exception = exception_data["exception"]
        # if isinstance(exception, HandledException):
        #     raise exception
        # elif isinstance(exception, asyncio.CancelledError):
        #     PrintStyle(font_color="white", background_color="red", padding=True).print(
        #         f"Context {self.context.id} terminated during message loop"
        #     )
        #     raise HandledException(exception)

        # else:
        #     error_text = errors.error_text(exception)
        #     error_message = errors.format_error(exception)

        #     # Mask secrets in error messages
        #     PrintStyle(font_color="red", padding=True).print(error_message)
        #     self.context.log.log(
        #         type="error",
        #         content=error_message,
        #     )
        #     PrintStyle(font_color="red", padding=True).print(
        #         f"{self.agent_name}: {error_text}"
        #     )

        #     raise HandledException(exception)  # Re-raise the exception to kill the loop

    @extension.extensible
    async def get_system_prompt(self, loop_data: LoopData) -> list[str]:
        system_prompt: list[str] = []
        await extension.call_extensions_async(
            "system_prompt", self, system_prompt=system_prompt, loop_data=loop_data
        )
        return system_prompt

    @extension.extensible
    def parse_prompt(self, _prompt_file: str, **kwargs):
        dirs = subagents.get_paths(self, "prompts")

        prompt = files.parse_file(
            _prompt_file, _directories=dirs, _agent=self, **kwargs
        )
        if isinstance(prompt, str):
            prompt = prompt.rstrip("\n")
        return prompt

    @extension.extensible
    def read_prompt(self, file: str, **kwargs) -> str:
        dirs = subagents.get_paths(self, "prompts")

        prompt = files.read_prompt_file(file, _directories=dirs, _agent=self, **kwargs)
        if files.is_full_json_template(prompt):
            prompt = files.remove_code_fences(prompt)
        if isinstance(prompt, str):
            prompt = prompt.rstrip("\n")
        return prompt

    def get_data(self, field: str):
        return self.data.get(field, None)

    def set_data(self, field: str, value):
        self.data[field] = value

    @extension.extensible
    def hist_add_message(
        self,
        ai: bool,
        content: history.MessageContent,
        tokens: int = 0,
        id: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        self.context.last_message = Localization.get().now()
        # Allow extensions to process content before adding to history
        content_data = {"content": content}
        extension.call_extensions_sync(
            "hist_add_before", self, content_data=content_data, ai=ai
        )
        return self.history.add_message(
            ai=ai,
            content=content_data["content"],
            tokens=tokens,
            id=id,
            metadata=metadata,
        )

    @extension.extensible
    def hist_add_user_message(self, message: UserMessage, intervention: bool = False):
        self.history.new_topic()  # user message starts a new topic in history

        # load message template based on intervention
        if intervention:
            content = self.parse_prompt(
                "fw.intervention.md",
                message=message.message,
                attachments=message.attachments,
                system_message=message.system_message,
            )
        else:
            content = self.parse_prompt(
                "fw.user_message.md",
                message=message.message,
                attachments=message.attachments,
                system_message=message.system_message,
            )

        # remove empty parts from template
        if isinstance(content, dict):
            content = {k: v for k, v in content.items() if v}

        # add to history
        msg = self.hist_add_message(False, content=content, id=message.id)  # type: ignore
        self.last_user_message = msg
        return msg

    @extension.extensible
    def hist_add_ai_response(
        self, message: str, llm_result: LLMResult | str | None = None, id: str = ""
    ):
        if isinstance(llm_result, str):
            if id:
                raise TypeError("History message ID supplied twice")
            id, llm_result = llm_result, None
        if llm_result is None:
            llm_result = LLMResult.non_llm()
        self.loop_data.last_response = (
            llm_result.repeat_response_text() if llm_result.function_calls else message
        )
        message = llm_result.function_calls_text() or message
        content = self.parse_prompt("fw.ai_response.md", message=message)
        msg = self.hist_add_message(
            True,
            content=content,
            id=id,
            metadata=metadata_from_llm_result(llm_result),
        )
        self._remember_llm_result_state(llm_result, msg)
        return msg

    @extension.extensible
    def hist_add_warning(self, message: history.MessageContent, id: str = ""):
        content = self.parse_prompt("fw.warning.md", message=message)
        return self.hist_add_message(False, content=content, id=id)

    @extension.extensible
    def hist_add_tool_result(self, tool_name: str, tool_result: str, **kwargs):
        msg_id = kwargs.pop("id", "")
        responses_item = kwargs.pop("_responses_output_item", None) or kwargs.pop(
            "responses_item", None
        )
        metadata = (
            {
                RESPONSE_METADATA_KEY: {
                    "input_items": [responses_item],
                    "output_items": [],
                    "mode": "responses",
                    "state": "provider",
                }
            }
            if isinstance(responses_item, dict)
            else None
        )
        data = {
            "tool_name": tool_name,
            "tool_result": tool_result,
            **kwargs,
        }
        extension.call_extensions_sync("hist_add_tool_result", self, data=data)
        return self.hist_add_message(False, content=data, id=msg_id, metadata=metadata)

    def concat_messages(
        self, messages
    ):  # TODO add param for message range, topic, history
        return self.history.output_text(human_label="user", ai_label="assistant")

    @extension.extensible
    def get_chat_model(self):
        return None

    @extension.extensible
    def get_utility_model(self):
        return None

    @extension.extensible
    def get_embedding_model(self):
        return None

    @extension.extensible
    async def call_utility_model(
        self,
        system: str,
        message: str,
        callback: Callable[[str], Awaitable[None]] | None = None,
        background: bool = False,
    ):
        model = self.get_utility_model()

        # call extensions
        call_data = {
            "model": model,
            "system": system,
            "message": message,
            "callback": callback,
            "background": background,
        }
        await extension.call_extensions_async(
            "util_model_call_before", self, call_data=call_data
        )

        # propagate stream to callback if set
        async def stream_callback(chunk: str, total: str):
            if call_data["callback"]:
                await call_data["callback"](chunk)

        response, _reasoning = await call_data["model"].unified_call(
            system_message=call_data["system"],
            user_message=call_data["message"],
            response_callback=stream_callback if call_data["callback"] else None,
            rate_limiter_callback=(
                self.rate_limiter_callback if not call_data["background"] else None
            ),
        )

        await extension.call_extensions_async(
            "util_model_call_after", self, call_data=call_data, response=response
        )

        return response

    @extension.extensible
    async def call_chat_model(
        self,
        messages: list[BaseMessage],
        response_callback: Callable[[str, str], Awaitable[str | None]] | None = None,
        reasoning_callback: Callable[[str, str], Awaitable[None]] | None = None,
        background: bool = False,
        explicit_caching: bool = True,
    ):
        response = ""

        # model class
        model = self.get_chat_model()

        # call extensions before
        call_data = {
            "model": model,
            "messages": messages,
            "response_callback": response_callback,
            "reasoning_callback": reasoning_callback,
            "background": background,
            "explicit_caching": explicit_caching,
        }
        await extension.call_extensions_async(
            "chat_model_call_before", self, call_data=call_data
        )

        # call model
        response, reasoning = await call_data["model"].unified_call(
            messages=call_data["messages"],
            reasoning_callback=call_data["reasoning_callback"],
            response_callback=call_data["response_callback"],
            rate_limiter_callback=(
                self.rate_limiter_callback if not call_data["background"] else None
            ),
            explicit_caching=call_data["explicit_caching"],
        )

        await extension.call_extensions_async(
            "chat_model_call_after", self, call_data=call_data, response=response, reasoning=reasoning
        )

        return response, reasoning

    @extension.extensible
    async def call_chat_model_turn(
        self,
        messages: list[BaseMessage],
        response_callback: Callable[[str, str], Awaitable[str | None]] | None = None,
        reasoning_callback: Callable[[str, str], Awaitable[None]] | None = None,
        background: bool = False,
        explicit_caching: bool = True,
    ) -> LLMResult:
        metrics = getattr(self, "_turn_metrics", None)
        started = time.perf_counter()
        turn_journal.append(self._journal_ctx(), "model_start")

        from plugins._model_config.helpers import model_config as _mc

        fallbacks = _mc.get_chat_fallback_configs(self)
        got_chunk = False

        async def _resp_wrapped(*args):
            nonlocal got_chunk
            got_chunk = True
            if response_callback:
                return await response_callback(*args)
            return None

        async def _reason_wrapped(*args):
            nonlocal got_chunk
            got_chunk = True
            if reasoning_callback:
                await reasoning_callback(*args)

        candidates: list[object | None] = [None]
        for cfg in fallbacks:
            try:
                candidates.append(_mc.build_chat_model_from_config(cfg))
            except Exception:
                continue

        for index, candidate in enumerate(candidates):
            try:
                result = await self._call_chat_model_turn(
                    messages,
                    response_callback=_resp_wrapped if response_callback else None,
                    reasoning_callback=_reason_wrapped if reasoning_callback else None,
                    background=background,
                    explicit_caching=explicit_caching,
                    model_override=candidate,
                )
            except Exception as exc:
                can_retry = (
                    not got_chunk
                    and index + 1 < len(candidates)
                    and _is_fallback_error(exc)
                )
                if not can_retry:
                    turn_journal.append(
                        self._journal_ctx(),
                        "model_end",
                        error=True,
                        duration_s=round(time.perf_counter() - started, 4),
                    )
                    if metrics:
                        metrics.api(
                            time.perf_counter() - started,
                            error=True,
                            iteration=getattr(getattr(self, "loop_data", None), "iteration", None),
                        )
                    raise
                turn_journal.append(
                    self._journal_ctx(),
                    "model_fallback",
                    index=index + 1,
                    error=type(exc).__name__,
                )
                continue

            turn_journal.append(
                self._journal_ctx(),
                "model_end",
                duration_s=round(time.perf_counter() - started, 4),
            )
            if metrics:
                metrics.api(
                    time.perf_counter() - started,
                    iteration=getattr(getattr(self, "loop_data", None), "iteration", None),
                )
            return result

        raise RuntimeError("no model candidate produced a response")

    async def _call_chat_model_turn(
        self,
        messages: list[BaseMessage],
        response_callback: Callable[[str, str], Awaitable[str | None]] | None = None,
        reasoning_callback: Callable[[str, str], Awaitable[None]] | None = None,
        background: bool = False,
        explicit_caching: bool = True,
        model_override: object | None = None,
    ) -> LLMResult:
        model = model_override or self.get_chat_model()
        model_kwargs = getattr(model, "kwargs", {}) if model else {}
        if isinstance(model_kwargs, dict) and model_kwargs.get("responses_delete_on_chat_delete") is False:
            self.set_data("responses_delete_on_chat_delete", False)
        # native function tools are only sent through the Responses API
        api_mode = model_kwargs.get("a0_api_mode") if isinstance(model_kwargs, dict) else None
        response_tools, name_map = (
            build_responses_function_tools(self)
            if TransportMode.from_value(api_mode) is TransportMode.RESPONSES
            else ([], {})
        )
        self.set_data(Agent.DATA_NAME_RESPONSES_TOOL_NAME_MAP, name_map)

        call_data = {
            "model": model,
            "messages": messages,
            "response_callback": response_callback,
            "reasoning_callback": reasoning_callback,
            "background": background,
            "explicit_caching": explicit_caching,
            "a0_responses_function_tools": response_tools,
        }

        previous_state = self._responses_state_for_model(model)
        if previous_state:
            history_counter = int(previous_state.get("history_counter", 0) or 0)
            call_data["previous_response_id"] = previous_state.get("response_id", "")
            call_data["responses_input_items"] = self._responses_input_items_since(
                model,
                history_counter,
            )
        call_data["responses_local_input_items"] = ResponsesTransport.input_from_model_messages(
            model,
            messages,
        )

        await extension.call_extensions_async(
            "chat_model_call_before", self, call_data=call_data
        )

        turn_kwargs = {
            **responses_history.prepare_call(self, call_data),
            "a0_responses_function_tools": call_data.get(
                "a0_responses_function_tools"
            ),
            "responses_local_input_items": call_data.get(
                "responses_local_input_items"
            ),
        }
        for key in (
            "responses_builtin_tools",
            "responses_state",
            "previous_response_id",
            "responses_input_items",
        ):
            if call_data.get(key) is not None:
                turn_kwargs[key] = call_data.get(key)

        llm_result = await call_data["model"].unified_turn(
            messages=call_data["messages"],
            reasoning_callback=call_data["reasoning_callback"],
            response_callback=call_data["response_callback"],
            rate_limiter_callback=(
                self.rate_limiter_callback if not call_data["background"] else None
            ),
            explicit_caching=call_data["explicit_caching"],
            **turn_kwargs,
        )

        downgraded = llm_result.capability.get("builtin_tool_downgrades")
        if downgraded:
            self.context.log.log(
                type="info",
                heading="Responses capability downgrade",
                content=(
                    "Provider rejected Responses built-in tool(s); omitted: "
                    + ", ".join(str(item) for item in downgraded)
                ),
            )

        await extension.call_extensions_async(
            "chat_model_call_after",
            self,
            call_data=call_data,
            response=llm_result.response,
            reasoning=llm_result.reasoning,
        )

        return llm_result

    def _responses_state_for_model(self, model: Any) -> dict[str, Any]:
        state = self.get_data(Agent.DATA_NAME_RESPONSES_STATE)
        if not isinstance(state, dict):
            return {}
        provider_model_key = str(getattr(model, "model_name", "") or "")
        if state.get("provider_model_key") != provider_model_key:
            return {}
        if not state.get("response_id"):
            return {}
        return state

    def _responses_input_items_since(
        self, model: Any, sequence: int
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in self.history.messages_since(sequence):
            items.extend(self._responses_input_items_for_message(model, message))
        return items

    def _responses_input_items_for_message(
        self, model: Any, message: history.Message
    ) -> list[dict[str, Any]]:
        result = result_from_metadata(message.metadata)
        if result:
            if message.ai and result.output_items:
                return [item.to_dict() for item in result.output_items]
            if not message.ai and result.input_items:
                return [dict(item) for item in result.input_items]

        output = message.output()
        langchain_messages = history.output_langchain(output)
        return ResponsesTransport.input_from_model_messages(model, langchain_messages)

    def _remember_llm_result_state(
        self, llm_result: LLMResult, history_message: history.Message
    ) -> None:
        if not llm_result.response_id:
            return
        current = self.get_data(Agent.DATA_NAME_RESPONSES_STATE)
        response_ids = []
        if isinstance(current, dict) and isinstance(current.get("response_ids"), list):
            response_ids = [str(item) for item in current["response_ids"] if item]
        if llm_result.response_id not in response_ids:
            response_ids.append(llm_result.response_id)
        self.set_data(
            Agent.DATA_NAME_RESPONSES_STATE,
            {
                "response_id": llm_result.response_id,
                "previous_response_id": llm_result.previous_response_id,
                "provider_model_key": llm_result.provider_model_key,
                "history_counter": history_message.sequence,
                "response_ids": response_ids,
            },
        )

    @extension.extensible
    async def rate_limiter_callback(
        self, message: str, key: str, total: int, limit: int
    ):
        # show the rate limit waiting in a progress bar, no need to spam the chat history
        self.context.log.set_progress(message, True)
        return False

    @extension.extensible
    async def handle_intervention(self, progress: str = ""):
        await self.wait_if_paused()
        if (
            self.intervention
        ):  # if there is an intervention message, but not yet processed
            msg = self.intervention
            self.intervention = None  # reset the intervention message
            # If a tool was running, save its progress to history
            last_tool = self.loop_data.current_tool
            if last_tool:
                tool_progress = last_tool.progress.strip()
                if tool_progress:
                    self.hist_add_tool_result(last_tool.name, tool_progress)
                    last_tool.set_progress(None)
            if progress.strip():
                self.hist_add_ai_response(progress, llm_result=LLMResult.non_llm())
            # append the intervention message
            self.hist_add_user_message(msg, intervention=True)
            raise InterventionException(msg)

    async def wait_if_paused(self):
        while self.context.paused:
            await asyncio.sleep(0.1)

    async def process_llm_result_tools(self, llm_result: LLMResult):
        await self._log_response_builtin_items(llm_result)
        if llm_result.function_calls:
            import os as _os

            from helpers import tool_runtime

            calls = []
            for function_call in llm_result.function_calls:
                name_map = self.get_data(Agent.DATA_NAME_RESPONSES_TOOL_NAME_MAP)
                tool_name = original_tool_name(function_call.name, name_map)
                response_item_factory = lambda response, call=function_call: function_call_output_item(
                    call.call_id,
                    response.message,
                )
                calls.append(
                    {
                        "tool_name": tool_name,
                        "tool_args": function_call.arguments,
                        "message": llm_result.response,
                        "raw_tool_name": tool_name,
                        "responses_item_factory": response_item_factory,
                    }
                )

            parallel_env = _os.environ.get("A0_PARALLEL_TOOLS")
            if parallel_env is not None:
                parallel_enabled = parallel_env != "0"
            else:
                from helpers import settings as _settings

                parallel_enabled = bool(
                    _settings.get_settings().get("agent_parallel_tools", True)
                )
            resolved: list[Any] | None = None
            sequential_tools = {
                item.strip()
                for item in _os.environ.get(
                    "A0_SEQUENTIAL_TOOLS", "response,scheduler,code_execution_tool"
                ).split(",")
                if item.strip()
            }
            if parallel_enabled and len(calls) > 1 and not any(
                call["tool_name"] in sequential_tools for call in calls
            ):
                resolved = [
                    self._resolve_tool_for_call(
                        call["tool_name"], call["tool_args"], call["message"]
                    )
                    for call in calls
                ]
                if all(
                    tool is not None
                    and tool_runtime.can_run_concurrently(
                        tool, **call["tool_args"]
                    )
                    for call, tool in zip(calls, resolved)
                ):
                    return await self._execute_tool_request_batch(
                        calls, prepared_tools=resolved
                    )

            for index, call in enumerate(calls):
                result = await self._execute_tool_request(
                    **call,
                    prepared_tool=(
                        resolved[index]
                        if resolved is not None and index < len(resolved)
                        else None
                    ),
                )
                if result:
                    return result
            return None
        if llm_result.builtin_items and not llm_result.response:
            return None
        message = llm_result.response
        if not message and llm_result.reasoning:
            if (
                extract_tools.extract_tool_request(llm_result.reasoning) is not None
                or extract_tools.is_misformatted_tool_request(llm_result.reasoning)
            ):
                message = llm_result.reasoning
        if (
            llm_result.mode == "responses"
            and isinstance(message, str)
            and bool(message.strip())
            and extract_tools.extract_tool_request(message) is None
            and not extract_tools.is_misformatted_tool_request(message)
        ):
            return await self._execute_tool_request(
                tool_name="response",
                tool_args={"text": message},
                message=message,
            )
        return await self.process_tools(message)

    def _begin_tool_span(self, tool_name: str):
        metrics = getattr(self, "_turn_metrics", None)
        started = time.perf_counter()
        turn_journal.append(self._journal_ctx(), "tool_start", tool=tool_name)
        return started, metrics

    def _end_tool_span(self, tool_name: str, started: float, metrics, *, error: bool = False):
        duration = round(time.perf_counter() - started, 4)
        turn_journal.append(
            self._journal_ctx(),
            "tool_end",
            tool=tool_name,
            error=error,
            duration_s=duration,
        )
        if metrics:
            metrics.tool(
                tool_name,
                duration,
                error=error,
                iteration=getattr(getattr(self, "loop_data", None), "iteration", None),
            )

    async def _execute_tool_request(
        self,
        tool_name: str,
        tool_args: dict,
        message: str,
        raw_tool_name: str = "",
        responses_item_factory: Callable[[Any], dict[str, Any]] | None = None,
        prepared_tool: Any = None,
    ):
        started, metrics = self._begin_tool_span(tool_name)
        try:
            result = await self._execute_tool_request_impl(
                tool_name=tool_name,
                tool_args=tool_args,
                message=message,
                raw_tool_name=raw_tool_name,
                responses_item_factory=responses_item_factory,
                prepared_tool=prepared_tool,
            )
        except Exception:
            self._end_tool_span(tool_name, started, metrics, error=True)
            raise
        self._end_tool_span(tool_name, started, metrics)
        return result

    def _resolve_tool_for_call(
        self,
        tool_name: str,
        tool_args: dict,
        message: str,
    ):
        tool_method = None
        tool = None
        try:
            import helpers.mcp_handler as mcp_helper

            tool = mcp_helper.MCPConfig.get_instance().get_tool(self, tool_name)
        except ImportError:
            PrintStyle(
                background_color="black", font_color="yellow", padding=True
            ).print("MCP helper module not found. Skipping MCP tool lookup.")
        except Exception as e:
            PrintStyle(background_color="black", font_color="red", padding=True).print(
                f"Failed to get MCP tool '{tool_name}': {e}"
            )
        if not tool:
            tool = self.get_tool(
                name=tool_name,
                method=tool_method,
                args=tool_args,
                message=message,
                loop_data=self.loop_data,
            )
        return tool

    async def _run_tool_before(self, tool, tool_name: str, tool_args: dict) -> None:
        self.loop_data.current_tool = tool  # type: ignore
        await self.handle_intervention()
        await tool.before_execution(**tool_args)
        await self.handle_intervention()
        await extension.call_extensions_async(
            "tool_execute_before",
            self,
            tool_args=tool_args or {},
            tool_name=tool_name,
        )

    async def _run_tool_after(
        self,
        tool,
        tool_name: str,
        response,
        responses_item_factory: Callable[[Any], dict[str, Any]] | None = None,
    ) -> None:
        self.loop_data.current_tool = tool  # type: ignore
        try:
            await self.handle_intervention()
            await extension.call_extensions_async(
                "tool_execute_after",
                self,
                response=response,
                tool_name=tool_name,
            )
            if responses_item_factory:
                response.additional = {
                    **(response.additional or {}),
                    "_responses_output_item": responses_item_factory(response),
                }
            await tool.after_execution(response)
            await self.handle_intervention()
        finally:
            self.loop_data.current_tool = None

    async def _execute_tool_request_impl(
        self,
        tool_name: str,
        tool_args: dict,
        message: str,
        raw_tool_name: str = "",
        responses_item_factory: Callable[[Any], dict[str, Any]] | None = None,
        prepared_tool: Any = None,
    ):
        raw_tool_name = raw_tool_name or tool_name
        tool = prepared_tool or self._resolve_tool_for_call(tool_name, tool_args, message)
        if not tool:
            error_detail = (
                f"Tool '{raw_tool_name}' not found or could not be initialized."
            )
            wmsg = self.hist_add_warning(error_detail)
            PrintStyle(font_color="red", padding=True).print(error_detail)
            self.context.log.log(
                type="warning",
                content=f"{self.agent_name}: {error_detail}",
                id=wmsg.id,
            )
            return None

        try:
            await self._run_tool_before(tool, tool_name, tool_args)
            response = await tool.execute(**tool_args)
            await self._run_tool_after(tool, tool_name, response, responses_item_factory)
        finally:
            self.loop_data.current_tool = None
        if response.break_loop:
            self._clear_responses_pending_state()
            return response.message
        return None

    async def _execute_tool_request_batch(
        self,
        calls: list[dict[str, Any]],
        *,
        prepared_tools: list[Any] | None = None,
    ):
        """Run native function calls concurrently, commit results in model order."""
        tools = prepared_tools or [
            self._resolve_tool_for_call(
                call["tool_name"], call["tool_args"], call["message"]
            )
            for call in calls
        ]
        if any(tool is None for tool in tools):
            # Preserve the sequential warning/unknown-tool behavior.
            for fallback in calls:
                result = await self._execute_tool_request(**fallback)
                if result:
                    return result
            return None
        prepared = list(zip(calls, tools))

        for call, tool in prepared:
            try:
                await self._run_tool_before(tool, call["tool_name"], call["tool_args"])
            finally:
                self.loop_data.current_tool = None

        try:
            from helpers import settings as _settings

            limit = int(
                __import__("os").environ.get("A0_MAX_PARALLEL_TOOLS")
                or _settings.get_settings().get("agent_max_parallel_tools", 0)
                or 0
            )
        except Exception:
            limit = 0
        semaphore = asyncio.Semaphore(limit) if limit > 0 else None

        async def run(call: dict[str, Any], tool):
            started, metrics = self._begin_tool_span(call["tool_name"])
            self.loop_data.current_tool = tool  # task-local ContextVar
            try:
                if semaphore is None:
                    response = await tool.execute(**call["tool_args"])
                else:
                    async with semaphore:
                        response = await tool.execute(**call["tool_args"])
            except asyncio.CancelledError:
                self._end_tool_span(call["tool_name"], started, metrics, error=True)
                raise
            except Exception:
                self._end_tool_span(call["tool_name"], started, metrics, error=True)
                raise
            finally:
                self.loop_data.current_tool = None
            self._end_tool_span(call["tool_name"], started, metrics)
            return response

        tasks = [
            asyncio.create_task(run(call, tool)) for call, tool in prepared
        ]
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_EXCEPTION
        )
        intervention = next(
            (
                task.exception()
                for task in done
                if not task.cancelled()
                and isinstance(task.exception(), InterventionException)
            ),
            None,
        )
        if intervention is not None:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.loop_data.current_tool = None
            raise intervention

        results = await asyncio.gather(*tasks, return_exceptions=True)

        late_intervention = next(
            (
                result
                for result in results
                if isinstance(result, InterventionException)
            ),
            None,
        )
        if late_intervention is not None:
            self.loop_data.current_tool = None
            raise late_intervention

        first_error: BaseException | None = None
        final: str | None = None
        for (call, tool), response in zip(prepared, results):
            if isinstance(response, BaseException):
                if first_error is None:
                    first_error = response
                continue
            await self._run_tool_after(
                tool,
                call["tool_name"],
                response,
                call.get("responses_item_factory"),
            )
            if response.break_loop:
                self._clear_responses_pending_state()
                if final is None:
                    final = response.message

        if final is not None:
            return final
        if first_error is not None:
            raise first_error
        return None

    async def _log_response_builtin_items(self, llm_result: LLMResult) -> None:
        for item in llm_result.builtin_items:
            if item.type == "computer_call":
                await self._handle_responses_computer_call(item.data)
                continue
            if item.type == "mcp_approval_request":
                self._handle_responses_mcp_approval_request(item.data)
                continue
            self.context.log.log(
                type="info",
                heading=f"Responses tool item: {item.type}",
                content=json.dumps(item.data, ensure_ascii=False, default=str),
            )

    async def _handle_responses_computer_call(self, item: dict[str, Any]) -> None:
        safety_checks = item.get("pending_safety_checks") or item.get("safety_checks")
        if safety_checks:
            message = (
                "Responses computer_call requested safety-check acknowledgement. "
                "Agent Zero requires explicit user acknowledgement before executing it."
            )
            output_item = {
                "type": "computer_call_output",
                "call_id": str(item.get("call_id") or item.get("id") or ""),
                "output": {"type": "input_text", "text": message},
            }
            self.hist_add_tool_result(
                "computer_call",
                message,
                responses_item=output_item,
            )
            self.context.log.log(type="warning", content=message)
            return

        args = self._computer_call_args(item)
        if not args:
            message = "Responses computer_call action is unsupported by Agent Zero."
            output_item = {
                "type": "computer_call_output",
                "call_id": str(item.get("call_id") or item.get("id") or ""),
                "output": {"type": "input_text", "text": message},
            }
            self.hist_add_tool_result(
                "computer_call",
                message,
                responses_item=output_item,
            )
            self.context.log.log(type="warning", content=message)
            return

        if args.get("action") != "start_session" and not args.get("session_id"):
            session_id = str(
                self.get_data(Agent.DATA_NAME_RESPONSES_COMPUTER_SESSION) or ""
            )
            if session_id:
                args["session_id"] = session_id

        response_item_factory = lambda response: self._computer_call_output_item(
            item,
            response,
        )
        result = await self._execute_tool_request(
            tool_name="computer_use_remote",
            tool_args=args,
            message=json.dumps(item, ensure_ascii=False, default=str),
            raw_tool_name="computer_call",
            responses_item_factory=response_item_factory,
        )
        _ = result

    def _handle_responses_mcp_approval_request(self, item: dict[str, Any]) -> None:
        request_id = str(
            item.get("approval_request_id") or item.get("id") or item.get("call_id") or ""
        )
        message = (
            "Responses MCP approval request received. Agent Zero denied it because "
            "provider-hosted MCP approval requires explicit user approval."
        )
        output_item = {
            "type": "mcp_approval_response",
            "approval_request_id": request_id,
            "approve": False,
        }
        self.hist_add_tool_result(
            "mcp_approval_request",
            message,
            responses_item=output_item,
        )
        self.context.log.log(
            type="warning",
            heading="Responses MCP approval required",
            content=message,
        )

    def _computer_call_args(self, item: dict[str, Any]) -> dict[str, Any]:
        action = item.get("action")
        action_data = dict(action) if isinstance(action, dict) else {}
        action_type = str(
            action_data.get("type")
            or action_data.get("action")
            or item.get("action_type")
            or ""
        ).strip().lower()
        args: dict[str, Any] = {}

        if action_type in {"screenshot", "capture"}:
            args["action"] = "capture"
        elif action_type in {"move", "mousemove"}:
            args.update({"action": "move", "x": action_data.get("x"), "y": action_data.get("y")})
        elif action_type in {"click", "double_click"}:
            args.update(
                {
                    "action": "click",
                    "x": action_data.get("x"),
                    "y": action_data.get("y"),
                    "button": action_data.get("button", "left"),
                    "count": 2 if action_type == "double_click" else action_data.get("count", 1),
                }
            )
        elif action_type == "scroll":
            args.update(
                {
                    "action": "scroll",
                    "dx": action_data.get("dx", action_data.get("scroll_x", 0)),
                    "dy": action_data.get("dy", action_data.get("scroll_y", 0)),
                }
            )
        elif action_type in {"keypress", "key"}:
            args.update(
                {
                    "action": "key",
                    "keys": action_data.get("keys") or action_data.get("key"),
                }
            )
        elif action_type in {"type", "input_text"}:
            args.update({"action": "type", "text": action_data.get("text", "")})
        else:
            return {}

        session_id = item.get("session_id") or action_data.get("session_id")
        if session_id:
            args["session_id"] = session_id
        return args

    def _computer_call_output_item(
        self, source_item: dict[str, Any], response: Any
    ) -> dict[str, Any]:
        output: dict[str, Any] = {
            "type": "input_text",
            "text": str(getattr(response, "message", "") or ""),
        }
        additional = getattr(response, "additional", None)
        raw_content = additional.get("raw_content") if isinstance(additional, dict) else None
        if isinstance(raw_content, list):
            for content in raw_content:
                if not isinstance(content, dict):
                    continue
                if content.get("type") != "image_url":
                    continue
                image_url = content.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if url:
                    output = {"type": "input_image", "image_url": url}
                    break

        session_id_match = re_search_session_id(str(getattr(response, "message", "") or ""))
        if session_id_match:
            self.set_data(Agent.DATA_NAME_RESPONSES_COMPUTER_SESSION, session_id_match)

        return {
            "type": "computer_call_output",
            "call_id": str(source_item.get("call_id") or source_item.get("id") or ""),
            "output": output,
        }

    def _clear_responses_pending_state(self) -> None:
        state = self.get_data(Agent.DATA_NAME_RESPONSES_STATE)
        if isinstance(state, dict):
            state = dict(state)
            state.pop("response_id", None)
            state.pop("previous_response_id", None)
            self.set_data(Agent.DATA_NAME_RESPONSES_STATE, state)

    @extension.extensible
    async def process_tools(self, msg: str):
        # search for tool usage requests in agent message
        tool_request = extract_tools.extract_tool_request(msg)

        raw_tool_name = ""
        tool_args = {}

        # Only validate when extraction produced an object; None means no JSON tool
        # block was found - the misformat warning path below handles that.
        if tool_request is not None:
            try:
                await self.validate_tool_request(tool_request)
                raw_tool_name, tool_args = extract_tools.normalize_tool_request(
                    tool_request
                )
            except ValueError:
                tool_request = None  # treat structural validation errors as misformat

        if tool_request is not None:
            tool_name = raw_tool_name  # Initialize tool_name with raw_tool_name
            tool_method = None  # Initialize tool_method

            tool = None  # Initialize tool to None

            # Try getting tool from MCP first
            try:
                import helpers.mcp_handler as mcp_helper

                mcp_tool_candidate = mcp_helper.MCPConfig.get_instance().get_tool(
                    self, tool_name
                )
                if mcp_tool_candidate:
                    tool = mcp_tool_candidate
            except ImportError:
                PrintStyle(
                    background_color="black", font_color="yellow", padding=True
                ).print("MCP helper module not found. Skipping MCP tool lookup.")
            except Exception as e:
                PrintStyle(
                    background_color="black", font_color="red", padding=True
                ).print(f"Failed to get MCP tool '{tool_name}': {e}")

            # Fallback to local get_tool if MCP tool was not found or MCP lookup failed
            if not tool:
                tool = self.get_tool(
                    name=tool_name,
                    method=tool_method,
                    args=tool_args,
                    message=msg,
                    loop_data=self.loop_data,
                )

            if tool:
                tool.args = tool_args
                self.loop_data.current_tool = tool  # type: ignore
                try:
                    await self.handle_intervention()

                    # Call tool hooks for compatibility
                    await tool.before_execution(**tool_args)
                    await self.handle_intervention()

                    # Allow extensions to preprocess tool arguments
                    await extension.call_extensions_async(
                        "tool_execute_before",
                        self,
                        tool_args=tool_args or {},
                        tool_name=tool_name,
                    )

                    response = await tool.execute(**tool_args)
                    await self.handle_intervention()

                    # Allow extensions to postprocess tool response
                    await extension.call_extensions_async(
                        "tool_execute_after",
                        self,
                        response=response,
                        tool_name=tool_name,
                    )

                    await tool.after_execution(response)
                    await self.handle_intervention()

                    if response.break_loop:
                        return response.message
                finally:
                    self.loop_data.current_tool = None
            else:
                error_detail = (
                    f"Tool '{raw_tool_name}' not found or could not be initialized."
                )
                wmsg = self.hist_add_warning(error_detail)
                PrintStyle(font_color="red", padding=True).print(error_detail)
                self.context.log.log(
                    type="warning", content=f"{self.agent_name}: {error_detail}", id=wmsg.id
                )
        else:
            warning_msg_misformat = self.read_prompt("fw.msg_misformat.md")
            wmsg = self.hist_add_warning(warning_msg_misformat)
            PrintStyle(font_color="red", padding=True).print(warning_msg_misformat)
            self.context.log.log(
                type="warning",
                content=f"{self.agent_name}: Message misformat, no valid tool request found.",
                id=wmsg.id,
            )

    @extension.extensible
    async def validate_tool_request(self, tool_request: Any):
        extract_tools.normalize_tool_request(tool_request)



    async def handle_reasoning_stream(self, stream: str):
        await self.handle_intervention()
        await extension.call_extensions_async(
            "reasoning_stream",
            self,
            loop_data=self.loop_data,
            text=stream,
        )

    async def handle_response_stream(self, stream: str):
        await self.handle_intervention()
        try:
            if len(stream) < 25:
                return  # no reason to try
            response = DirtyJson.parse_string(stream)
            if isinstance(response, dict):
                try:
                    tool_name, tool_args = extract_tools.normalize_tool_request(response)
                    response.update(tool_name=tool_name, tool_args=tool_args)
                except ValueError:
                    pass  # Tool fields may still be incomplete.
                await extension.call_extensions_async(
                    "response_stream",
                    self,
                    loop_data=self.loop_data,
                    text=stream,
                    parsed=response,
                )

        except Exception as e:
            pass

    @extension.extensible
    def get_tool(
        self,
        name: str,
        method: str | None,
        args: dict,
        message: str,
        loop_data: LoopData | None,
        **kwargs,
    ):
        from tools.unknown import Unknown
        from helpers.tool import Tool

        classes = []

        # search for tools in agent's folder hierarchy
        paths = subagents.get_paths(self, "tools", name + ".py")

        for path in paths:
            try:
                classes = extract_tools.load_classes_from_file(path, Tool)  # type: ignore[arg-type]
                break
            except Exception:
                continue

        tool_class = classes[0] if classes else Unknown
        return tool_class(
            agent=self,
            name=name,
            method=method,
            args=args,
            message=message,
            loop_data=loop_data,
            **kwargs,
        )


def re_search_session_id(text: str) -> str:
    match = re.search(r"session_id=([A-Za-z0-9_.:-]+)", text or "")
    return match.group(1) if match else ""
