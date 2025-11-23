# web_app.py (corrected & hardened)
import asyncio
import importlib.util
import json
import logging
import os
import re  # For input validation
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import bcrypt

# UPGRADE: For real-time collaboration
import redis
import streamlit as st
import yaml

# Import custom modules - Fixed to use absolute imports
from intent_capture.agent_core import RedisStateBackend, get_or_create_agent
from intent_capture.config import Config, PluginManager
from intent_capture.requirements import generate_coverage_report, get_coverage_history
from intent_capture.spec_utils import generate_spec_from_memory
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

# UPGRADE: Observability - Prometheus & OpenTelemetry
from prometheus_client import Counter, start_http_server
from streamlit_autorefresh import st_autorefresh

# P6: Retries for Redis/Agent calls
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

# Optional content-safety plugin (fail-safe fallback)
try:
    from intent_capture.self_evolution_plugin import (  # type: ignore
        _check_content_safety,
        initiate_evolution_cycle,
    )
except Exception:

    async def _check_content_safety(text: str) -> tuple[bool, str]:
        # Fail-safe: allow content by default if plugin is missing
        return True, ""

    def initiate_evolution_cycle(*args, **kwargs):
        return None


# --- Setup & Configuration ---
# P4: Logging - All user actions, errors, and system events must be logged (no sensitive data).
# Configure logging to output JSON by default
class JsonFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record):
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "name": record.name,
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
            "process": record.process,
            "thread": record.thread,
            "username": st.session_state.get(
                "username", "anonymous"
            ),  # P3: Add user/session to logs
        }
        if hasattr(record, "exc_info") and record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)
        if hasattr(record, "stack_info") and record.stack_info:
            log_record["stack_info"] = self.formatStack(record.stack_info)
        # Add any extra attributes passed to the logger
        for key, value in record.__dict__.items():
            if key not in [
                "name",
                "msg",
                "levelname",
                "pathname",
                "filename",
                "lineno",
                "funcName",
                "created",
                "asctime",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "process",
                "exc_info",
                "exc_text",
                "stack_info",
                "args",
                "kwargs",
                "format",
                "levelno",
                "module",
            ]:
                log_record[key] = value
        return json.dumps(log_record, default=str)


# Remove default handlers and add our JSON formatter
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.root.addHandler(handler)
logging.root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())  # Set default level from env
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Intent Capture Agent", layout="wide", initial_sidebar_state="expanded"
)

# P5: Observability - Prometheus Metrics
PROMETHEUS_AVAILABLE = True
try:
    HTTP_REQUESTS_TOTAL = Counter(
        "streamlit_http_requests_total",
        "Total HTTP requests to Streamlit app",
        ["path"],
    )
    APP_ERRORS_TOTAL = Counter(
        "streamlit_app_errors_total",
        "Total errors in Streamlit app",
        ["component", "error_type"],
    )
except Exception:
    PROMETHEUS_AVAILABLE = False
    logger.warning("Prometheus client not initialized. Streamlit metrics will be disabled.")

# Optionally expose a Prometheus endpoint if configured (runs once on first import)
if PROMETHEUS_AVAILABLE and os.getenv("PROMETHEUS_PORT"):
    try:
        start_http_server(int(os.getenv("PROMETHEUS_PORT")))
        logger.info(
            "Prometheus metrics server started.",
            extra={"port": os.getenv("PROMETHEUS_PORT")},
        )
    except Exception as e:
        logger.warning(f"Failed to start Prometheus server: {e}")

# P5: Observability - OpenTelemetry Tracing
if os.getenv("OTEL_ENABLED", "false").lower() == "true":
    resource = Resource.create(attributes={"service.name": "streamlit-web-app"})
    provider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(ConsoleSpanExporter())  # For production, use OTLPSpanExporter
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(__name__)
    logger.info("OpenTelemetry tracing enabled for Streamlit app.")
else:
    tracer = None


