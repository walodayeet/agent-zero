import initialize
from helpers import dotenv, extension, runtime
from helpers.api import csrf_protect, requires_auth
from helpers.print_style import PrintStyle
from helpers.server_startup import run_uvicorn_with_retries
from helpers.ui_server import UiServerRuntime, configure_process_environment


def run():
    configure_process_environment()
    PrintStyle().print("Initializing Python framework...")
    PrintStyle().print("Checking for data migration...")
    run_migration_checks()

    PrintStyle().print("Preparing web server runtime...")
    server_runtime, host, port = prepare_web_runtime()

    PrintStyle().print("Initializing Agent Zero components...")
    finish_init = init_a0()
    if callable(finish_init):
        finish_init()

    PrintStyle().print("Starting UI/API server...")
    start_web_server(server_runtime, host, port)


def run_migration_checks() -> None:
    initialize.initialize_migration()


def prepare_web_runtime() -> tuple[UiServerRuntime, str, int]:
    host = (
        runtime.get_arg("host") or dotenv.get_dotenv_value("WEB_UI_HOST") or "localhost"
    )
    port = runtime.get_web_ui_port()
    server_runtime = UiServerRuntime.create()
    server_runtime.register_http_routes()
    server_runtime.register_transport_handlers()

    return server_runtime, host, port


def start_web_server(server_runtime: UiServerRuntime, host: str, port: int) -> None:
    run_uvicorn_with_retries(
        host=host,
        port=port,
        build_asgi_app=server_runtime.build_asgi_app,
        flush_callback=create_flush_callback(),
        access_log=server_runtime.access_log_enabled(),
        ws="wsproto",
    )


def create_flush_callback():
    def flush_and_shutdown_callback() -> None:
        """
        TODO(dev): add cleanup + flush-to-disk logic here.
        """
        return

    flush_ran = False

    def _run_flush(reason: str) -> None:
        nonlocal flush_ran
        if flush_ran:
            return
        flush_ran = True
        try:
            flush_and_shutdown_callback()
        except Exception as e:
            PrintStyle.warning(f"Shutdown flush failed ({reason}): {e}")

    return _run_flush


@extension.extensible
def init_a0():
    init_preload = initialize.initialize_preload()
    init_chats = initialize.initialize_chats()
    init_chats.result_sync()

    def finish_init():
        import time as _t
        _st = _t.perf_counter()
        def _mark(label):
            PrintStyle().print(f"[startup] {label} +{_t.perf_counter()-_st:.2f}s")
        # Warm the embedding model without blocking HTTP longer than configured:
        # bare metal serves immediately, Docker keeps its 10s budget.
        wait_seconds = runtime.get_preload_wait_seconds()
        if init_preload and wait_seconds > 0:
            try:
                init_preload.result_sync(timeout=wait_seconds)
            except TimeoutError:
                pass
            except Exception as e:
                PrintStyle().error(f"Error waiting for preload: {e}")

        _mark("preload wait done")
        initialize.initialize_mcp()
        _mark("mcp init scheduled")
        initialize.initialize_job_loop()
        _mark("job loop scheduled")

        # Warm extension modules off the request path. Importing plugin
        # extension files on the first message costs several seconds; doing it
        # here (daemon thread) makes the first turn feel like a warm turn.
        def _warm_extensions():
            try:
                from helpers.extension import _get_extension_classes

                # Prompt/LLM points only. Importing webui/ws extension points
                # here initializes websocket state off the server loop and
                # breaks the first Socket.IO upgrade.
                points = (
                    "system_prompt",
                    "message_loop_prompts_before",
                    "message_loop_prompts_after",
                    "monologue_start",
                    "monologue_end",
                    "message_loop_start",
                    "message_loop_end",
                    "chat_model_call",
                )
                for point in points:
                    try:
                        _get_extension_classes(point, None)
                    except Exception:
                        pass
            except Exception:
                pass

        # Prompt-extension imports are expensive and run before uvicorn when
        # executed here. Keep them opt-in; the UI bundle prebuild below is cheap.
        import os as _os
        if _os.environ.get("A0_WARMUP_EXTENSIONS", "0") == "1":
            _warm_extensions()
        if _os.environ.get("A0_PREBUILD_UI", "0") == "1":
            try:
                from helpers.ui_bundler import get_ui_asset_bundle
                from helpers.ui_server import UI_INDEX_ASSET_URL

                get_ui_asset_bundle([UI_INDEX_ASSET_URL], agent=None)
                _mark("asset bundle ready")
            except Exception:
                pass

    # Let end hooks run during warmup before waiting and starting MCP/jobs.
    return finish_init


if __name__ == "__main__":
    runtime.initialize()
    dotenv.load_dotenv()
    run()
