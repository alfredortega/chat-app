import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")


def get_connection():
    """
    Return a per-request SQLite connection stored on Flask's ``g`` object so
    that all DB calls within the same request share one connection instead of
    opening a fresh one each time.  Falls back to a plain connection when
    called outside a Flask request context (e.g. init_db at startup).
    Fix #2: request-scoped connection via Flask g.
    """
    try:
        from flask import g
        if "db" not in g:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db
    except RuntimeError:
        # Outside application context (startup, tests, CLI)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


# ── Starter personas ───────────────────────────────────────────────────────────

STARTER_PERSONAS = [
    (
        "Business Analyst",
        "You are a senior Business Analyst. Focus on eliciting and documenting requirements, "
        "writing clear user stories and acceptance criteria, analysing business processes, "
        "identifying gaps and improvements, and communicating findings in plain language "
        "suitable for both technical and non-technical stakeholders. Use structured formats "
        "such as use-case tables, process flows, and RACI matrices where helpful."
    ),
    (
        "Python Developer",
        "You are an expert Python developer. Write clean, idiomatic, PEP-8-compliant Python. "
        "Prefer built-in libraries and the standard library where possible. Suggest appropriate "
        "design patterns, highlight edge cases, include type hints, and write docstrings. "
        "When reviewing code, call out performance concerns, security issues, and testability. "
        "Provide working code examples with brief explanations."
    ),
    (
        "Project Manager",
        "You are an experienced Project Manager. Think in terms of scope, schedule, budget, "
        "risk, and stakeholder communication. Help create project plans, risk registers, "
        "status reports, and meeting agendas. Identify dependencies and critical-path items. "
        "Use recognised frameworks (Agile, PRINCE2, PMI) where relevant, and always keep the "
        "conversation focused on actionable next steps."
    ),
    (
        "Data Scientist",
        "You are a skilled Data Scientist. Help with data exploration, feature engineering, "
        "model selection, evaluation metrics, and interpretation of results. Use Python "
        "(pandas, NumPy, scikit-learn, matplotlib) as the default toolset. Explain statistical "
        "concepts clearly, highlight assumptions and limitations, and always consider "
        "reproducibility and data ethics."
    ),
    (
        "Database Developer",
        "You are a senior Database Developer specializing in relational database design, SQL performance, "
        "data integrity, and safe schema evolution. Design clear, normalized schemas where appropriate, "
        "write efficient and maintainable queries, use indexes intentionally, and preserve transactional "
        "correctness. Prioritize data quality, security, least-privilege access, backup/restore awareness, "
        "and migration safety. Avoid destructive changes, exposing sensitive data, or making assumptions "
        "about production data. When proposing changes, explain tradeoffs, include rollback considerations, "
        "and ask before modifying schemas, permissions, stored procedures, or data migration logic."
    ),
    (
        "Technical Writer",
        "You are a professional Technical Writer. Produce clear, concise, audience-appropriate "
        "documentation: API references, user guides, README files, release notes, and "
        "runbooks. Follow the Diátaxis framework (tutorials, how-to guides, reference, "
        "explanation) where appropriate. Use active voice, plain language, and consistent "
        "terminology. Format output in clean Markdown."
    ),
    (
        "DevSecOps Engineer",
        "You are a senior DevSecOps engineer focused on secure, reliable delivery. Review code, "
        "infrastructure, CI/CD, dependencies, configuration, and runtime practices for security, "
        "maintainability, and operational risk. Prefer least privilege, secure defaults, reproducible "
        "builds, automated testing, vulnerability scanning, secret management, auditability, and clear "
        "rollback paths. Identify risks with severity, evidence, and practical remediation steps. "
        "Do not expose secrets or sensitive data, and ask before making changes to deployment, "
        "access control, production, or compliance-related configurations."
    ),
    (
        "Security Analyst",
        "You are a cybersecurity analyst. Assess threats and vulnerabilities, recommend "
        "mitigations, and explain security concepts clearly. Reference frameworks such as "
        "MITRE ATT&CK, OWASP Top 10, NIST, and CIS Controls where applicable. When reviewing "
        "code or architecture, identify attack surfaces and suggest defence-in-depth strategies. "
        "Always note relevant compliance considerations (GDPR, HIPAA, SOC2)."
    ),
    (
        "UX Designer",
        "You are an experienced UX Designer. Help with user research planning, persona "
        "creation, wireframe descriptions, information architecture, and usability heuristics. "
        "Ground recommendations in accessibility standards (WCAG 2.1), Nielsen's heuristics, "
        "and evidence-based design principles. Describe layouts and interactions clearly "
        "in text, and suggest tools and methods appropriate to the project stage."
    ),
]