# --- UPGRADE: Internationalization (i18n) ---
@st.cache_data(ttl=3600)  # Cache for 1 hour
def load_locales():
    """Loads UI localization strings from a YAML file."""
    try:
        with open("locales.yaml", "r", encoding="utf-8") as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        logger.error("`locales.yaml` not found. Falling back to default English strings.")
        return {
            "en": {
                "_language_name": "English",
                "_language_code": "en",
                "welcome_message": "Welcome",
                "page_chat": "Chat",
                "page_dashboard": "Dashboard",
                "page_specs": "Specs",
                "page_collab": "Collaboration",
                "page_plugins": "Plugins",
                "chat_header": "Interactive Requirements Chat",
                "agent_thinking": "Agent is thinking...",
                "reasoning_expander": "Show Reasoning",
                "initial_thought_tab": "Initial Thought",
                "reflection_tab": "Self-Reflection",
                "critique_tab": "Peer Critique",
                "generate_spec_button": "Generate Spec",
                "spec_generated_success": "Specification generated successfully!",
                "spec_generated_fail": "Failed to generate specification.",
                "latest_spec_header": "Latest Spec",
                "collab_header": "Real-time Collaboration",
                "collab_redis_error": "Real-time collaboration requires a Redis server. Please configure REDIS_URL in your environment.",
                "collab_send_message": "Send a message to the team...",
                "plugin_extensions_header": "Plugin Extensions",
                "plugin_info_message": "This page dynamically renders UI components from enabled plugins.",
                "no_enabled_plugins": "No enabled plugins with UI components found.",
                "error_rendering_plugin": "Error rendering main UI for plugin",
                "plugin_no_main_component": "This plugin does not provide a main UI component.",
                "project_coverage_header": "Project Coverage Analytics",
                "no_coverage_history": "No coverage history found. Generate a spec on the 'Specs' page to start tracking coverage.",
                "agent_not_initialized": "Agent not initialized.",
                "username_input": "Username",
                "password_input": "Password",
                "login_button": "Login",
                "login_error": "Username/password is incorrect",
                "logout_button": "Logout",
                "language_selector": "Language",
                "sidebar_plugins_header": "Plugins",
                "sidebar_no_plugins_info": "No plugins found. Create a directory in `plugins/` with a `plugin_config.json` and a `web_ui.py` to add plugin UI components here.",
                "sidebar_plugin_error": "Error loading UI for plugin",
                "sidebar_plugin_expander_title": "Plugin",
                "captcha_label": "Enter the text below",
                "captcha_error": "CAPTCHA incorrect. Please try again.",
            }
        }


LOCALES = load_locales()
if "lang" not in st.session_state:
    st.session_state.lang = "en"


def t(key: str) -> str:
    """Translation helper function."""
    return LOCALES.get(st.session_state.lang, {}).get(key, key)


def run_async(coroutine):
    """
    Wrapper to run an async coroutine in a new thread.
    This is necessary because Streamlit's main loop is synchronous.
    """
    with ThreadPoolExecutor() as executor:
        future = executor.submit(asyncio.run, coroutine)
        return future.result()


# --- Session State Initialization (define BEFORE use) ---
def init_session_state():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.username = None
    if "agent" not in st.session_state or st.session_state.agent is None:
        if st.session_state.authenticated:
            logger.info(f"Initializing agent for user: {st.session_state.username}")

            @st.cache_resource(ttl=3600)  # Cache agent for 1 hour
            def get_cached_agent(session_id: str):
                app_config = Config()

                # Prefer Redis state if available
                state_backend = None
                if getattr(app_config, "REDIS_URL", None):
                    try:
                        state_backend = RedisStateBackend(redis_url=app_config.REDIS_URL)
                    except Exception:
                        logger.warning(
                            "RedisStateBackend not available, falling back to InMemoryStateBackend."
                        )
                if state_backend is None:
                    state_backend = None

                # Return created/loaded agent by running the async factory
                return run_async(get_or_create_agent(session_token=session_id))

            st.session_state.agent = get_cached_agent(st.session_state.username)
            st.session_state.messages = []
            st.session_state.last_spec = None
            st.session_state.last_spec_format = "markdown"


# --- Authentication ---
try:
    auth_config_path = os.environ.get("AUTH_CONFIG_PATH", "auth_config.yaml")
    with open(auth_config_path) as file:
        auth_config = yaml.safe_load(file)
    # Step 1: Hash cleartext passwords once (migration)
    for user in auth_config.get("credentials", {}).get("usernames", {}).values():
        if not str(user.get("password", "")).startswith("$2b$"):  # bcrypt prefix
            salt = bcrypt.gensalt()
            user["password"] = bcrypt.hashpw(str(user["password"]).encode("utf-8"), salt).decode(
                "utf-8"
            )
    # Persist hashed credentials
    with open(auth_config_path, "w") as file:
        yaml.dump(auth_config, file)
