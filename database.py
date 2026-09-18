from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from datetime import datetime, timezone
from cryptography.fernet import Fernet
import os
import base64
import hashlib
import json

def load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    os.environ[key] = val

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")

db = SQLAlchemy()


def _get_encryption_key():
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        key = Fernet.generate_key().decode('utf-8')
        os.environ["ENCRYPTION_KEY"] = key
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        try:
            if os.path.isfile(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if "ENCRYPTION_KEY" not in content:
                    with open(env_path, "a", encoding="utf-8") as f:
                        f.write(f"\n# Auto-generated secret key for API key encryption:\nENCRYPTION_KEY={key}\n")
        except Exception:
            pass
    return key.encode('utf-8')


_fernet_instance = None


def _get_fernet():
    global _fernet_instance
    if _fernet_instance is None:
        try:
            key = _get_encryption_key()
            _fernet_instance = Fernet(key)
        except Exception:
            raw_key = os.environ.get("ENCRYPTION_KEY", "fallback-default-key-safe")
            hashed = hashlib.sha256(raw_key.encode('utf-8')).digest()
            base64_key = base64.urlsafe_b64encode(hashed)
            _fernet_instance = Fernet(base64_key)
    return _fernet_instance


def encrypt_val(val: str) -> str:
    if not val:
        return ""
    f = _get_fernet()
    return f.encrypt(val.encode('utf-8')).decode('utf-8')


def decrypt_val(val: str) -> str:
    if not val:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(val.encode('utf-8')).decode('utf-8')
    except Exception:
        return val


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    # Only emit PRAGMAs for sqlite connections
    if type(dbapi_connection).__name__ == "Connection" or "sqlite" in str(type(dbapi_connection)):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
        except Exception:
            pass


# ── Models ────────────────────────────────────────────────────────────────────

class Folder(db.Model):
    __tablename__ = 'folders'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    archived = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.String(50), nullable=False)
    updated_at = db.Column(db.String(50), nullable=False)
    kind = db.Column(db.String(50), nullable=False, default='folder')
    workspace_dir = db.Column(db.String(512), nullable=False, default='')
    template_id = db.Column(db.String(100), nullable=False, default='')
    propagation_mode = db.Column(db.String(50), nullable=False, default='off')
    next_req_seq = db.Column(db.Integer, nullable=False, default=1)
    token_budget = db.Column(db.Integer, nullable=False, default=0)
    ba_conversation_id = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "position": self.position,
            "archived": self.archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "kind": getattr(self, "kind", "folder"),
            "workspace_dir": getattr(self, "workspace_dir", ""),
            "template_id": getattr(self, "template_id", ""),
            "propagation_mode": getattr(self, "propagation_mode", "off"),
            "next_req_seq": getattr(self, "next_req_seq", 1),
            "token_budget": getattr(self, "token_budget", 0),
            "ba_conversation_id": getattr(self, "ba_conversation_id", None),
        }


class Persona(db.Model):
    __tablename__ = 'personas'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(191), nullable=False, unique=True)
    prompt = db.Column(db.Text(length=16777215), nullable=False)
    created_at = db.Column(db.String(50), nullable=False)
    updated_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }


class Endpoint(db.Model):
    __tablename__ = 'endpoints'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    base_url = db.Column(db.String(512), nullable=False)
    api_key = db.Column(db.Text, nullable=False, default='')
    default_model = db.Column(db.String(255), nullable=False, default='')
    is_default = db.Column(db.Integer, nullable=False, default=0, index=True)
    model_filter = db.Column(db.String(512), nullable=False, default='')
    created_at = db.Column(db.String(50), nullable=False)
    updated_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key": decrypt_val(self.api_key),
            "default_model": self.default_model,
            "is_default": self.is_default,
            "model_filter": self.model_filter,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }


class Conversation(db.Model):
    __tablename__ = 'conversations'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(255), nullable=False, default='New Conversation')
    model_id = db.Column(db.String(255), nullable=False, default='')
    persona_id = db.Column(db.Integer, db.ForeignKey('personas.id', ondelete='SET NULL'), nullable=True)
    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoints.id', ondelete='SET NULL'), nullable=True)
    output_dir = db.Column(db.String(512), nullable=False, default='')
    enable_tools = db.Column(db.Integer, nullable=False, default=1)
    folder_id = db.Column(db.Integer, db.ForeignKey('folders.id', ondelete='SET NULL'), nullable=True, index=True)
    archived = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.String(50), nullable=False)
    updated_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "model_id": self.model_id,
            "persona_id": self.persona_id,
            "endpoint_id": self.endpoint_id,
            "output_dir": self.output_dir,
            "enable_tools": self.enable_tools,
            "folder_id": self.folder_id,
            "archived": self.archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }


class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    role = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text(length=16777215), nullable=False)
    tool_call_id = db.Column(db.String(255), nullable=True)
    tool_calls_json = db.Column(db.Text(length=16777215), nullable=True)
    created_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "tool_call_id": self.tool_call_id,
            "tool_calls_json": self.tool_calls_json,
            "created_at": self.created_at
        }


class Setting(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(191), primary_key=True)
    value = db.Column(db.Text(length=16777215), nullable=False)

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.value
        }


class TokenUsage(db.Model):
    __tablename__ = 'token_usage'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    prompt_tokens = db.Column(db.Integer, nullable=False, default=0)
    completion_tokens = db.Column(db.Integer, nullable=False, default=0)
    total_tokens = db.Column(db.Integer, nullable=False, default=0)
    estimated = db.Column(db.Integer, nullable=False, default=1)
    model_id = db.Column(db.String(255), nullable=False, default='')
    provider = db.Column(db.String(255), nullable=False, default='')
    tool_schema_tokens = db.Column(db.Integer, nullable=False, default=0)
    cached_prompt_tokens = db.Column(db.Integer, nullable=False, default=0)
    cost_usd = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated": bool(self.estimated),
            "model_id": self.model_id,
            "provider": self.provider,
            "tool_schema_tokens": self.tool_schema_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "cost_usd": self.cost_usd,
            "created_at": self.created_at,
        }


class ResearchSource(db.Model):
    __tablename__ = 'research_sources'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(1024), nullable=False)
    enabled = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.String(50), nullable=False)
    updated_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "enabled": bool(self.enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConvFile(db.Model):
    __tablename__ = 'conv_files'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    original_name = db.Column(db.String(512), nullable=False)
    disk_path = db.Column(db.String(512), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    char_count = db.Column(db.Integer, nullable=False, default=0)
    snippet = db.Column(db.Text(length=16777215), nullable=True)
    created_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "original_name": self.original_name,
            "disk_path": self.disk_path,
            "size_bytes": self.size_bytes,
            "char_count": self.char_count,
            "snippet": self.snippet,
            "created_at": self.created_at
        }


class LinkedFolder(db.Model):
    __tablename__ = 'linked_folders'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    folder_path = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "folder_path": self.folder_path,
            "created_at": self.created_at
        }


class Artifact(db.Model):
    __tablename__ = 'artifacts'
    __table_args__ = (
        db.UniqueConstraint('project_id', 'artifact_key', name='uq_artifact_project_key'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('folders.id', ondelete='CASCADE'), nullable=False, index=True)
    artifact_key = db.Column(db.String(100), nullable=False, index=True)
    role_persona_id = db.Column(db.Integer, db.ForeignKey('personas.id', ondelete='SET NULL'), nullable=True)
    rel_path = db.Column(db.String(512), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    content_hash = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(50), nullable=False, default='current')
    origin = db.Column(db.String(50), nullable=False, default='human')
    heading_index = db.Column(db.JSON, nullable=False, default=list)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "artifact_key": self.artifact_key,
            "role_persona_id": self.role_persona_id,
            "rel_path": self.rel_path,
            "version": self.version,
            "content_hash": self.content_hash,
            "status": self.status,
            "origin": self.origin,
            "heading_index": self.heading_index,
        }


class ArtifactDep(db.Model):
    __tablename__ = 'artifact_deps'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('folders.id', ondelete='CASCADE'), nullable=False, index=True)
    upstream_key = db.Column(db.String(100), nullable=False, index=True)
    downstream_key = db.Column(db.String(100), nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "upstream_key": self.upstream_key,
            "downstream_key": self.downstream_key,
        }