def init_db():
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                position   INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT    NOT NULL DEFAULT 'New Conversation',
                model_id     TEXT    NOT NULL DEFAULT '',
                persona_id   INTEGER REFERENCES personas(id) ON DELETE SET NULL,
                endpoint_id  INTEGER REFERENCES endpoints(id) ON DELETE SET NULL,
                output_dir   TEXT    NOT NULL DEFAULT '',
                enable_tools INTEGER NOT NULL DEFAULT 1,
                folder_id    INTEGER REFERENCES folders(id) ON DELETE SET NULL,
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id     INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role                TEXT    NOT NULL,
                content             TEXT    NOT NULL,
                tool_call_id        TEXT,
                tool_calls_json     TEXT,
                created_at          TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS personas (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL UNIQUE,
                prompt     TEXT    NOT NULL,
                created_at TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conv_files (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                original_name   TEXT    NOT NULL,
                disk_path       TEXT    NOT NULL,
                size_bytes      INTEGER NOT NULL DEFAULT 0,
                char_count      INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS linked_folders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                folder_path     TEXT    NOT NULL,
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS endpoints (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL,
                base_url      TEXT    NOT NULL,
                api_key       TEXT    NOT NULL DEFAULT '',
                default_model TEXT    NOT NULL DEFAULT '',
                is_default    INTEGER NOT NULL DEFAULT 0,
                model_filter  TEXT    NOT NULL DEFAULT '',
                created_at    TEXT    NOT NULL,
                updated_at    TEXT    NOT NULL
            );

            INSERT OR IGNORE INTO settings (key, value) VALUES ('output_dir', '');
            INSERT OR IGNORE INTO settings (key, value) VALUES ('browser_root', '');

            -- Fix #14: indexes on foreign-key columns used in WHERE clauses
            CREATE INDEX IF NOT EXISTS idx_messages_conv_id       ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_conv_files_conv_id     ON conv_files(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_linked_folders_conv_id ON linked_folders(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_folder_id ON conversations(folder_id);
            CREATE INDEX IF NOT EXISTS idx_endpoints_is_default   ON endpoints(is_default);
            """
        )
        conn.execute("PRAGMA journal_mode=WAL")

        # Add persona_id column to conversations if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN persona_id INTEGER REFERENCES personas(id) ON DELETE SET NULL")
        except Exception:
            pass  # column already exists

        # Add output_dir column to conversations if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN output_dir TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # column already exists

        # Add enable_tools column to conversations if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN enable_tools INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass  # column already exists

        # Add endpoint_id column to conversations if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN endpoint_id INTEGER REFERENCES endpoints(id) ON DELETE SET NULL")
        except Exception:
            pass  # column already exists

        # Add default_model column to endpoints if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE endpoints ADD COLUMN default_model TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # column already exists

        # Add model_filter column to endpoints if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE endpoints ADD COLUMN model_filter TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # column already exists

        # Add folder_id column to conversations if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")
        except Exception:
            pass  # column already exists

        # Add snippet column to conv_files if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE conv_files ADD COLUMN snippet TEXT")
        except Exception:
            pass  # column already exists

        # Remove context_window and default_model if upgrading from old DB
        try:
            conn.execute("DELETE FROM settings WHERE key IN ('context_window', 'default_model')")
        except Exception:
            pass

        # Seed starter personas (skip if they already exist)
        for name, prompt in STARTER_PERSONAS:
            conn.execute(
                "INSERT OR IGNORE INTO personas (name, prompt, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, prompt, _now(), _now()),
            )


# ── Conversations ──────────────────────────────────────────────────────────────

def list_conversations():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, model_id, persona_id, endpoint_id, output_dir, enable_tools, folder_id, created_at, updated_at "
            "FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_conversation(title: str, model_id: str, persona_id: int = None, endpoint_id: int = None, folder_id: int = None) -> dict:
    now = _now()
    if endpoint_id is None:
        default_ep = get_default_endpoint()
        endpoint_id = default_ep["id"] if default_ep else None
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (title, model_id, persona_id, endpoint_id, folder_id, output_dir, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '', ?, ?)",
            (title, model_id, persona_id, endpoint_id, folder_id, now, now),
        )
        row = conn.execute(
            "SELECT id, title, model_id, persona_id, endpoint_id, output_dir, enable_tools, folder_id, created_at, updated_at FROM conversations WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def get_conversation(conversation_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, model_id, persona_id, endpoint_id, output_dir, enable_tools, folder_id, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def update_conversation(conversation_id: int, title: str = None, model_id: str = None, persona_id: int = None, clear_persona: bool = False, output_dir: str = None, enable_tools: bool = None, endpoint_id: int = None, clear_endpoint: bool = False, folder_id: int = None, clear_folder: bool = False):
    fields, params = [], []
    if title is not None:
        fields.append("title = ?")
        params.append(title)
    if model_id is not None:
        fields.append("model_id = ?")
        params.append(model_id)
    if persona_id is not None:
        fields.append("persona_id = ?")
        params.append(persona_id)
    elif clear_persona:
        fields.append("persona_id = NULL")
    if endpoint_id is not None:
        fields.append("endpoint_id = ?")
        params.append(endpoint_id)
    elif clear_endpoint:
        fields.append("endpoint_id = NULL")
    if folder_id is not None:
        fields.append("folder_id = ?")
        params.append(folder_id)
    elif clear_folder:
        fields.append("folder_id = NULL")
    if output_dir is not None:
        fields.append("output_dir = ?")
        params.append(output_dir)
    if enable_tools is not None:
        fields.append("enable_tools = ?")
        params.append(1 if enable_tools else 0)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(conversation_id)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?", params
        )


def delete_conversation(conversation_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def touch_conversation(conversation_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )


# ── Messages ───────────────────────────────────────────────────────────────────

def get_messages(conversation_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, conversation_id, role, content, tool_call_id, tool_calls_json, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(
    conversation_id: int,
    role: str,
    content: str,
    tool_call_id: str = None,
    tool_calls_json: str = None,
) -> dict:
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, tool_call_id, tool_calls_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, tool_call_id, tool_calls_json, now),
        )
        row = conn.execute(
            "SELECT id, conversation_id, role, content, tool_call_id, tool_calls_json, created_at "
            "FROM messages WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


# ── Settings ───────────────────────────────────────────────────────────────────

def get_setting(key: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def reset_settings() -> dict:
    """
    Reset the core configuration settings to empty:
    output_dir (default output folder) and browser_root
    (folder browser starting path). Also clears each endpoint's
    default_model so no default/fallback model remains configured.
    Personas, endpoints (other than their default model) and conversations
    are left intact.
    """
    with get_connection() as conn:
        for key in ("output_dir", "browser_root"):
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, '') "
                "ON CONFLICT(key) DO UPDATE SET value = ''",
                (key,),
            )
        # Clear the per-endpoint default (fallback) model too.
        conn.execute("UPDATE endpoints SET default_model = ''")
    return {
        "output_dir": "",
        "browser_root": "",
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

def list_endpoints() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, base_url, api_key, default_model, is_default, model_filter, created_at, updated_at "
            "FROM endpoints ORDER BY is_default DESC, name ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_endpoint(endpoint_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, base_url, api_key, default_model, is_default, model_filter, created_at, updated_at "
            "FROM endpoints WHERE id = ?",
            (endpoint_id,),
        ).fetchone()
    return dict(row) if row else None


def get_default_endpoint() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, base_url, api_key, default_model, is_default, model_filter, created_at, updated_at "
            "FROM endpoints WHERE is_default = 1 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            # Fall back to any endpoint if none is explicitly marked default
            row = conn.execute(
                "SELECT id, name, base_url, api_key, default_model, is_default, model_filter, created_at, updated_at "
                "FROM endpoints ORDER BY id ASC LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def create_endpoint(name: str, base_url: str, api_key: str = "", default_model: str = "", is_default: bool = False, model_filter: str = "") -> dict:
    now = _now()
    with get_connection() as conn:
        if is_default:
            conn.execute("UPDATE endpoints SET is_default = 0")
        cur = conn.execute(
            "INSERT INTO endpoints (name, base_url, api_key, default_model, is_default, model_filter, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, base_url, api_key, default_model, 1 if is_default else 0, model_filter, now, now),
        )
        # If this is the very first endpoint, make it default regardless.
        total = conn.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0]
        if total == 1:
            conn.execute("UPDATE endpoints SET is_default = 1 WHERE id = ?", (cur.lastrowid,))
        row = conn.execute(
            "SELECT id, name, base_url, api_key, default_model, is_default, model_filter, created_at, updated_at "
            "FROM endpoints WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def update_endpoint(endpoint_id: int, name: str = None, base_url: str = None,
                    api_key: str = None, default_model: str = None, is_default: bool = None, model_filter: str = None) -> dict | None:
    fields, params = [], []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if base_url is not None:
        fields.append("base_url = ?")
        params.append(base_url)
    if api_key is not None:
        fields.append("api_key = ?")
        params.append(api_key)
    if default_model is not None:
        fields.append("default_model = ?")
        params.append(default_model)
    if model_filter is not None:
        fields.append("model_filter = ?")
        params.append(model_filter)
    with get_connection() as conn:
        # Handle default flag exclusively (only one endpoint may be default).
        if is_default is True:
            conn.execute("UPDATE endpoints SET is_default = 0")
            fields.append("is_default = ?")
            params.append(1)
        elif is_default is False:
            fields.append("is_default = ?")
            params.append(0)
        if fields:
            fields.append("updated_at = ?")
            params.append(_now())
            params.append(endpoint_id)
            conn.execute(
                f"UPDATE endpoints SET {', '.join(fields)} WHERE id = ?", params
            )
        row = conn.execute(
            "SELECT id, name, base_url, api_key, default_model, is_default, model_filter, created_at, updated_at "
            "FROM endpoints WHERE id = ?",
            (endpoint_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_endpoint(endpoint_id: int):
    with get_connection() as conn:
        was_default = conn.execute(
            "SELECT is_default FROM endpoints WHERE id = ?", (endpoint_id,)
        ).fetchone()
        conn.execute("DELETE FROM endpoints WHERE id = ?", (endpoint_id,))
        # If we removed the default, promote another endpoint to default.
        if was_default and was_default["is_default"]:
            nxt = conn.execute(
                "SELECT id FROM endpoints ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if nxt:
                conn.execute(
                    "UPDATE endpoints SET is_default = 1 WHERE id = ?", (nxt["id"],)
                )


# ── Personas ───────────────────────────────────────────────────────────────────

def list_personas() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, prompt, created_at, updated_at FROM personas ORDER BY name ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_persona(persona_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, prompt, created_at, updated_at FROM personas WHERE id = ?",
            (persona_id,),
        ).fetchone()
    return dict(row) if row else None


def create_persona(name: str, prompt: str) -> dict:
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO personas (name, prompt, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, prompt, now, now),
        )
        row = conn.execute(
            "SELECT id, name, prompt, created_at, updated_at FROM personas WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def update_persona(persona_id: int, name: str = None, prompt: str = None):
    fields, params = [], []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if prompt is not None:
        fields.append("prompt = ?")
        params.append(prompt)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(persona_id)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE personas SET {', '.join(fields)} WHERE id = ?", params
        )


def delete_persona(persona_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))


# ── Conversation files ─────────────────────────────────────────────────────────

def add_conv_file(conversation_id: int, original_name: str, disk_path: str,
                  size_bytes: int, char_count: int, snippet: str = "") -> dict:
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO conv_files (conversation_id, original_name, disk_path, size_bytes, char_count, snippet, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, original_name, disk_path, size_bytes, char_count, snippet, now),
        )
        row = conn.execute(
            "SELECT id, conversation_id, original_name, disk_path, size_bytes, char_count, snippet, created_at "
            "FROM conv_files WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def list_conv_files(conversation_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, conversation_id, original_name, disk_path, size_bytes, char_count, created_at "
            "FROM conv_files WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conv_file(file_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, conversation_id, original_name, disk_path, size_bytes, char_count, created_at "
            "FROM conv_files WHERE id = ?",
            (file_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_conv_file(file_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM conv_files WHERE id = ?", (file_id,))


def delete_last_assistant_turn(conversation_id: int):
    """
    Remove the last assistant message and any tool messages that follow it,
    so the model can regenerate a fresh response.

    Fix #15: perform SELECT and DELETE in a single connection/transaction
    to eliminate the TOCTOU gap between two separate connections.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, role FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()

        if not rows:
            return

        # Walk backwards to find the last assistant message
        cut_from_id = None
        for row in reversed(rows):
            if row["role"] == "assistant":
                cut_from_id = row["id"]
                break

        if cut_from_id is None:
            return

        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
            (conversation_id, cut_from_id),
        )


def edit_message_and_truncate(conversation_id: int, message_id: int, new_content: str):
    """Update a message's content and delete all messages after it."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE messages SET content = ? WHERE id = ? AND conversation_id = ?",
            (new_content, message_id, conversation_id),
        )
        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id > ?",
            (conversation_id, message_id),
        )


def search_all_conversations(query: str) -> list[dict]:
    """Search message content across all conversations. Returns unique conversations with a snippet."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT c.id, c.title, c.updated_at, m.content as snippet
            FROM conversations c
            JOIN messages m ON m.conversation_id = c.id
            WHERE lower(m.content) LIKE ?
            ORDER BY c.updated_at DESC
            LIMIT 50
            """,
            (f"%{query.lower()}%",),
        ).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        # Trim snippet to 200 chars around the match
        content = d.get("snippet", "")
        idx = content.lower().find(query.lower())
        if idx >= 0:
            start = max(0, idx - 80)
            end   = min(len(content), idx + 120)
            d["snippet"] = ("…" if start > 0 else "") + content[start:end] + ("…" if end < len(content) else "")
        results.append(d)
    return results


# ── Linked folders ─────────────────────────────────────────────────────────────

def add_linked_folder(conversation_id: int, folder_path: str) -> dict:
    now = _now()
    with get_connection() as conn:
        # Prevent exact duplicates on the same conversation
        existing = conn.execute(
            "SELECT id FROM linked_folders WHERE conversation_id = ? AND folder_path = ?",
            (conversation_id, folder_path),
        ).fetchone()
        if existing:
            row = conn.execute(
                "SELECT id, conversation_id, folder_path, created_at FROM linked_folders WHERE id = ?",
                (existing["id"],),
            ).fetchone()
            return dict(row)
        cur = conn.execute(
            "INSERT INTO linked_folders (conversation_id, folder_path, created_at) VALUES (?, ?, ?)",
            (conversation_id, folder_path, now),
        )
        row = conn.execute(
            "SELECT id, conversation_id, folder_path, created_at FROM linked_folders WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def list_linked_folders(conversation_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, conversation_id, folder_path, created_at "
            "FROM linked_folders WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_linked_folder(folder_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, conversation_id, folder_path, created_at FROM linked_folders WHERE id = ?",
            (folder_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_linked_folder(folder_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM linked_folders WHERE id = ?", (folder_id,))


# ── Folders ────────────────────────────────────────────────────────────────────

def list_folders() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, position, created_at, updated_at FROM folders ORDER BY position ASC, name ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_folder(folder_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, position, created_at, updated_at FROM folders WHERE id = ?",
            (folder_id,),
        ).fetchone()
    return dict(row) if row else None


def create_folder(name: str) -> dict:
    now = _now()
    with get_connection() as conn:
        max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) FROM folders").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO folders (name, position, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, max_pos + 1, now, now),
        )
        row = conn.execute(
            "SELECT id, name, position, created_at, updated_at FROM folders WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def update_folder(folder_id: int, name: str = None, position: int = None):
    fields, params = [], []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if position is not None:
        fields.append("position = ?")
        params.append(position)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(folder_id)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE folders SET {', '.join(fields)} WHERE id = ?", params
        )


def delete_folder(folder_id: int):
    """Delete a folder. Conversations inside it become un-grouped (folder_id → NULL)."""
    with get_connection() as conn:
        conn.execute("UPDATE conversations SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
        conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))


# ── Purge ──────────────────────────────────────────────────────────────────────

def purge_all_conversations() -> dict:
    """
    Delete every conversation, message, conv_file record, and linked_folder
    record. Settings and personas are left untouched.
    Returns a summary dict with counts of what was deleted.
    """
    import shutil

    with get_connection() as conn:
        file_count   = conn.execute("SELECT COUNT(*) FROM conv_files").fetchone()[0]
        msg_count    = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conv_count   = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        folder_count = conn.execute("SELECT COUNT(*) FROM linked_folders").fetchone()[0]

        # CASCADE deletes handle messages, conv_files, linked_folders automatically
        conn.execute("DELETE FROM conversations")

    # Wipe the entire uploads directory and recreate it empty.
    # This catches both DB-tracked files and any orphaned files left from
    # previous purges or manual deletions of DB records.
    uploads_root = os.path.join(os.path.dirname(__file__), "uploads")
    deleted_files = 0
    if os.path.isdir(uploads_root):
        for root, dirs, files in os.walk(uploads_root):
            deleted_files += len(files)
        shutil.rmtree(uploads_root)
    os.makedirs(uploads_root, exist_ok=True)

    return {
        "conversations":  conv_count,
        "messages":       msg_count,
        "files":          file_count,
        "files_deleted":  deleted_files,
        "linked_folders": folder_count,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