except Exception as e:
    logger.critical(f"Error loading/hashing auth config: {e}")
    st.error("Authentication config error.")
    st.stop()


# P2: CAPTCHA for login if public-facing
def generate_captcha():
    """Generate a new CAPTCHA for the login form."""
    import random
    import string

    st.session_state.captcha_text = "".join(
        random.choices(string.ascii_uppercase + string.digits, k=6)
    )
    st.session_state.captcha_expiry = datetime.now() + timedelta(minutes=5)


if "captcha_text" not in st.session_state:
    generate_captcha()
if "failed_login_attempts" not in st.session_state:
    st.session_state.failed_login_attempts = {}

# --- Login UI ---
if not st.session_state.get("authenticated", False):
    st.subheader(t("login_button"))
    username = st.text_input(t("username_input"), key="login_username")
    password = st.text_input(t("password_input"), type="password", key="login_password")

    # Check for rate limiting
    if st.session_state.failed_login_attempts.get(username, 0) >= 5:
        if datetime.now() < st.session_state.failed_login_attempts.get(
            f"{username}_blocked_until", datetime.now()
        ):
            st.error("Too many failed login attempts. Please wait 10 minutes.")
            st.stop()
        else:
            st.session_state.failed_login_attempts[username] = 0  # Reset attempts

    # CAPTCHA display
    captcha_input = st.text_input(
        f"{t('captcha_label')}: `{st.session_state.captcha_text}`", key="login_captcha"
    )

    if st.button(t("login_button")):
        sanitized_username = re.sub(r"[^\w.-]", "", username).strip()
        sanitized_password = password  # hashed/compared, not sanitized as text

        # CAPTCHA validation
        if (
            datetime.now() > st.session_state.captcha_expiry
            or captcha_input.upper() != st.session_state.captcha_text
        ):
            logger.warning(
                f"Failed login attempt for username: {sanitized_username} (CAPTCHA incorrect or expired)"
            )
            st.error(t("captcha_error"))
            st.session_state.failed_login_attempts[sanitized_username] = (
                st.session_state.failed_login_attempts.get(sanitized_username, 0) + 1
            )
            generate_captcha()
            st.rerun()

        # Password verification
        user_info = auth_config.get("credentials", {}).get("usernames", {}).get(sanitized_username)
        if user_info and bcrypt.checkpw(
            sanitized_password.encode("utf-8"), user_info["password"].encode("utf-8")
        ):
            st.session_state.authenticated = True
            st.session_state.username = sanitized_username
            st.session_state.failed_login_attempts[sanitized_username] = 0
            logger.info(f"User '{sanitized_username}' authenticated successfully.")
            # NOTE: Do NOT call init_session_state here; define-then-call pattern prevents NameError
            st.rerun()
        else:
            logger.warning(
                f"Failed login attempt for username: {sanitized_username} (credentials incorrect)"
            )
            st.error(t("login_error"))
            st.session_state.failed_login_attempts[sanitized_username] = (
                st.session_state.failed_login_attempts.get(sanitized_username, 0) + 1
            )
            if st.session_state.failed_login_attempts[sanitized_username] >= 5:
                st.session_state.failed_login_attempts[f"{sanitized_username}_blocked_until"] = (
                    datetime.now() + timedelta(minutes=10)
                )
            generate_captcha()
            st.rerun()
    st.stop()

# Ensure state is initialized after successful login
init_session_state()


# --- UPGRADE: Real-Time Collaboration Setup ---
def get_redis_client():
    config = Config()
    redis_url = getattr(config, "REDIS_URL", os.getenv("REDIS_URL"))
    if redis_url:
        try:

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, max=10),
                before_sleep=before_sleep_log(logger, logging.WARNING),
            )
            def _connect_redis():
                client = redis.from_url(redis_url, decode_responses=True)
                client.ping()
                return client

            client = _connect_redis()
            logger.info("Redis client connected successfully.")
            return client
        except Exception as e:
            logger.error(f"Failed to connect to Redis at {redis_url}: {e}", exc_info=True)
            if PROMETHEUS_AVAILABLE:
                APP_ERRORS_TOTAL.labels(
                    component="redis_connection", error_type=type(e).__name__
                ).inc()
            return None
    logger.warning("REDIS_URL not configured. Real-time collaboration will be unavailable.")
    return None