class ArtifactTrace(db.Model):
    __tablename__ = 'artifact_traces'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('folders.id', ondelete='CASCADE'), nullable=False, index=True)
    artifact_key = db.Column(db.String(100), nullable=False, index=True)
    req_id = db.Column(db.String(50), nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "artifact_key": self.artifact_key,
            "req_id": self.req_id,
        }


class ChangeEvent(db.Model):
    __tablename__ = 'change_events'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('folders.id', ondelete='CASCADE'), nullable=False, index=True)
    source_key = db.Column(db.String(100), nullable=False)
    from_version = db.Column(db.Integer, nullable=False)
    to_version = db.Column(db.Integer, nullable=False)
    summary = db.Column(db.Text, nullable=True)
    changed_reqs = db.Column(db.Text, nullable=True)
    removed_reqs = db.Column(db.Text, nullable=True)
    diff = db.Column(db.Text, nullable=True)
    origin = db.Column(db.String(50), nullable=False, default='human')
    created_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        import json
        changed_reqs = self.changed_reqs
        removed_reqs = self.removed_reqs
        try:
            if changed_reqs:
                changed_reqs = json.loads(changed_reqs)
        except Exception:
            pass
        try:
            if removed_reqs:
                removed_reqs = json.loads(removed_reqs)
        except Exception:
            pass
        return {
            "id": self.id,
            "project_id": self.project_id,
            "source_key": self.source_key,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "summary": self.summary,
            "changed_reqs": changed_reqs,
            "removed_reqs": removed_reqs,
            "diff": self.diff,
            "origin": self.origin,
            "created_at": self.created_at,
        }


class PropagationJob(db.Model):
    __tablename__ = 'propagation_jobs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    change_id = db.Column(db.Integer, db.ForeignKey('change_events.id', ondelete='CASCADE'), nullable=False, index=True)
    artifact_key = db.Column(db.String(100), nullable=False, index=True)
    persona_id = db.Column(db.Integer, db.ForeignKey('personas.id', ondelete='SET NULL'), nullable=True)
    state = db.Column(db.String(50), nullable=False, default='queued')
    depth = db.Column(db.Integer, nullable=False, default=0)
    batch_id = db.Column(db.String(100), nullable=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='SET NULL'), nullable=True)
    tokens_used = db.Column(db.Integer, nullable=False, default=0)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "change_id": self.change_id,
            "artifact_key": self.artifact_key,
            "persona_id": self.persona_id,
            "state": self.state,
            "depth": self.depth,
            "batch_id": self.batch_id,
            "conversation_id": self.conversation_id,
            "tokens_used": self.tokens_used,
            "attempts": self.attempts,
            "error": self.error,
            "created_at": self.created_at,
        }


class AgentIssue(db.Model):
    __tablename__ = 'agent_issues'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('folders.id', ondelete='CASCADE'), nullable=False, index=True)
    raised_by_key = db.Column(db.String(100), nullable=False, index=True)
    raised_by_persona_id = db.Column(db.Integer, db.ForeignKey('personas.id', ondelete='SET NULL'), nullable=True)
    change_id = db.Column(db.Integer, db.ForeignKey('change_events.id', ondelete='SET NULL'), nullable=True)
    depth = db.Column(db.Integer, nullable=False, default=0)
    kind = db.Column(db.String(50), nullable=False)  # question / risk / suggestion / conflict
    req_id = db.Column(db.String(50), nullable=True)
    blocking = db.Column(db.Integer, nullable=True, default=0)
    body = db.Column(db.Text, nullable=True)
    proposed_answer = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False, default='open')
    answer = db.Column(db.Text, nullable=True)
    answered_at = db.Column(db.String(50), nullable=True)
    stale_context = db.Column(db.Integer, nullable=True, default=0)
    digest_message_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "raised_by_key": self.raised_by_key,
            "raised_by_persona_id": self.raised_by_persona_id,
            "change_id": self.change_id,
            "depth": self.depth,
            "kind": self.kind,
            "req_id": self.req_id,
            "blocking": self.blocking,
            "body": self.body,
            "proposed_answer": self.proposed_answer,
            "status": self.status,
            "answer": self.answer,
            "answered_at": self.answered_at,
            "stale_context": self.stale_context,
            "digest_message_id": self.digest_message_id,
        }


class ArtifactAssumption(db.Model):
    __tablename__ = 'artifact_assumptions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('folders.id', ondelete='CASCADE'), nullable=False, index=True)
    artifact_key = db.Column(db.String(100), nullable=False, index=True)
    req_id = db.Column(db.String(50), nullable=False)
    marker_text = db.Column(db.Text, nullable=False)
    resolved = db.Column(db.Integer, nullable=False, default=0)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "artifact_key": self.artifact_key,
            "req_id": self.req_id,
            "marker_text": self.marker_text,
            "resolved": bool(self.resolved),
        }


class ArtifactRequest(db.Model):
    __tablename__ = 'artifact_requests'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('folders.id', ondelete='CASCADE'), nullable=False, index=True)
    persona_id = db.Column(db.Integer, db.ForeignKey('personas.id', ondelete='SET NULL'), nullable=True)
    artifact_key = db.Column(db.String(100), nullable=False, index=True)
    rel_path = db.Column(db.String(512), nullable=False)
    rationale = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False, default='pending')
    created_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "persona_id": self.persona_id,
            "artifact_key": self.artifact_key,
            "rel_path": self.rel_path,
            "rationale": self.rationale,
            "status": self.status,
        }


# ── Starter personas ───────────────────────────────────────────────────────────

# Bump this when STARTER_PERSONAS prompts change so existing installs can
# opt into the latest text via reset_starter_personas() without silently
# clobbering user-edited prompts (C29).
STARTER_PERSONAS_VERSION = "2"


def reset_starter_personas(force: bool = False) -> dict:
    """
    Reset starter personas to the latest seeded text (C29).

    Never clobbers user-edited prompts: a persona whose prompt has been edited
    is left alone unless ``force`` is True. Unedited seeded personas are
    recognisable because their ``created_at == updated_at``. Returns the
    personas updated and skipped.
    """
    updated = []
    skipped = []
    for name, prompt in STARTER_PERSONAS:
        persona = Persona.query.filter_by(name=name).first()
        if not persona:
            continue
        if persona.prompt == prompt:
            continue  # already the latest text
        if force or persona.created_at == persona.updated_at:
            persona.prompt = prompt
            persona.updated_at = _now()
            db.session.add(persona)
            updated.append(name)
        else:
            skipped.append(name)

    row = db.session.get(Setting, "starter_personas_version")
    if row:
        row.value = STARTER_PERSONAS_VERSION
    else:
        db.session.add(Setting(key="starter_personas_version", value=STARTER_PERSONAS_VERSION))
    db.session.commit()

    return {"updated": updated, "skipped": skipped}


def get_starter_personas_version() -> str:
    row = db.session.get(Setting, "starter_personas_version")
    return row.value if row else STARTER_PERSONAS_VERSION

