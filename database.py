from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from datetime import datetime, timezone
from cryptography.fernet import Fernet
import os
import base64
import hashlib

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
            cursor.close()
        except Exception:
            pass


# ── Models ────────────────────────────────────────────────────────────────────

class Folder(db.Model):
    __tablename__ = 'folders'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.String(50), nullable=False)
    updated_at = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "position": self.position,
            "created_at": self.created_at,
            "updated_at": self.updated_at
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
    api_key = db.Column(db.String(512), nullable=False, default='')
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


def init_db(app=None):
    if app:
        with app.app_context():
            db.create_all()
            _seed_personas_and_settings()
            migrate_existing_api_keys()
    else:
        db.create_all()
        _seed_personas_and_settings()
        migrate_existing_api_keys()


def migrate_existing_api_keys():
    endpoints = Endpoint.query.all()
    migrated = False
    for ep in endpoints:
        if ep.api_key:
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
    for key, val in [('output_dir', ''), ('browser_root', '')]:
        existing = db.session.get(Setting, key)
        if not existing:
            setting = Setting(key=key, value=val)
            db.session.add(setting)

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


def update_conversation(conversation_id: int, title: str = None, model_id: str = None, persona_id: int = None, clear_persona: bool = False, output_dir: str = None, enable_tools: bool = None, endpoint_id: int = None, clear_endpoint: bool = False, folder_id: int = None, clear_folder: bool = False):
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


# ── Settings ───────────────────────────────────────────────────────────────────

def get_setting(key: str) -> str | None:
    setting = db.session.get(Setting, key)
    return setting.value if setting else None


def set_setting(key: str, value: str):
    setting = db.session.get(Setting, key)
    if setting:
        setting.value = value
    else:
        setting = Setting(key=key, value=value)
        db.session.add(setting)
    db.session.commit()


def reset_settings() -> dict:
    for key in ("output_dir", "browser_root"):
        setting = db.session.get(Setting, key)
        if setting:
            setting.value = ""
        else:
            setting = Setting(key=key, value="")
            db.session.add(setting)

    endpoints = Endpoint.query.all()
    for ep in endpoints:
        ep.default_model = ""

    db.session.commit()
    return {
        "output_dir": "",
        "browser_root": "",
    }


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


def edit_message_and_truncate(conversation_id: int, message_id: int, new_content: str):
    msg = db.session.get(Message, message_id)
    if msg and msg.conversation_id == conversation_id:
        msg.content = new_content
        Message.query.filter(Message.conversation_id == conversation_id, Message.id > message_id).delete()
        db.session.commit()


def search_all_conversations(query: str) -> list[dict]:
    rows = db.session.query(Conversation.id, Conversation.title, Conversation.updated_at, Message.content)\
        .join(Message, Message.conversation_id == Conversation.id)\
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


def update_folder(folder_id: int, name: str = None, position: int = None):
    f = db.session.get(Folder, folder_id)
    if not f:
        return
    if name is not None:
        f.name = name
    if position is not None:
        f.position = position
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