redis_client = get_redis_client()
COLLAB_CHANNEL = f"collab_chat:{st.session_state.username}" if redis_client else None
# NOTE: We intentionally avoid using a background thread that calls st.experimental_rerun()
# from outside the main thread. Instead, we rely on st_autorefresh to poll and rerun safely.


# --- Sidebar and Page Routing ---
with st.sidebar:
    st.write(f"{t('welcome_message')}, **{st.session_state.username}**")

    selected_lang_display = st.selectbox(
        t("language_selector"),
        options=list(LOCALES.keys()),
        format_func=lambda x: LOCALES[x].get("_language_name", x).capitalize(),
    )
    if LOCALES[selected_lang_display].get("_language_code") != st.session_state.lang:
        st.session_state.lang = LOCALES[selected_lang_display].get("_language_code")
        logger.info(f"Language changed to: {st.session_state.lang}")
        st.rerun()

    page = st.radio(
        "Navigate",
        [
            t("page_chat"),
            t("page_dashboard"),
            t("page_specs"),
            t("page_collab"),
            t("page_plugins"),
            "Health",
        ],
    )

    st.header(t("sidebar_plugins_header"))
    enabled_plugins = PluginManager.get_plugin_diagnostics()
    if not enabled_plugins:
        st.info(t("sidebar_no_plugins_info"), icon="💡")
    else:
        for plugin in enabled_plugins:
            if plugin["status"] == "enabled":
                try:
                    cfg = Config()
                    plugin_ui_path = os.path.join(cfg.PLUGINS_DIR, plugin["name"], "web_ui.py")
                    if os.path.exists(plugin_ui_path):
                        spec = importlib.util.spec_from_file_location(
                            plugin["name"], plugin_ui_path
                        )
                        module = importlib.util.module_from_spec(spec)
                        assert spec.loader is not None
                        spec.loader.exec_module(module)
                        if hasattr(module, "render_sidebar_component"):
                            with st.expander(
                                f"{t('sidebar_plugin_expander_title')}: {plugin['name'].replace('_', ' ').title()}",
                                expanded=True,
                            ):
                                module.render_sidebar_component(st)
                except Exception as e:
                    logger.error(
                        f"{t('sidebar_plugin_error')} '{plugin['name']}': {e}",
                        exc_info=True,
                    )
                    if PROMETHEUS_AVAILABLE:
                        APP_ERRORS_TOTAL.labels(
                            component="plugin_sidebar_ui", error_type=type(e).__name__
                        ).inc()
                    st.error(f"{t('sidebar_plugin_error')} '{plugin['name']}'.")

    if st.sidebar.button(t("logout_button")):
        st.session_state.authenticated = False
        st.session_state.username = None
        st.session_state.agent = None
        st.session_state.messages = []
        logger.info("User logged out.")
        st.rerun()


# --- Page Implementations ---
st.title("Intent Capture Agent")