STARTER_PERSONAS = [
    (
        "Business Analyst",
        "You are a senior Business Analyst. Focus on eliciting and documenting requirements, "
        "writing clear user stories and acceptance criteria, analysing business processes, "
        "identifying gaps and improvements, and communicating findings in plain language "
        "suitable for both technical and non-technical stakeholders. Use structured formats "
        "such as use-case tables, process flows, and RACI matrices where helpful.\n"
        "REQUIREMENTS CONTRACT: emit and NEVER renumber stable `REQ-nnn` IDs. Each "
        "requirement uses a heading the parser recognises, e.g. `## REQ-014 — Use SQLite "
        "and MySQL`. Do not merge, split or renumber existing IDs, and allocate new IDs "
        "from the numbers the project provides. Every downstream artifact traces to these IDs."
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
        "conversation focused on actionable next steps.\n"
        "PROPAGATION CONTRACT: during propagation you are NOT talking to the user directly — "
        "questions go to the BA inbox. Use `ask_question` for genuine ambiguity (blocking=true only "
        "when you cannot produce a correct artifact without an answer; blocking=false otherwise, writing "
        "your best guess with an inline assumption marker formatted exactly as "
        "`> **⚠️ ASSUMPTION (Q-nnnn):** <text> — Project Manager`). Use `raise_issue` for risks and "
        "suggestions, and `propose_artifact` when a new document is genuinely needed — never write an "
        "unregistered file. Maintain `derives_from` front-matter exactly and preserve unaffected "
        "sections verbatim in a whole-file rewrite."
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
        "and ask before modifying schemas, permissions, stored procedures, or data migration logic.\n"
        "FILE PREFIX: prefix every file you generate with `DB_`, e.g. `DB_data_model.md`.\n"
        "PROPAGATION CONTRACT: during propagation you are NOT talking to the user directly — "
        "questions go to the BA inbox. Use `ask_question` for genuine ambiguity (blocking=true only "
        "when you cannot produce a correct artifact without an answer; blocking=false otherwise, writing "
        "your best guess with an inline assumption marker formatted exactly as "
        "`> **⚠️ ASSUMPTION (Q-nnnn):** <text> — Database Developer`). Use `raise_issue` for risks and "
        "suggestions, and `propose_artifact` when a new document is genuinely needed — never write an "
        "unregistered file. Maintain `derives_from` front-matter exactly and preserve unaffected "
        "sections verbatim in a whole-file rewrite."
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
        "access control, production, or compliance-related configurations.\n"
        "FILE PREFIX: prefix every file you generate with `SEC_`, e.g. `SEC_risk_register.md`.\n"
        "PROPAGATION CONTRACT: during propagation you are NOT talking to the user directly — "
        "questions go to the BA inbox. Use `ask_question` for genuine ambiguity (blocking=true only "
        "when you cannot produce a correct artifact without an answer; blocking=false otherwise, writing "
        "your best guess with an inline assumption marker formatted exactly as "
        "`> **⚠️ ASSUMPTION (Q-nnnn):** <text> — DevSecOps Engineer`). Use `raise_issue` for risks and "
        "suggestions, and `propose_artifact` when a new document is genuinely needed — never write an "
        "unregistered file. Maintain `derives_from` front-matter exactly and preserve unaffected "
        "sections verbatim in a whole-file rewrite."
    ),
    (
        "Security Analyst",
        "You are a cybersecurity analyst. Assess threats and vulnerabilities, recommend "
        "mitigations, and explain security concepts clearly. Reference frameworks such as "
        "MITRE ATT&CK, OWASP Top 10, NIST, and CIS Controls where applicable. When reviewing "
        "code or architecture, identify attack surfaces and suggest defence-in-depth strategies. "
        "Always note relevant compliance considerations (GDPR, HIPAA, SOC2).\n"
        "PROPAGATION CONTRACT: during propagation you are NOT talking to the user directly — "
        "questions go to the BA inbox. Use `ask_question` for genuine ambiguity (blocking=true only "
        "when you cannot produce a correct artifact without an answer; blocking=false otherwise, writing "
        "your best guess with an inline assumption marker formatted exactly as "
        "`> **⚠️ ASSUMPTION (Q-nnnn):** <text> — Security Analyst`). Use `raise_issue` for risks and "
        "suggestions, and `propose_artifact` when a new document is genuinely needed — never write an "
        "unregistered file. Maintain `derives_from` front-matter exactly and preserve unaffected "
        "sections verbatim in a whole-file rewrite."
    ),
    (
        "UX Designer",
        "You are an experienced UX Designer. Help with user research planning, persona "
        "creation, wireframe descriptions, information architecture, and usability heuristics. "
        "Ground recommendations in accessibility standards (WCAG 2.1), Nielsen's heuristics, "
        "and evidence-based design principles. Describe layouts and interactions clearly "
        "in text, and suggest tools and methods appropriate to the project stage.\n"
        "PROPAGATION CONTRACT: during propagation you are NOT talking to the user directly — "
        "questions go to the BA inbox. Use `ask_question` for genuine ambiguity (blocking=true only "
        "when you cannot produce a correct artifact without an answer; blocking=false otherwise, writing "
        "your best guess with an inline assumption marker formatted exactly as "
        "`> **⚠️ ASSUMPTION (Q-nnnn):** <text> — UX Designer`). Use `raise_issue` for risks and "
        "suggestions, and `propose_artifact` when a new document is genuinely needed — never write an "
        "unregistered file. Maintain `derives_from` front-matter exactly and preserve unaffected "
        "sections verbatim in a whole-file rewrite."
    ),
]


def init_db(app=None):
    if app:
        with app.app_context():
            db.create_all()
            _ensure_archived_columns()
            _ensure_token_usage_table()
            _seed_personas_and_settings()
            _ensure_api_key_column_capacity()
            migrate_existing_api_keys()
            _ensure_repair_watermark()
    else:
        db.create_all()
        _ensure_archived_columns()
        _ensure_token_usage_table()
        _seed_personas_and_settings()
        _ensure_api_key_column_capacity()
        migrate_existing_api_keys()
        _ensure_repair_watermark()


def _ensure_token_usage_table():
    """Self-heal for databases that predate the Phase 6 token_usage table."""
    inspector = inspect(db.engine)
    if "token_usage" not in inspector.get_table_names():
        TokenUsage.__table__.create(db.engine)
        db.session.commit()
        return
    columns = {column["name"] for column in inspector.get_columns("token_usage")}
    additions = {
        "model_id": "VARCHAR(255) NOT NULL DEFAULT ''",
        "provider": "VARCHAR(255) NOT NULL DEFAULT ''",
        "tool_schema_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cached_prompt_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cost_usd": "FLOAT NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.session.execute(text(f"ALTER TABLE token_usage ADD COLUMN {name} {definition}"))
    db.session.commit()


def _ensure_repair_watermark():
    existing = db.session.get(Setting, "tool_call_repair_version")
    if not existing:
        db.session.add(Setting(key="tool_call_repair_version", value="1"))
        db.session.commit()


def _ensure_archived_columns():
    inspector = inspect(db.engine)
    
    # Check folders table
    columns = [c["name"] for c in inspector.get_columns("folders")]
    if "archived" not in columns:
        db.session.execute(text("ALTER TABLE folders ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"))
        db.session.commit()
        
    # Check conversations table
    columns = [c["name"] for c in inspector.get_columns("conversations")]
    if "archived" not in columns:
        db.session.execute(text("ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"))
        db.session.commit()


def _ensure_api_key_column_capacity():
    if db.engine.dialect.name in ("mysql", "mariadb"):
        columns = inspect(db.engine).get_columns("endpoints")
        api_key_column = next((column for column in columns if column["name"] == "api_key"), None)
        if api_key_column and "TEXT" not in str(api_key_column["type"]).upper():
            db.session.execute(text(
                "ALTER TABLE endpoints MODIFY COLUMN api_key TEXT NOT NULL"
            ))
            db.session.commit()


def migrate_existing_api_keys():
    endpoints = Endpoint.query.all()
    migrated = False
    for ep in endpoints:
        if ep.api_key:
            # If it already looks like a Fernet token, skip migrating it
            if ep.api_key.strip().startswith("gAAAAA"):
                continue
            # Check if it is plaintext
            decrypted = decrypt_val(ep.api_key)
            if decrypted == ep.api_key:
                # It is plaintext! Encrypt and save!
                ep.api_key = encrypt_val(ep.api_key)
                migrated = True
    if migrated:
        db.session.commit()


def _seed_personas_and_settings():
    # Settings default seeding
    for key, val in [('output_dir', ''), ('browser_root', ''), ('allow_local_file_access', '1')]:
        existing = db.session.get(Setting, key)
        if not existing:
            setting = Setting(key=key, value=val)
            db.session.add(setting)

    # Track the starter-persona version so edits are never silently clobbered.
    if not db.session.get(Setting, "starter_personas_version"):
        db.session.add(Setting(key="starter_personas_version", value=STARTER_PERSONAS_VERSION))

    # Personas seeding
    for name, prompt in STARTER_PERSONAS:
        existing = Persona.query.filter_by(name=name).first()
        if not existing:
            persona = Persona(name=name, prompt=prompt, created_at=_now(), updated_at=_now())
            db.session.add(persona)

    db.session.commit()


# ── Conversations ──────────────────────────────────────────────────────────────

def list_conversations():
    rows = Conversation.query.order_by(Conversation.updated_at.desc()).all()
    return [r.to_dict() for r in rows]


def create_conversation(title: str, model_id: str, persona_id: int = None, endpoint_id: int = None, folder_id: int = None) -> dict:
    now = _now()
    if endpoint_id is None:
        default_ep = get_default_endpoint()
        endpoint_id = default_ep["id"] if default_ep else None

    conv = Conversation(
        title=title,
        model_id=model_id,
        persona_id=persona_id,
        endpoint_id=endpoint_id,
        folder_id=folder_id,
        output_dir='',
        created_at=now,
        updated_at=now
    )
    db.session.add(conv)
    db.session.commit()
    return conv.to_dict()


def get_conversation(conversation_id: int) -> dict | None:
    conv = db.session.get(Conversation, conversation_id)
    return conv.to_dict() if conv else None


def update_conversation(conversation_id: int, title: str = None, model_id: str = None, persona_id: int = None, clear_persona: bool = False, output_dir: str = None, enable_tools: bool = None, endpoint_id: int = None, clear_endpoint: bool = False, folder_id: int = None, clear_folder: bool = False, archived: bool = None):
    conv = db.session.get(Conversation, conversation_id)
    if not conv:
        return

    if title is not None:
        conv.title = title
    if model_id is not None:
        conv.model_id = model_id
    if persona_id is not None:
        conv.persona_id = persona_id
    elif clear_persona:
        conv.persona_id = None

    if endpoint_id is not None:
        conv.endpoint_id = endpoint_id
    elif clear_endpoint:
        conv.endpoint_id = None

    if folder_id is not None:
        conv.folder_id = folder_id
    elif clear_folder:
        conv.folder_id = None

    if output_dir is not None:
        conv.output_dir = output_dir
    if enable_tools is not None:
        conv.enable_tools = 1 if enable_tools else 0

    if archived is not None:
        conv.archived = 1 if archived else 0
        if not archived and conv.folder_id:
            f = db.session.get(Folder, conv.folder_id)
            if f and f.archived == 1:
                conv.folder_id = None

    conv.updated_at = _now()
    db.session.commit()


def delete_conversation(conversation_id: int):
    conv = db.session.get(Conversation, conversation_id)
    if conv:
        db.session.delete(conv)
        db.session.commit()


def touch_conversation(conversation_id: int):
    conv = db.session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = _now()
        db.session.commit()


# ── Messages ───────────────────────────────────────────────────────────────────

def get_messages(conversation_id: int) -> list[dict]:
    rows = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.id.asc()).all()
    return [r.to_dict() for r in rows]