# ----- Chat Page -----
def render_chat_page():
    if PROMETHEUS_AVAILABLE:
        HTTP_REQUESTS_TOTAL.labels(path="/chat").inc()

    st.header(t("chat_header"))
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant" and "trace" in message:
                with st.expander(t("reasoning_expander"), expanded=False):
                    tabs = st.tabs(
                        [
                            t("initial_thought_tab"),
                            t("reflection_tab"),
                            t("critique_tab"),
                        ]
                    )
                    with tabs[0]:
                        st.info(message["trace"]["initial_response"])
                    with tabs[1]:
                        st.warning(message["trace"]["reflection"])
                    with tabs[2]:
                        st.error(message["trace"]["critique"])

    MAX_CHAT_INPUT_LENGTH = 2000
    if prompt := st.chat_input(
        "Describe your project or requirements...", max_chars=MAX_CHAT_INPUT_LENGTH
    ):
        sanitized_prompt = re.sub(r"<[^>]*>", "", prompt).strip()
        if not sanitized_prompt:
            st.warning("Please enter a non-empty message.")
            st.stop()

        is_safe, safety_reason = run_async(_check_content_safety(sanitized_prompt))
        if not is_safe:
            st.error(f"Input failed content safety check: {safety_reason}")
            logger.warning(
                f"User '{st.session_state.username}' input failed safety check: {safety_reason}"
            )
            st.stop()

        logger.info(f"User '{st.session_state.username}' input: {sanitized_prompt[:100]}...")
        st.session_state.messages.append({"role": "user", "content": sanitized_prompt})
        with st.chat_message("user"):
            st.markdown(sanitized_prompt)

        with st.spinner(t("agent_thinking")):
            try:

                @retry(
                    stop=stop_after_attempt(3),
                    wait=wait_exponential(multiplier=1, max=10),
                    before_sleep=before_sleep_log(logger, logging.WARNING),
                )
                async def get_agent_response(user_input: str):
                    return await st.session_state.agent.predict(user_input=user_input)

                response_data = run_async(get_agent_response(sanitized_prompt))
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": response_data["response"],
                        "trace": response_data["trace"],
                    }
                )
                logger.info(f"Agent response generated for user '{st.session_state.username}'.")
            except Exception as e:
                logger.error(
                    f"Agent prediction failed for user '{st.session_state.username}': {e}",
                    exc_info=True,
                )
                if PROMETHEUS_AVAILABLE:
                    APP_ERRORS_TOTAL.labels(
                        component="agent_predict", error_type=type(e).__name__
                    ).inc()
                st.error(f"Sorry, the agent encountered an error: {e}")
        st.rerun()


# ----- Dashboard Page -----
def render_dashboard_page():
    if PROMETHEUS_AVAILABLE:
        HTTP_REQUESTS_TOTAL.labels(path="/dashboard").inc()

    st.header(t("page_dashboard"))
    st.subheader(t("project_coverage_header"))

    if "agent" in st.session_state and st.session_state.agent:

        @st.cache_data(ttl=300)
        def _cached_history(session_id: str):
            return run_async(get_coverage_history(session_id))

        @st.cache_data(ttl=300)
        def _cached_report(session_id: str):
            return run_async(generate_coverage_report(session_id))

        history = _cached_history(st.session_state.agent.session_id)
        if history:
            report = _cached_report(st.session_state.agent.session_id)
            st.markdown(report)
        else:
            st.info(t("no_coverage_history"))
    else:
        st.warning(t("agent_not_initialized"))


# ----- Specs Page -----
def render_specs_page():
    if PROMETHEUS_AVAILABLE:
        HTTP_REQUESTS_TOTAL.labels(path="/specs").inc()

    st.header("Specification Management")
    config = Config()
    supported_spec_formats = getattr(
        config,
        "SUPPORTED_SPEC_FORMATS",
        ["markdown", "gherkin", "json", "yaml", "user_story"],
    )
    spec_format = st.selectbox("Specification Format", options=supported_spec_formats, index=0)

    if st.button(t("generate_spec_button"), use_container_width=True):
        logger.info(
            f"User '{st.session_state.username}' attempting to generate spec in {spec_format} format."
        )
        with st.spinner("Generating specification..."):
            try:

                @retry(
                    stop=stop_after_attempt(3),
                    wait=wait_exponential(multiplier=1, max=10),
                    before_sleep=before_sleep_log(logger, logging.WARNING),
                )
                async def generate_spec_async():
                    return await generate_spec_from_memory(
                        st.session_state.agent.memory,
                        st.session_state.agent._llm,
                        format=spec_format,
                        persona=st.session_state.agent._persona_key,
                        language=st.session_state.lang,
                    )

                spec_data = run_async(generate_spec_async())
                if spec_data:
                    st.session_state.last_spec = spec_data["content"]
                    st.session_state.last_spec_format = spec_format
                    st.success(t("spec_generated_success"))
                    logger.info(
                        f"Spec generated successfully for user '{st.session_state.username}'."
                    )
                else:
                    st.error(t("spec_generated_fail"))
                    logger.error(f"Failed to generate spec for user '{st.session_state.username}'.")
                    if PROMETHEUS_AVAILABLE:
                        APP_ERRORS_TOTAL.labels(
                            component="generate_spec", error_type="GenerationFailed"
                        ).inc()
            except Exception as e:
                logger.error(
                    f"Error generating spec for user '{st.session_state.username}': {e}",
                    exc_info=True,
                )
                if PROMETHEUS_AVAILABLE:
                    APP_ERRORS_TOTAL.labels(
                        component="generate_spec", error_type=type(e).__name__
                    ).inc()
                st.error(f"An error occurred during spec generation: {e}")

    if st.session_state.last_spec:
        st.subheader(f"{t('latest_spec_header')} ({st.session_state.last_spec_format})")
        st.code(st.session_state.last_spec, language=st.session_state.last_spec_format)