def add_message(
    conversation_id: int,
    role: str,
    content: str,
    tool_call_id: str = None,
    tool_calls_json: str = None,
) -> dict:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_call_id=tool_call_id,
        tool_calls_json=tool_calls_json,
        created_at=_now()
    )
    db.session.add(msg)
    db.session.commit()
    return msg.to_dict()


# ── Token accounting (Phase 6) ─────────────────────────────────────────────────

def record_token_usage(
    conversation_id: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    estimated: bool = True,
    model_id: str = "",
    provider: str = "",
    tool_schema_tokens: int = 0,
    cached_prompt_tokens: int = 0,
    cost_usd: float = 0.0,
) -> dict:
    """Persist one model call's token usage for a conversation."""
    tu = TokenUsage(
        conversation_id=conversation_id,
        prompt_tokens=int(prompt_tokens or 0),
        completion_tokens=int(completion_tokens or 0),
        total_tokens=int(total_tokens or 0),
        estimated=1 if estimated else 0,
        model_id=model_id or "",
        provider=provider or "",
        tool_schema_tokens=int(tool_schema_tokens or 0),
        cached_prompt_tokens=int(cached_prompt_tokens or 0),
        cost_usd=float(cost_usd or 0),
        created_at=_now(),
    )
    db.session.add(tu)
    db.session.commit()
    return tu.to_dict()


def last_token_usage(conversation_id: int) -> dict | None:
    """Return the most recent recorded usage row for a conversation."""
    row = TokenUsage.query.filter_by(conversation_id=conversation_id)\
        .order_by(TokenUsage.id.desc()).first()
    return row.to_dict() if row else None


# ── Settings ───────────────────────────────────────────────────────────────────

def get_setting(key: str) -> str | None:
    setting = db.session.get(Setting, key)
    return setting.value if setting else None


def local_file_access_enabled() -> bool:
    """Global lock: when off, the model may only touch files uploaded to the
    conversation (read_named_file / uploads) — no local paths, no disk writes,
    and no propagation/scan runs. Defaults to enabled (secure default) even if
    called outside an app context (e.g. import-time or test helpers)."""
    try:
        value = get_setting("allow_local_file_access")
        return value != "0"
    except Exception:
        return True


def set_setting(key: str, value: str):
    setting = db.session.get(Setting, key)
    if setting:
        setting.value = value
    else:
        setting = Setting(key=key, value=value)
        db.session.add(setting)
    db.session.commit()


def reset_settings() -> dict:
    for key in ("output_dir", "browser_root", "allow_local_file_access"):
        setting = db.session.get(Setting, key)
        if setting:
            setting.value = "1" if key == "allow_local_file_access" else ""
        else:
            setting = Setting(key=key, value="1" if key == "allow_local_file_access" else "")
            db.session.add(setting)

    endpoints = Endpoint.query.all()
    for ep in endpoints:
        ep.default_model = ""

    db.session.commit()
    return {
        "output_dir": "",
        "browser_root": "",
    }


# ── Research sources ─────────────────────────────────────────────────────────

def list_research_sources() -> list[dict]:
    rows = ResearchSource.query.order_by(ResearchSource.name.asc()).all()
    return [r.to_dict() for r in rows]


def get_research_source(source_id: int) -> dict | None:
    source = db.session.get(ResearchSource, source_id)
    return source.to_dict() if source else None


def create_research_source(name: str, url: str, enabled: bool = True) -> dict:
    now = _now()
    source = ResearchSource(
        name=name, url=url, enabled=1 if enabled else 0,
        created_at=now, updated_at=now,
    )
    db.session.add(source)
    db.session.commit()
    return source.to_dict()


def update_research_source(source_id: int, name: str = None, url: str = None,
                           enabled: bool = None) -> dict | None:
    source = db.session.get(ResearchSource, source_id)
    if not source:
        return None
    if name is not None:
        source.name = name
    if url is not None:
        source.url = url
    if enabled is not None:
        source.enabled = 1 if enabled else 0
    source.updated_at = _now()
    db.session.commit()
    return source.to_dict()


def delete_research_source(source_id: int):
    source = db.session.get(ResearchSource, source_id)
    if source:
        db.session.delete(source)
        db.session.commit()


# ── Endpoints ──────────────────────────────────────────────────────────────────

def list_endpoints() -> list[dict]:
    rows = Endpoint.query.order_by(Endpoint.is_default.desc(), Endpoint.name.asc()).all()
    return [r.to_dict() for r in rows]


def get_endpoint(endpoint_id: int) -> dict | None:
    ep = db.session.get(Endpoint, endpoint_id)
    return ep.to_dict() if ep else None


def get_default_endpoint() -> dict | None:
    ep = Endpoint.query.filter_by(is_default=1).order_by(Endpoint.updated_at.desc()).first()
    if ep is None:
        ep = Endpoint.query.order_by(Endpoint.id.asc()).first()
    return ep.to_dict() if ep else None


def create_endpoint(name: str, base_url: str, api_key: str = "", default_model: str = "", is_default: bool = False, model_filter: str = "") -> dict:
    now = _now()
    if is_default:
        Endpoint.query.update({Endpoint.is_default: 0})

    ep = Endpoint(
        name=name,
        base_url=base_url,
        api_key=encrypt_val(api_key),
        default_model=default_model,
        is_default=1 if is_default else 0,
        model_filter=model_filter,
        created_at=now,
        updated_at=now
    )
    db.session.add(ep)
    db.session.commit()

    total = Endpoint.query.count()
    if total == 1:
        ep.is_default = 1
        db.session.commit()

    return ep.to_dict()


def update_endpoint(endpoint_id: int, name: str = None, base_url: str = None,
                    api_key: str = None, default_model: str = None, is_default: bool = None, model_filter: str = None) -> dict | None:
    ep = db.session.get(Endpoint, endpoint_id)
    if not ep:
        return None

    if name is not None:
        ep.name = name
    if base_url is not None:
        ep.base_url = base_url
    if api_key is not None:
        ep.api_key = encrypt_val(api_key)
    if default_model is not None:
        ep.default_model = default_model
    if model_filter is not None:
        ep.model_filter = model_filter

    if is_default is True:
        Endpoint.query.filter(Endpoint.id != endpoint_id).update({Endpoint.is_default: 0})
        ep.is_default = 1
    elif is_default is False:
        ep.is_default = 0

    ep.updated_at = _now()
    db.session.commit()
    return ep.to_dict()


def delete_endpoint(endpoint_id: int):
    ep = db.session.get(Endpoint, endpoint_id)
    if not ep:
        return

    was_default = ep.is_default == 1
    db.session.delete(ep)
    db.session.commit()

    if was_default:
        nxt = Endpoint.query.order_by(Endpoint.id.asc()).first()
        if nxt:
            nxt.is_default = 1
            db.session.commit()


# ── Personas ───────────────────────────────────────────────────────────────────

def list_personas() -> list[dict]:
    rows = Persona.query.order_by(Persona.name.asc()).all()
    return [r.to_dict() for r in rows]


def get_persona(persona_id: int) -> dict | None:
    p = db.session.get(Persona, persona_id)
    return p.to_dict() if p else None


def create_persona(name: str, prompt: str) -> dict:
    now = _now()
    p = Persona(name=name, prompt=prompt, created_at=now, updated_at=now)
    db.session.add(p)
    db.session.commit()
    return p.to_dict()


def update_persona(persona_id: int, name: str = None, prompt: str = None):
    p = db.session.get(Persona, persona_id)
    if not p:
        return
    if name is not None:
        p.name = name
    if prompt is not None:
        p.prompt = prompt
    p.updated_at = _now()
    db.session.commit()


def delete_persona(persona_id: int):
    p = db.session.get(Persona, persona_id)
    if p:
        db.session.delete(p)
        db.session.commit()


# ── Conversation files ─────────────────────────────────────────────────────────

def add_conv_file(conversation_id: int, original_name: str, disk_path: str,
                  size_bytes: int, char_count: int, snippet: str = "") -> dict:
    cf = ConvFile(
        conversation_id=conversation_id,
        original_name=original_name,
        disk_path=disk_path,
        size_bytes=size_bytes,
        char_count=char_count,
        snippet=snippet,
        created_at=_now()
    )
    db.session.add(cf)
    db.session.commit()
    return cf.to_dict()


def list_conv_files(conversation_id: int) -> list[dict]:
    rows = ConvFile.query.filter_by(conversation_id=conversation_id).order_by(ConvFile.created_at.asc()).all()
    return [r.to_dict() for r in rows]


def get_conv_file(file_id: int) -> dict | None:
    cf = db.session.get(ConvFile, file_id)
    return cf.to_dict() if cf else None


def delete_conv_file(file_id: int):
    cf = db.session.get(ConvFile, file_id)
    if cf:
        db.session.delete(cf)
        db.session.commit()


def update_conv_file(
    file_id: int,
    size_bytes: int = None,
    char_count: int = None,
    snippet: str = None,
    original_name: str = None,
) -> dict | None:
    """Update an existing conversation-upload record (used by editor saves so
    editing a generated Markdown file never creates a duplicate upload row)."""
    cf = db.session.get(ConvFile, file_id)
    if not cf:
        return None
    if size_bytes is not None:
        cf.size_bytes = int(size_bytes)
    if char_count is not None:
        cf.char_count = int(char_count)
    if snippet is not None:
        cf.snippet = snippet
    if original_name is not None:
        cf.original_name = original_name
    db.session.commit()
    return cf.to_dict()


# ── Recent document-edit marker (token-efficient model integration) ──────────

RECENT_EDIT_TTL_SECONDS = 600


def mark_recent_document_edit(conv_id: int, name: str, kind: str) -> None:
    """Remember that the user just edited ``name`` for this conversation so the
    next context build can add a short note without re-injecting the file."""
    import time
    row = db.session.get(Setting, f"doc_edit_{conv_id}")
    payload = {
        "name": name,
        "kind": kind,
        "ts": int(time.time()),
    }
    if row:
        row.value = json.dumps(payload)
    else:
        db.session.add(Setting(key=f"doc_edit_{conv_id}", value=json.dumps(payload)))
    db.session.commit()


def get_recent_document_edit_note(conv_id: int) -> str:
    """
    Return a one-line context note when the user recently edited a document in
    this conversation; empty otherwise. The note tells the model how to pull the
    current contents without paying for the whole file on every prompt.
    """
    import time
    try:
        row = db.session.get(Setting, f"doc_edit_{conv_id}")
        if not row or not row.value:
            return ""
        payload = json.loads(row.value)
        name = payload.get("name") or ""
        kind = payload.get("kind") or ""
        ts = payload.get("ts") or 0
    except Exception:
        return ""
    if not name or (time.time() - ts) > RECENT_EDIT_TTL_SECONDS:
        return ""
    if kind == "upload":
        tool_hint = "Use the read_named_file tool if you need its complete current contents."
    else:
        tool_hint = "Use the read_file tool if you need its complete current contents."
    return (
        f"The user edited {name} since the previous turn. "
        f"{tool_hint} See the file index above for its size, hash and preview."
    )


def delete_last_assistant_turn(conversation_id: int):
    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.id.asc()).all()
    if not messages:
        return

    cut_from_id = None
    for msg in reversed(messages):
        if msg.role == 'assistant':
            cut_from_id = msg.id
            break

    if cut_from_id is None:
        return

    Message.query.filter(Message.conversation_id == conversation_id, Message.id >= cut_from_id).delete()
    db.session.commit()


def compact_conversation(conversation_id: int, keep_recent: int = 20) -> dict:
    """
    Compact a conversation's history to reduce the tokens re-sent to the model.

    Keeps the opening turn (the first user message, which carries the task
    context) plus the final ``keep_recent`` messages verbatim — those still
    reference each other (assistant tool_calls ↔ tool results), so the API
    history stays valid. Everything in between is deleted outright.

    Returns:
        {"deleted": int, "kept": int}
    """
    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.id.asc()).all()
    if not messages:
        return {"deleted": 0, "kept": 0}

    keep_ids: set[int] = set()
    for m in messages:
        keep_ids.add(m.id)
        if m.role == "user":
            break
    for m in messages[-keep_recent:]:
        keep_ids.add(m.id)

    to_delete = [m for m in messages if m.id not in keep_ids]
    if not to_delete:
        return {"deleted": 0, "kept": len(messages)}

    ids = [m.id for m in to_delete]
    Message.query.filter(
        Message.conversation_id == conversation_id,
        Message.id.in_(ids),
    ).delete(synchronize_session=False)
    db.session.commit()
    return {"deleted": len(ids), "kept": len(keep_ids)}


def edit_message_and_truncate(conversation_id: int, message_id: int, new_content: str):
    msg = db.session.get(Message, message_id)
    if msg and msg.conversation_id == conversation_id:
        msg.content = new_content
        Message.query.filter(Message.conversation_id == conversation_id, Message.id > message_id).delete()
        db.session.commit()


def search_all_conversations(query: str) -> list[dict]:
    rows = db.session.query(Conversation.id, Conversation.title, Conversation.updated_at, Message.content)\
        .join(Message, Message.conversation_id == Conversation.id)\
        .filter(Conversation.archived != 1)\
        .filter(Message.content.ilike(f"%{query}%"))\
        .distinct()\
        .order_by(Conversation.updated_at.desc())\
        .limit(50).all()

    results = []
    for row in rows:
        d = {
            "id": row[0],
            "title": row[1],
            "updated_at": row[2],
            "snippet": row[3]
        }
        content = d["snippet"] or ""
        idx = content.lower().find(query.lower())
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(content), idx + 120)
            d["snippet"] = ("…" if start > 0 else "") + content[start:end] + ("…" if end < len(content) else "")
        results.append(d)
    return results


# ── Linked folders ─────────────────────────────────────────────────────────────

def add_linked_folder(conversation_id: int, folder_path: str) -> dict:
    existing = LinkedFolder.query.filter_by(conversation_id=conversation_id, folder_path=folder_path).first()
    if existing:
        return existing.to_dict()

    lf = LinkedFolder(
        conversation_id=conversation_id,
        folder_path=folder_path,
        created_at=_now()
    )
    db.session.add(lf)
    db.session.commit()
    return lf.to_dict()


def list_linked_folders(conversation_id: int) -> list[dict]:
    rows = LinkedFolder.query.filter_by(conversation_id=conversation_id).order_by(LinkedFolder.created_at.asc()).all()
    return [r.to_dict() for r in rows]