# ----- Collaboration Page -----
def render_collab_page():
    if PROMETHEUS_AVAILABLE:
        HTTP_REQUESTS_TOTAL.labels(path="/collab").inc()

    st.header(t("collab_header"))
    if not redis_client:
        st.error(t("collab_redis_error"), icon="🚨")
        if PROMETHEUS_AVAILABLE:
            APP_ERRORS_TOTAL.labels(
                component="collab_redis", error_type="RedisConnectionError"
            ).inc()
        st.stop()

    st_autorefresh(interval=3000, limit=None, key="collab_refresher")
    st.subheader("Shared Chat")

    try:

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, max=10),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        def _get_redis_history():
            return redis_client.lrange(COLLAB_CHANNEL, -100, -1)

        message_history = _get_redis_history()
        for msg_json in message_history:
            msg = json.loads(msg_json)
            with st.chat_message(
                msg.get("role", "user"),
                avatar="🧑‍💻" if msg.get("role") == "user" else "🤖",
            ):
                st.write(f"**{msg.get('username', 'Unknown')}**: {msg.get('content', '')}")
    except Exception as e:
        logger.error(f"Error retrieving collaboration history from Redis: {e}", exc_info=True)
        if PROMETHEUS_AVAILABLE:
            APP_ERRORS_TOTAL.labels(component="collab_history", error_type=type(e).__name__).inc()
        st.warning("Could not load collaboration history.")

    MAX_COLLAB_INPUT_LENGTH = 1000
    if collab_prompt := st.chat_input(t("collab_send_message"), max_chars=MAX_COLLAB_INPUT_LENGTH):
        sanitized_collab_prompt = re.sub(r"<[^>]*>", "", collab_prompt).strip()
        if not sanitized_collab_prompt:
            st.warning("Please enter a non-empty message.")
            st.stop()

        message_data = {
            "role": "user",
            "username": st.session_state.username,
            "content": sanitized_collab_prompt,
            "timestamp": datetime.utcnow().isoformat(),
        }
        try:

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, max=10),
                before_sleep=before_sleep_log(logger, logging.WARNING),
            )
            def _send_collab_message():
                redis_client.rpush(COLLAB_CHANNEL, json.dumps(message_data))
                redis_client.publish(COLLAB_CHANNEL, json.dumps(message_data))

            _send_collab_message()
            logger.info(f"User '{st.session_state.username}' sent collab message.")
        except Exception as e:
            logger.error(f"Failed to send collaboration message: {e}", exc_info=True)
            if PROMETHEUS_AVAILABLE:
                APP_ERRORS_TOTAL.labels(component="collab_send", error_type=type(e).__name__).inc()
            st.error("Failed to send message. Please try again.")
        st.rerun()


# ----- Plugins Page -----
def render_plugins_page():
    if PROMETHEUS_AVAILABLE:
        HTTP_REQUESTS_TOTAL.labels(path="/plugins").inc()

    st.header(t("plugin_extensions_header"))
    st.info(t("plugin_info_message"), icon="🔌")

    enabled_plugins = [
        p for p in PluginManager.get_plugin_diagnostics() if p["status"] == "enabled"
    ]
    if not enabled_plugins:
        st.warning(t("no_enabled_plugins"))
    else:
        for plugin in enabled_plugins:
            try:
                cfg = Config()
                plugin_ui_path = os.path.join(cfg.PLUGINS_DIR, plugin["name"], "web_ui.py")
                if os.path.exists(plugin_ui_path):
                    with st.container(border=True):
                        st.subheader(f"Plugin: {plugin['name'].replace('_', ' ').title()}")
                        spec = importlib.util.spec_from_file_location(
                            plugin["name"], plugin_ui_path
                        )
                        module = importlib.util.module_from_spec(spec)
                        assert spec.loader is not None
                        spec.loader.exec_module(module)
                        if hasattr(module, "render_main_component"):
                            module.render_main_component(st)
                        else:
                            st.info(t("plugin_no_main_component"))
            except Exception as e:
                logger.error(
                    f"{t('error_rendering_plugin')} '{plugin['name']}': {e}",
                    exc_info=True,
                )
                if PROMETHEUS_AVAILABLE:
                    APP_ERRORS_TOTAL.labels(
                        component="plugin_main_ui", error_type=type(e).__name__
                    ).inc()
                st.error(f"{t('error_rendering_plugin')} '{plugin['name']}'.")