def get_linked_folder(folder_id: int) -> dict | None:
    lf = db.session.get(LinkedFolder, folder_id)
    return lf.to_dict() if lf else None


def delete_linked_folder(folder_id: int):
    lf = db.session.get(LinkedFolder, folder_id)
    if lf:
        db.session.delete(lf)
        db.session.commit()


# ── Folders ────────────────────────────────────────────────────────────────────

def list_folders() -> list[dict]:
    rows = Folder.query.order_by(Folder.position.asc(), Folder.name.asc()).all()
    return [r.to_dict() for r in rows]


def get_folder(folder_id: int) -> dict | None:
    f = db.session.get(Folder, folder_id)
    return f.to_dict() if f else None


def create_folder(name: str) -> dict:
    now = _now()
    max_pos = db.session.query(db.func.max(Folder.position)).scalar()
    if max_pos is None:
        max_pos = -1

    f = Folder(name=name, position=max_pos + 1, created_at=now, updated_at=now)
    db.session.add(f)
    db.session.commit()
    return f.to_dict()


def create_project_team(name: str, template_id: str = "sdlc", workspace_dir: str | None = None) -> dict:
    """Create a managed project folder with a full team of role conversations.

    Replaces the legacy ``create_code_folder``. Builds a git-tracked workspace
    scaffolded from ``PROJECT_TEMPLATES`` (D8/D9), registers the artifacts and
    dependency edges, then creates one conversation per role — persona set,
    linked to the workspace root, output directory pointed at the role's
    sub-folder. The Business Analyst conversation is designated as the
    project's single Q&A inbox (D19 / §5.4).

    ``workspace_dir`` may be supplied explicitly; otherwise it is derived from
    the configured output folder + project name.

    Returns:
        A dict with the project folder, the created conversations, and the
        registration summary from ``register_project_from_template``.
    """
    import os
    from templates import get_template

    template = get_template(template_id)
    if not template:
        raise ValueError(f"Unknown template: {template_id}")

    if workspace_dir:
        project_dir = workspace_dir
    else:
        # Validate the output folder up front so we never leak a plain folder on failure.
        output_dir = get_setting("output_dir") or ""
        if not output_dir:
            raise ValueError("Default output folder is not configured. Please set it in Settings.")
        output_dir = os.path.expanduser(output_dir.strip())
        if not os.path.isdir(output_dir):
            raise ValueError(f"Default output folder does not exist: {output_dir}")
        project_dir = os.path.join(output_dir, name)

    folder = create_folder(name)
    folder_id = folder["id"]
    summary = register_project_from_template(folder_id, template_id, project_dir)

    # One conversation per role: persona, linked to the workspace root, output
    # dir pointed at that role's sub-folder (derived from the template paths).
    persona_by_name = {p.name: p.id for p in Persona.query.all()}
    conversations = []
    ba_conversation_id = None
    seen_roles = set()
    for spec in template.artifacts:
        role = spec.role
        if role in seen_roles:
            continue
        seen_roles.add(role)

        sub_dir = os.path.join(project_dir, os.path.dirname(spec.rel_path))
        os.makedirs(sub_dir, exist_ok=True)

        persona_name = template.role_to_persona.get(role)
        persona_id = persona_by_name.get(persona_name)
        conv = create_conversation(
            title=role,
            model_id="",
            persona_id=persona_id,
            folder_id=folder_id,
        )
        conv_id = conv["id"]

        # Link the conversation to the workspace root and point its output
        # directory at the role's sub-folder.
        add_linked_folder(conv_id, project_dir)
        update_conversation(conv_id, output_dir=sub_dir)
        conversations.append(get_conversation(conv_id))

        if role == "Business Analyst" and ba_conversation_id is None:
            ba_conversation_id = conv_id

    # Designate the BA conversation as the Q&A inbox (D19).
    if ba_conversation_id is not None:
        update_folder_project(folder_id, ba_conversation_id=ba_conversation_id)

    return {
        "project": get_folder(folder_id),
        "conversations": conversations,
        "workspace_dir": project_dir,
        "ba_conversation_id": ba_conversation_id,
        "artifacts_registered": summary["artifacts_registered"],
        "edges_created": summary["edges_created"],
    }


def update_folder(folder_id: int, name: str = None, position: int = None, archived: bool = None):
    f = db.session.get(Folder, folder_id)
    if not f:
        return
    if name is not None:
        f.name = name
    if position is not None:
        f.position = position
    if archived is not None:
        f.archived = 1 if archived else 0
        Conversation.query.filter_by(folder_id=folder_id).update({Conversation.archived: 1 if archived else 0})
    f.updated_at = _now()
    db.session.commit()


def delete_folder(folder_id: int):
    Conversation.query.filter_by(folder_id=folder_id).update({Conversation.folder_id: None})
    f = db.session.get(Folder, folder_id)
    if f:
        db.session.delete(f)
        db.session.commit()


# ── Purge ──────────────────────────────────────────────────────────────────────

def purge_all_conversations() -> dict:
    import shutil

    file_count = ConvFile.query.count()
    msg_count = Message.query.count()
    conv_count = Conversation.query.count()
    folder_count = LinkedFolder.query.count()

    # Cascade deletes handle messages, conv_files, linked_folders automatically
    Conversation.query.delete()
    db.session.commit()

    uploads_root = os.path.join(os.path.dirname(__file__), "uploads")
    deleted_files = 0
    if os.path.isdir(uploads_root):
        for root, dirs, files in os.walk(uploads_root):
            deleted_files += len(files)
        shutil.rmtree(uploads_root)
    os.makedirs(uploads_root, exist_ok=True)

    return {
        "conversations": conv_count,
        "messages": msg_count,
        "files": file_count,
        "files_deleted": deleted_files,
        "linked_folders": folder_count,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Artifacts ────────────────────────────────────────────────────────────────────

def list_artifacts(project_id: int) -> list[dict]:
    rows = Artifact.query.filter_by(project_id=project_id).order_by(Artifact.artifact_key.asc()).all()
    return [r.to_dict() for r in rows]


def get_artifact(project_id: int, artifact_key: str) -> dict | None:
    row = Artifact.query.filter_by(project_id=project_id, artifact_key=artifact_key).first()
    return row.to_dict() if row else None


def create_artifact(
    project_id: int,
    artifact_key: str,
    rel_path: str,
    role_persona_id: int | None = None,
    version: int = 1,
    content_hash: str = "",
    status: str = "current",
    origin: str = "human",
) -> dict:
    artifact = Artifact(
        project_id=project_id,
        artifact_key=artifact_key,
        role_persona_id=role_persona_id,
        rel_path=rel_path,
        version=version,
        content_hash=content_hash,
        status=status,
        origin=origin,
    )
    db.session.add(artifact)
    db.session.commit()
    return artifact.to_dict()


def delete_artifact(project_id: int, artifact_key: str) -> None:
    artifact = db.session.query(Artifact).filter_by(project_id=project_id, artifact_key=artifact_key).first()
    if artifact:
        db.session.delete(artifact)
        db.session.commit()


# ── Artifact Dependencies ──────────────────────────────────────────────────────

def create_artifact_dep(project_id: int, upstream_key: str, downstream_key: str) -> dict:
    dep = ArtifactDep(
        project_id=project_id,
        upstream_key=upstream_key,
        downstream_key=downstream_key,
    )
    db.session.add(dep)
    db.session.commit()
    return dep.to_dict()


def list_artifact_deps(project_id: int) -> list[dict]:
    rows = ArtifactDep.query.filter_by(project_id=project_id).order_by(ArtifactDep.upstream_key.asc(), ArtifactDep.downstream_key.asc()).all()
    return [r.to_dict() for r in rows]


def delete_artifact_deps(project_id: int, upstream_key: str | None = None, downstream_key: str | None = None) -> None:
    query = ArtifactDep.query.filter_by(project_id=project_id)
    if upstream_key is not None:
        query = query.filter_by(upstream_key=upstream_key)
    if downstream_key is not None:
        query = query.filter_by(downstream_key=downstream_key)
    query.delete(synchronize_session=False)
    db.session.commit()


# ── Artifact Traces ────────────────────────────────────────────────────────────

def create_artifact_trace(project_id: int, artifact_key: str, req_id: str) -> dict:
    trace = ArtifactTrace(
        project_id=project_id,
        artifact_key=artifact_key,
        req_id=req_id,
    )
    db.session.add(trace)
    db.session.commit()
    return trace.to_dict()


def list_artifact_traces(project_id: int, artifact_key: str | None = None, req_id: str | None = None) -> list[dict]:
    query = ArtifactTrace.query.filter_by(project_id=project_id)
    if artifact_key:
        query = query.filter_by(artifact_key=artifact_key)
    if req_id:
        query = query.filter_by(req_id=req_id)
    rows = query.order_by(ArtifactTrace.artifact_key.asc(), ArtifactTrace.req_id.asc()).all()
    return [r.to_dict() for r in rows]


def update_artifact(project_id: int, artifact_key: str, **updates) -> dict:
    artifact = db.session.query(Artifact).filter_by(project_id=project_id, artifact_key=artifact_key).first()
    if not artifact:
        return None
    for key, value in updates.items():
        setattr(artifact, key, value)
    db.session.commit()
    return artifact.to_dict()


# ── Change Events ────────────────────────────────────────────────────────────────

def create_change_event(
    project_id: int,
    source_key: str,
    from_version: int,
    to_version: int,
    summary: str | None = None,
    changed_reqs: list | None = None,
    removed_reqs: list | None = None,
    diff: str | None = None,
    origin: str = "human",
) -> dict:
    # Use max id + 1 for simplicity, or get from DB
    from sqlalchemy import func
    max_id = db.session.query(func.max(ChangeEvent.id)).scalar()
    new_id = (max_id or 0) + 1

    changed_reqs_json = json.dumps(changed_reqs) if changed_reqs else None
    removed_reqs_json = json.dumps(removed_reqs) if removed_reqs else None

    event = ChangeEvent(
        id=new_id,
        project_id=project_id,
        source_key=source_key,
        from_version=from_version,
        to_version=to_version,
        summary=summary,
        changed_reqs=changed_reqs_json,
        removed_reqs=removed_reqs_json,
        diff=diff,
        origin=origin,
        created_at=_now(),
    )
    db.session.add(event)
    db.session.commit()
    return event.to_dict()


def list_change_events(project_id: int) -> list[dict]:
    rows = ChangeEvent.query.filter_by(project_id=project_id).order_by(ChangeEvent.created_at.desc()).all()
    return [r.to_dict() for r in rows]


def update_change_event(change_id: int, **updates) -> dict | None:
    event = db.session.get(ChangeEvent, change_id)
    if not event:
        return None
    for key, value in updates.items():
        setattr(event, key, value)
    db.session.commit()
    return event.to_dict()


# ── Propagation Jobs ────────────────────────────────────────────────────────────

def create_propagation_job(
    change_id: int,
    artifact_key: str,
    persona_id: int | None,
    depth: int,
    batch_id: str | None = None,
    conversation_id: int | None = None,
) -> dict:
    from sqlalchemy import func
    max_id = db.session.query(func.max(PropagationJob.id)).scalar()
    new_id = (max_id or 0) + 1

    job = PropagationJob(
        id=new_id,
        change_id=change_id,
        artifact_key=artifact_key,
        persona_id=persona_id,
        state="pending",
        depth=depth,
        batch_id=batch_id,
        conversation_id=conversation_id,
        tokens_used=0,
        attempts=0,
        created_at=_now(),
    )
    db.session.add(job)
    db.session.commit()
    return job.to_dict()


def list_propagation_jobs(change_id: int) -> list[dict]:
    rows = PropagationJob.query.filter_by(change_id=change_id).order_by(PropagationJob.depth.asc(), PropagationJob.artifact_key.asc()).all()
    return [r.to_dict() for r in rows]


def update_propagation_job(job_id: int, **updates) -> dict | None:
    job = db.session.get(PropagationJob, job_id)
    if not job:
        return None
    for key, value in updates.items():
        setattr(job, key, value)
    db.session.commit()
    return job.to_dict()


# ── Agent Issues ─────────────────────────────────────────────────────────────────

def create_agent_issue(
    project_id: int,
    raised_by_key: str,
    raised_by_persona_id: int | None,
    change_id: int | None,
    depth: int,
    kind: str,  # question / risk / suggestion / conflict
    req_id: str | None = None,
    blocking: int | None = None,
    body: str | None = None,
    proposed_answer: str | None = None,
) -> dict:
    from sqlalchemy import func
    max_id = db.session.query(func.max(AgentIssue.id)).scalar()
    new_id = (max_id or 0) + 1

    issue = AgentIssue(
        id=new_id,
        project_id=project_id,
        raised_by_key=raised_by_key,
        raised_by_persona_id=raised_by_persona_id,
        change_id=change_id,
        depth=depth,
        kind=kind,
        req_id=req_id,
        blocking=blocking,
        body=body,
        proposed_answer=proposed_answer,
        status="open",
        answered_at=None,
        stale_context=0,
        digest_message_id=None,
        created_at=_now(),
    )
    db.session.add(issue)
    db.session.commit()
    return issue.to_dict()


def list_agent_issues(
    project_id: int,
    kind: str | None = None,
    status: str | None = None,
    blocking: int | None = None,
) -> list[dict]:
    query = AgentIssue.query.filter_by(project_id=project_id)
    if kind:
        query = query.filter_by(kind=kind)
    if status:
        query = query.filter_by(status=status)
    if blocking is not None:
        query = query.filter(AgentIssue.blocking == blocking)
    rows = query.order_by(AgentIssue.created_at.desc()).all()
    return [r.to_dict() for r in rows]


def update_agent_issue(issue_id: int, **updates) -> dict | None:
    issue = db.session.get(AgentIssue, issue_id)
    if not issue:
        return None
    for key, value in updates.items():
        setattr(issue, key, value)
    if issue.status == "answered" and issue.answered_at is None:
        issue.answered_at = _now()
    db.session.commit()
    return issue.to_dict()


# ── Artifact Assumptions ────────────────────────────────────────────────────────

def create_artifact_assumption(
    project_id: int,
    artifact_key: str,
    req_id: str,
    marker_text: str,
) -> dict:
    from sqlalchemy import func
    max_id = db.session.query(func.max(ArtifactAssumption.id)).scalar()
    new_id = (max_id or 0) + 1

    assumption = ArtifactAssumption(
        id=new_id,
        project_id=project_id,
        artifact_key=artifact_key,
        req_id=req_id,
        marker_text=marker_text,
        resolved=0,
    )
    db.session.add(assumption)
    db.session.commit()
    return assumption.to_dict()


def list_artifact_assumptions(project_id: int, artifact_key: str | None = None) -> list[dict]:
    query = ArtifactAssumption.query.filter_by(project_id=project_id)
    if artifact_key:
        query = query.filter_by(artifact_key=artifact_key)
    rows = query.order_by(ArtifactAssumption.req_id.asc()).all()
    return [r.to_dict() for r in rows]


def update_artifact_assumption(assumption_id: int, **updates) -> dict | None:
    assumption = db.session.get(ArtifactAssumption, assumption_id)
    if not assumption:
        return None
    for key, value in updates.items():
        setattr(assumption, key, value)
    db.session.commit()
    return assumption.to_dict()


# ── Artifact Requests ───────────────────────────────────────────────────────────

def create_artifact_request(
    project_id: int,
    persona_id: int | None,
    artifact_key: str,
    rel_path: str,
    rationale: str | None = None,
) -> dict:
    from sqlalchemy import func
    max_id = db.session.query(func.max(ArtifactRequest.id)).scalar()
    new_id = (max_id or 0) + 1

    request = ArtifactRequest(
        id=new_id,
        project_id=project_id,
        persona_id=persona_id,
        artifact_key=artifact_key,
        rel_path=rel_path,
        rationale=rationale,
        status="pending",
        created_at=_now(),
    )
    db.session.add(request)
    db.session.commit()
    return request.to_dict()


def list_artifact_requests(
    project_id: int,
    artifact_key: str | None = None,
    status: str | None = None,
) -> list[dict]:
    query = ArtifactRequest.query.filter_by(project_id=project_id)
    if artifact_key:
        query = query.filter_by(artifact_key=artifact_key)
    if status:
        query = query.filter_by(status=status)
    rows = query.order_by(ArtifactRequest.created_at.desc()).all()
    return [r.to_dict() for r in rows]


def update_artifact_request(request_id: int, status: str = None, **updates) -> dict | None:
    request = db.session.get(ArtifactRequest, request_id)
    if not request:
        return None
    if status is not None:
        request.status = status
    for key, value in updates.items():
        setattr(request, key, value)
    db.session.commit()
    return request.to_dict()


# ── Folder Project Setup ────────────────────────────────────────────────────────

def update_folder_project(
    project_id: int,
    workspace_dir: str = None,
    kind: str = None,
    template_id: str = None,
    propagation_mode: str = None,
    next_req_seq: int = None,
    token_budget: int = None,
    ba_conversation_id: int = None,
) -> dict:
    f = db.session.get(Folder, project_id)
    if not f:
        return None
    if workspace_dir is not None:
        f.workspace_dir = workspace_dir
    if kind is not None:
        f.kind = kind
    if template_id is not None:
        f.template_id = template_id
    if propagation_mode is not None:
        f.propagation_mode = propagation_mode
    if next_req_seq is not None:
        f.next_req_seq = next_req_seq
    if token_budget is not None:
        f.token_budget = token_budget
    if ba_conversation_id is not None:
        f.ba_conversation_id = ba_conversation_id
    f.updated_at = _now()
    db.session.commit()
    return f.to_dict()


def register_project_from_template(project_id: int, template_id: str, workspace_dir: str) -> dict:
    """Register a project from a template, creating artifact rows from the template."""
    import os
    from templates import get_template, expand_role_edges_to_artifact_edges, topological_sort, CycleDetectedError

    # Validate template
    template = get_template(template_id)
    if not template:
        raise ValueError(f"Unknown template: {template_id}")

    # Build artifact keys and edges first so cycle detection happens before any writes
    artifact_keys = [a.key for a in template.artifacts]
    edges = expand_role_edges_to_artifact_edges(template)
    try:
        topological_sort(artifact_keys, edges)
    except CycleDetectedError:
        raise

    # Update folder with project info
    f = db.session.get(Folder, project_id)
    if not f:
        return None
    f.kind = "project"
    f.template_id = template_id
    f.workspace_dir = workspace_dir
    f.propagation_mode = template.default_propagation_mode if template.default_propagation_mode else "propose"
    f.next_req_seq = 1
    f.token_budget = 0
    f.updated_at = _now()
    db.session.commit()

    # Map role -> persona id (may be None if the persona is not seeded)
    persona_by_name = {p.name: p.id for p in Persona.query.all()}

    registered_keys = []

    # Create default artifact files if they don't exist, then register them
    for spec in template.artifacts:
        dir_path = os.path.join(workspace_dir, os.path.dirname(spec.rel_path))
        file_path = os.path.join(workspace_dir, spec.rel_path)

        if not os.path.exists(file_path):
            os.makedirs(dir_path, exist_ok=True)
            fm = "---\nartifact_id: %s\nrole: %s\nversion: 1\norigin: human\nderives_from: []\n---\n" % (
                spec.key,
                spec.role,
            )
            body = "# %s\n\n## placeholder\n\n_Authors: %s._\n" % (spec.key, spec.role)
            with open(file_path, 'w', encoding='utf-8') as fw:
                fw.write(fm + body)

        # Read content and hash
        content, file_exists = read_artifact_file(workspace_dir, spec.rel_path)
        content_hash = ""
        if file_exists and content:
            content_hash = compute_content_hash(content)

        role_persona_id = persona_by_name.get(template.role_to_persona.get(spec.role))

        create_artifact(
            project_id=project_id,
            artifact_key=spec.key,
            role_persona_id=role_persona_id,
            rel_path=spec.rel_path,
            version=1,
            content_hash=content_hash,
            status="current",
            origin="human",
        )
        registered_keys.append(spec.key)

    # Create artifact dependencies (expanded role edges)
    delete_artifact_deps(project_id)
    for upstream, downstream in edges:
        create_artifact_dep(project_id, upstream, downstream)

    # Initialize the git repo for the workspace
    try:
        from git_integration import init_project_workspace
        init_project_workspace(workspace_dir)
    except Exception:
        pass

    return {
        "project_id": project_id,
        "template_id": template_id,
        "artifacts_registered": len(registered_keys),
        "edges_created": len(edges),
        "artifact_keys": registered_keys,
    }


def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of content."""
    import hashlib
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def is_artifact_registered(project_id: int, artifact_key: str) -> bool:
    """Check whether an artifact key is registered for the project."""
    return db.session.query(Artifact.id)\
        .filter_by(project_id=project_id, artifact_key=artifact_key)\
        .first() is not None


def get_artifact_context(
    project_id: int,
    artifact_keys: list[str],
    change_event: dict | None = None,
) -> tuple[str, int, list[str]]:
    """
    DB-backed scoped artifact context lookup (C07).

    Loads the requested artifact contents from disk using each artifact's
    registered ``rel_path``, then builds a scoped context via
    ``file_handler.build_artifact_context``.

    Returns:
        (context_str, total_char_count, truncated_keys_list)
    """
    from file_handler import build_artifact_context

    project = get_folder(project_id)
    if not project:
        return "", 0, []

    workspace_dir = project.get("workspace_dir", "")
    if not artifact_keys or not workspace_dir:
        return "", 0, []

    artifacts = {}
    for artifact in Artifact.query.filter_by(project_id=project_id).all():
        if artifact.artifact_key not in artifact_keys:
            continue
        rel_path = artifact.rel_path
        content, file_exists = read_artifact_file(workspace_dir, rel_path)
        if file_exists:
            artifacts[artifact.artifact_key] = {"content": content, "rel_path": rel_path}

    return build_artifact_context(artifacts, change_event or {})


def get_project_artifact_graph(project_id: int) -> dict:
    """Return the artifact dependency graph with sorted keys, edges and depths."""
    from templates import topological_sort, compute_depths, CycleDetectedError

    artifacts = list_artifacts(project_id)
    edges = [(d["upstream_key"], d["downstream_key"]) for d in list_artifact_deps(project_id)]
    artifact_keys = [a["artifact_key"] for a in artifacts]

    try:
        sorted_keys = topological_sort(artifact_keys, edges)
    except CycleDetectedError:
        sorted_keys = sorted(artifact_keys)

    depths = compute_depths(artifact_keys, edges)

    return {
        "project_id": project_id,
        "artifact_keys": artifact_keys,
        "sorted_keys": sorted_keys,
        "edges": edges,
        "depths": depths,
    }


def read_artifact_file(workspace_dir: str, rel_path: str) -> tuple[str, bool]:
    full_path = os.path.join(workspace_dir, rel_path)
    if not os.path.exists(full_path):
        return "", False
    try:
        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        return content, True
    except Exception:
        return "", False


# ── Thread-safe session helper (0.4) ──────────────────────────────────────────

def run_with_session(app, fn):
    """
    Run a worker-style job inside an app context with a scoped session,
    committed on success, rolled back on error, and the session removed after.

    Args:
        app: The Flask application.
        fn: A zero-argument callable that performs DB work.

    Returns:
        The return value of ``fn``.
    """
    with app.app_context():
        try:
            result = fn()
            db.session.commit()
            return result
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.session.remove()


# ── Repair watermark (0.4) ─────────────────────────────────────────────────────

def should_run_repair(version: str) -> bool:
    """Return True if the repair routine at ``version`` has not yet run."""
    row = db.session.get(Setting, "tool_call_repair_version")
    return row is None or row.value != str(version)


def mark_repair_complete(version: str) -> None:
    """Record that the repair routine at ``version`` has completed."""
    row = db.session.get(Setting, "tool_call_repair_version")
    if row:
        row.value = str(version)
    else:
        db.session.add(Setting(key="tool_call_repair_version", value=str(version)))
    db.session.commit()


# ── Cascade / lifecycle cleanup wrappers (C09) ──────────────────────────────────

def cleanup_on_folder_delete(project_id: int, workspace_dir: str) -> dict:
    """Per D9: never delete user documents from a sidebar action; warn instead."""
    from git_integration import cleanup_on_folder_delete as _impl
    return _impl(project_id, workspace_dir)


def cleanup_on_purge() -> dict:
    """Per D9: clear jobs/changes or explicitly preserve them on purge."""
    from git_integration import cleanup_on_purge as _impl
    return _impl()


def verify_git_on_startup() -> tuple[bool, str]:
    """Verify git is available when the application starts."""
    from git_integration import verify_git_on_startup as _impl
    return _impl()