# ----- Health Page -----
def render_health_page():
    st.header("Application Health")
    st.markdown(
        "This page provides a simple health check for the application and its dependencies."
    )

    health_status = {"status": "Healthy", "details": {}}

    # Check Redis
    try:
        if redis_client:
            redis_client.ping()
            health_status["details"]["redis"] = "Connected"
        else:
            raise redis.exceptions.ConnectionError("Not connected")
    except Exception as e:
        health_status["status"] = "Degraded"
        health_status["details"]["redis"] = f"Failed: {e}"

    # Check LLM (via agent)
    try:
        if st.session_state.agent and st.session_state.agent._llm:
            health_status["details"]["llm"] = "Available"
        else:
            raise RuntimeError("Agent/LLM not initialized")
    except Exception as e:
        health_status["status"] = "Degraded"
        health_status["details"]["llm"] = f"Failed: {e}"

    st.json(health_status)


# -------- Route to selected page (with optional tracing) --------
try:
    if page == t("page_chat"):
        if tracer:
            with tracer.start_as_current_span("chat_page_view"):
                render_chat_page()
        else:
            render_chat_page()

    elif page == t("page_dashboard"):
        if tracer:
            with tracer.start_as_current_span("dashboard_page_view"):
                render_dashboard_page()
        else:
            render_dashboard_page()

    elif page == t("page_specs"):
        if tracer:
            with tracer.start_as_current_span("specs_page_view"):
                render_specs_page()
        else:
            render_specs_page()

    elif page == t("page_collab"):
        if tracer:
            with tracer.start_as_current_span("collab_page_view"):
                render_collab_page()
        else:
            render_collab_page()

    elif page == t("page_plugins"):
        if tracer:
            with tracer.start_as_current_span("plugins_page_view"):
                render_plugins_page()
        else:
            render_plugins_page()

    elif page == "Health":
        render_health_page()
except Exception as e:
    # Per-page top-level error capture
    current = page if isinstance(page, str) else "unknown"
    logger.error(f"Error on {current} page: {e}", exc_info=True)
    if PROMETHEUS_AVAILABLE:
        APP_ERRORS_TOTAL.labels(component=f"{current}_page", error_type=type(e).__name__).inc()
    st.error(f"An unexpected error occurred on the {current} page: {e}")


# P11/P12: Dockerfile and CI-CD notes remain in comments below for reference.
"""
# Dockerfile for Streamlit application
FROM python:3.12-slim-bookworm

WORKDIR /app
COPY requirements.txt ./

RUN pip install --no-cache-dir \
    streamlit==1.48.1 \
    pyyaml \
    redis==6.4.0 \
    streamlit-autorefresh \
    prometheus_client==0.22.1 \
    opentelemetry-sdk==1.36.0 \
    opentelemetry-exporter-otlp \
    opentelemetry-instrumentation-wsgi \
    bcrypt==4.2.0 \
    tenacity==9.1.2 \
    langchain-core \
    langchain-openai \
    psutil \
    termcolor \
    rich \
    python-dotenv \
    cryptography \
    cachetools \
    aiohttp \
    portalocker \
    sentence-transformers \
    torch \
    pandas \
    asyncpg \
    pyjwt \
    nltk

RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"

COPY . /app
EXPOSE 8501
CMD ["streamlit", "run", "web_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
"""

"""
# .github/workflows/streamlit-ci-cd.yml
name: Streamlit CI/CD Pipeline
on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  build-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v3
        with:
          python-version: 3.12
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov flake8 black isort
      - name: Run linting
        run: |
          flake8 .
          black --check .
          isort --check-only .
      - name: Run unit tests
        run: |
          pytest --cov=. --cov-report=xml
"""
