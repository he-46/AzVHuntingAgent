"""SQLite persistence for the local job application tracker."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator


DEFAULT_DB_PATH = "data/applications.db"
SCHEMA_VERSION = 6
_active_transaction: ContextVar[tuple[Path, sqlite3.Connection] | None] = ContextVar(
    "active_database_transaction", default=None
)
EVENT_TYPES = frozenset(
    {
        "招聘开始",
        "岗位发布",
        "投递截止",
        "已投递",
        "测评邀请",
        "测评完成",
        "面试邀请",
        "面试完成",
        "HR反馈",
        "录取通知",
        "拒绝",
        "其他",
    }
)
DEADLINE_KINDS = frozenset({"官方截止", "自设截止", "建议跟进"})
APPLICATION_FIELDS = frozenset(
    {"company", "role", "jd", "company_info", "recruitment_start", "recruitment_end"}
)
EVENT_FIELDS = frozenset(
    {
        "event_type",
        "event_date",
        "details",
        "deadline_at",
        "deadline_kind",
        "source",
        "source_quote",
        "feedback_score",
    }
)


@contextmanager
def _connection(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path).resolve()
    active = _active_transaction.get()
    if active is not None and active[0] == path:
        yield active[1]
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Create the database if needed; safe to call more than once."""
    active = _active_transaction.get()
    if active is not None and active[0] == Path(db_path).resolve():
        return
    with _connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                jd TEXT NOT NULL DEFAULT '',
                company_info TEXT NOT NULL DEFAULT '',
                recruitment_start TEXT,
                recruitment_end TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                application_id INTEGER NOT NULL
                    REFERENCES applications(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                event_date TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                deadline_at TEXT,
                deadline_kind TEXT,
                source TEXT NOT NULL DEFAULT '',
                source_quote TEXT NOT NULL DEFAULT '',
                feedback_score REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_events_application_date
                ON events(application_id, event_date, id);
            CREATE TABLE IF NOT EXISTS intake_entries (
                id INTEGER PRIMARY KEY,
                application_id INTEGER NOT NULL
                    REFERENCES applications(id) ON DELETE CASCADE,
                input_text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_intake_entries_application
                ON intake_entries(application_id, id);
            CREATE TABLE IF NOT EXISTS resume_versions (
                id INTEGER PRIMARY KEY,
                application_id INTEGER NOT NULL
                    REFERENCES applications(id) ON DELETE CASCADE,
                source_resume TEXT NOT NULL,
                tailored_resume TEXT NOT NULL,
                match_analysis TEXT NOT NULL DEFAULT '',
                missing_evidence TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_resume_versions_application
                ON resume_versions(application_id, id DESC);
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS candidate_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                education_json TEXT NOT NULL DEFAULT '[]',
                experiences_json TEXT NOT NULL DEFAULT '[]',
                internships_json TEXT NOT NULL DEFAULT '[]',
                projects_json TEXT NOT NULL DEFAULT '[]',
                skills_json TEXT NOT NULL DEFAULT '[]',
                source_text TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY,
                operation TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                request_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_llm_usage_created
                ON llm_usage(created_at, operation);
            CREATE TABLE IF NOT EXISTS llm_reservations (
                id INTEGER PRIMARY KEY,
                operation TEXT NOT NULL,
                model TEXT NOT NULL,
                reserved_tokens INTEGER NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_llm_reservations_created
                ON llm_reservations(created_at, request_hash);
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(applications)")
        }
        if "company_info" not in columns:
            conn.execute(
                "ALTER TABLE applications ADD COLUMN company_info TEXT NOT NULL DEFAULT ''"
            )
        conn.executemany(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            [(version,) for version in range(1, SCHEMA_VERSION + 1)],
        )


@contextmanager
def atomic(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[None]:
    """Run existing repository calls in one SQLite transaction."""
    path = Path(db_path).resolve()
    if _active_transaction.get() is not None:
        raise RuntimeError("不支持嵌套数据库事务")
    init_db(path)
    with _connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        token = _active_transaction.set((path, conn))
        try:
            yield
        finally:
            _active_transaction.reset(token)


def backup_database(db_path: str | Path = DEFAULT_DB_PATH) -> bytes:
    """Return a consistent SQLite snapshot suitable for download."""
    init_db(db_path)
    with closing(sqlite3.connect(Path(db_path).resolve())) as source:
        with closing(sqlite3.connect(":memory:")) as snapshot:
            source.backup(snapshot)
            return snapshot.serialize()


def inspect_backup(data: bytes) -> dict[str, int]:
    """Validate a backup and summarize its user data before restore."""
    if not data or len(data) > 100_000_000:
        raise ValueError("备份文件为空或超过 100 MB。")
    try:
        with closing(sqlite3.connect(":memory:")) as source:
            source.deserialize(data)
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("备份文件未通过完整性检查。")
            required = {"applications", "events", "candidate_profile", "schema_migrations"}
            present = {
                row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if not required.issubset(present):
                raise ValueError("这不是本项目的完整数据库备份。")
            version = source.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            if int(version) > SCHEMA_VERSION:
                raise ValueError("备份由较新版本创建，当前程序无法恢复。")
            if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("备份中的岗位和事件关联不完整。")
            return {
                "applications": source.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
                "events": source.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            }
    except sqlite3.DatabaseError as exc:
        raise ValueError("无法读取 SQLite 备份文件。") from exc


def restore_database(data: bytes, db_path: str | Path = DEFAULT_DB_PATH) -> Path:
    """Validate and restore a backup, saving the current database first."""
    inspect_backup(data)
    target = Path(db_path).resolve()
    init_db(target)
    backup_dir = target.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    before_restore = backup_dir / f"before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db"
    with closing(sqlite3.connect(target)) as current, closing(sqlite3.connect(before_restore)) as old_copy:
        current.backup(old_copy)
    with closing(sqlite3.connect(":memory:")) as source, closing(sqlite3.connect(target)) as destination:
        source.deserialize(data)
        source.backup(destination)
    init_db(target)
    return before_restore


def _iso_date(value: str | date | datetime | None, *, optional: bool) -> str | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError("事件日期不能为空")
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise TypeError("日期必须是 ISO 字符串或 date 对象")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"日期必须采用 YYYY-MM-DD 格式：{value}") from exc


def _iso_deadline(value: str | date | datetime | None) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise TypeError("截止时间必须是 ISO 字符串或 date/datetime 对象")
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError as exc:
        raise ValueError(f"截止时间必须采用 ISO 格式：{value}") from exc


def _event_type(value: str) -> str:
    if value not in EVENT_TYPES:
        raise ValueError(f"不支持的事件类型：{value}")
    return value


def _deadline_kind(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in DEADLINE_KINDS:
        raise ValueError(f"不支持的截止时间类型：{value}")
    return value


def _score(value: float | int | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("面试表现评分必须是 1–5 的数字") from exc
    if not 1 <= score <= 5:
        raise ValueError("面试表现评分必须在 1–5 之间")
    return score


def _application_values(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = set(fields) - APPLICATION_FIELDS
    if unknown:
        raise ValueError(f"不可修改的申请字段：{', '.join(sorted(unknown))}")
    result = dict(fields)
    for field in ("company", "role"):
        if field in result:
            result[field] = str(result[field] or "").strip()
            if not result[field]:
                raise ValueError(f"{field} 不能为空")
    for field in ("jd", "company_info"):
        if field in result:
            result[field] = str(result[field] or "")
    for field in ("recruitment_start", "recruitment_end"):
        if field in result:
            result[field] = _iso_date(result[field], optional=True)
    return result


def _event_values(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = set(fields) - EVENT_FIELDS
    if unknown:
        raise ValueError(f"不可修改的事件字段：{', '.join(sorted(unknown))}")
    result = dict(fields)
    if "event_type" in result:
        result["event_type"] = _event_type(result["event_type"])
    if "event_date" in result:
        result["event_date"] = _iso_date(result["event_date"], optional=False)
    if "deadline_at" in result:
        result["deadline_at"] = _iso_deadline(result["deadline_at"])
    if "deadline_kind" in result:
        result["deadline_kind"] = _deadline_kind(result["deadline_kind"])
    if "feedback_score" in result:
        result["feedback_score"] = _score(result["feedback_score"])
    for field in ("details", "source", "source_quote"):
        if field in result:
            result[field] = str(result[field] or "")
    return result


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def create_application(
    company: str,
    role: str,
    jd: str = "",
    recruitment_start: str | date | None = None,
    recruitment_end: str | date | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    company_info: str = "",
) -> int:
    init_db(db_path)
    values = _application_values(
        {
            "company": company,
            "role": role,
            "jd": jd,
            "company_info": company_info,
            "recruitment_start": recruitment_start,
            "recruitment_end": recruitment_end,
        }
    )
    with _connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO applications
                (company, role, jd, company_info, recruitment_start, recruitment_end)
                VALUES (:company, :role, :jd, :company_info, :recruitment_start,
                        :recruitment_end)""",
            values,
        )
        return int(cursor.lastrowid)


def list_applications(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connection(db_path) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM applications ORDER BY updated_at DESC, id DESC"
            )
        ]


def get_application(
    application_id: int, db_path: str | Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_db(db_path)
    with _connection(db_path) as conn:
        return _row_dict(
            conn.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
        )


def update_application(
    application_id: int,
    fields: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    values = _application_values(fields)
    if not values:
        return
    init_db(db_path)
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    values["id"] = application_id
    with _connection(db_path) as conn:
        conn.execute(
            f"UPDATE applications SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = :id",
            values,
        )


def delete_application(
    application_id: int, db_path: str | Path = DEFAULT_DB_PATH
) -> None:
    init_db(db_path)
    with _connection(db_path) as conn:
        conn.execute("DELETE FROM applications WHERE id = ?", (application_id,))


def add_intake_entry(
    application_id: int,
    input_text: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Save the original text submitted to the unified intake for an application."""
    if not isinstance(input_text, str) or not input_text.strip():
        raise ValueError("输入内容不能为空")
    init_db(db_path)
    with _connection(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO intake_entries (application_id, input_text) VALUES (?, ?)",
            (application_id, input_text),
        )
        conn.execute(
            "UPDATE applications SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (application_id,),
        )
        return int(cursor.lastrowid)


def list_intake_entries(
    application_id: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Return submitted intake texts in the order they were saved."""
    init_db(db_path)
    with _connection(db_path) as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM intake_entries WHERE application_id = ?
                   ORDER BY id""",
                (application_id,),
            )
        ]


def add_resume_version(
    application_id: int,
    source_resume: str,
    tailored_resume: str,
    match_analysis: str = "",
    missing_evidence: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Save a reviewed job-specific resume version."""
    if not isinstance(source_resume, str) or not source_resume.strip():
        raise ValueError("基础简历不能为空")
    if not isinstance(tailored_resume, str) or not tailored_resume.strip():
        raise ValueError("定制简历不能为空")
    init_db(db_path)
    with _connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO resume_versions
                (application_id, source_resume, tailored_resume,
                 match_analysis, missing_evidence)
                VALUES (?, ?, ?, ?, ?)""",
            (
                application_id,
                source_resume,
                tailored_resume,
                str(match_analysis or ""),
                str(missing_evidence or ""),
            ),
        )
        conn.execute(
            "UPDATE applications SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (application_id,),
        )
        return int(cursor.lastrowid)


def list_resume_versions(
    application_id: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Return saved resume versions, newest first."""
    init_db(db_path)
    with _connection(db_path) as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM resume_versions WHERE application_id = ?
                   ORDER BY id DESC""",
                (application_id,),
            )
        ]


def save_candidate_profile(
    profile: dict[str, Any],
    *,
    source_text: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Create or replace the single local candidate profile."""
    list_fields = ("education", "experiences", "internships", "projects", "skills")
    normalized_lists: dict[str, list[str]] = {}
    for field in list_fields:
        value = profile.get(field) or []
        if not isinstance(value, list):
            raise ValueError(f"{field} 必须是列表")
        normalized_lists[field] = [str(item).strip() for item in value if str(item).strip()]
    values = {
        "name": str(profile.get("name") or "").strip(),
        "summary": str(profile.get("summary") or "").strip(),
        "source_text": str(source_text or ""),
        **{f"{field}_json": json.dumps(items, ensure_ascii=False) for field, items in normalized_lists.items()},
    }
    if not any([values["name"], values["summary"], *normalized_lists.values()]):
        raise ValueError("求职者资料不能为空")
    init_db(db_path)
    with _connection(db_path) as conn:
        conn.execute(
            """INSERT INTO candidate_profile
                (id, name, summary, education_json, experiences_json,
                 internships_json, projects_json, skills_json, source_text)
                VALUES (1, :name, :summary, :education_json, :experiences_json,
                        :internships_json, :projects_json, :skills_json, :source_text)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    summary = excluded.summary,
                    education_json = excluded.education_json,
                    experiences_json = excluded.experiences_json,
                    internships_json = excluded.internships_json,
                    projects_json = excluded.projects_json,
                    skills_json = excluded.skills_json,
                    source_text = excluded.source_text,
                    updated_at = CURRENT_TIMESTAMP""",
            values,
        )


def get_candidate_profile(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Return the local candidate profile with decoded list fields."""
    init_db(db_path)
    with _connection(db_path) as conn:
        row = conn.execute("SELECT * FROM candidate_profile WHERE id = 1").fetchone()
    if row is None:
        return None
    result = dict(row)
    for field in ("education", "experiences", "internships", "projects", "skills"):
        result[field] = json.loads(result.pop(f"{field}_json"))
    return result


def record_llm_usage(
    *,
    operation: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    request_hash: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Persist actual token usage returned by the model provider."""
    init_db(db_path)
    with _connection(db_path) as conn:
        conn.execute(
            """INSERT INTO llm_usage
                (operation, model, input_tokens, output_tokens, total_tokens, request_hash)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (
                operation,
                model,
                max(0, int(input_tokens)),
                max(0, int(output_tokens)),
                max(0, int(total_tokens)),
                request_hash,
            ),
        )


def llm_usage_summary(
    *,
    since: datetime,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, int]:
    """Return call and token totals from a UTC-aware boundary."""
    init_db(db_path)
    with _connection(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens
               FROM llm_usage
               WHERE datetime(created_at) >= datetime(?)""",
            (since.isoformat(),),
        ).fetchone()
        reserved = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(reserved_tokens), 0) AS tokens
               FROM llm_reservations
               WHERE datetime(created_at) >= datetime(?)""",
            (since.isoformat(),),
        ).fetchone()
    return {
        "calls": int(row["calls"]) + int(reserved["calls"]),
        "successful_calls": int(row["calls"]),
        "reserved_calls": int(reserved["calls"]),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "reserved_tokens": int(reserved["tokens"]),
        "total_tokens": int(row["total_tokens"]) + int(reserved["tokens"]),
    }


def has_recent_llm_request(
    request_hash: str,
    *,
    seconds: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> bool:
    """Whether the same successful request was recorded recently."""
    init_db(db_path)
    with _connection(db_path) as conn:
        row = conn.execute(
            """SELECT 1 FROM (
                   SELECT request_hash, created_at FROM llm_usage
                   UNION ALL
                   SELECT request_hash, created_at FROM llm_reservations
               )
               WHERE request_hash = ? AND datetime(created_at) >= datetime('now', ?)
               LIMIT 1""",
            (request_hash, f"-{int(seconds)} seconds"),
        ).fetchone()
    return row is not None


def reserve_llm_call(
    *,
    operation: str,
    model: str,
    request_hash: str,
    reserved_tokens: int,
    since: datetime,
    token_budget: int,
    call_budget: int,
    duplicate_window_seconds: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Atomically reserve daily call and token capacity before an API request."""
    with atomic(db_path):
        usage = llm_usage_summary(since=since, db_path=db_path)
        if usage["calls"] >= call_budget:
            raise ValueError(f"今天已经调用或预留 {usage['calls']} 次，达到每日上限 {call_budget} 次")
        if usage["total_tokens"] + reserved_tokens > token_budget:
            remaining = max(0, token_budget - usage["total_tokens"])
            raise ValueError(
                f"本次请求预留 {reserved_tokens} tokens，今日预算仅剩 {remaining} tokens"
            )
        if has_recent_llm_request(
            request_hash,
            seconds=duplicate_window_seconds,
            db_path=db_path,
        ):
            raise ValueError(f"相同请求刚刚已经执行，请等待 {duplicate_window_seconds} 秒后再试")
        with _connection(db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO llm_reservations
                    (operation, model, reserved_tokens, request_hash)
                   VALUES (?, ?, ?, ?)""",
                (operation, model, reserved_tokens, request_hash),
            )
            return int(cursor.lastrowid)


def settle_llm_call(
    reservation_id: int,
    *,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Replace a reservation with the provider's actual usage atomically."""
    with atomic(db_path):
        with _connection(db_path) as conn:
            reserved = conn.execute(
                "SELECT * FROM llm_reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            if reserved is None:
                raise ValueError("模型请求预留记录不存在")
            conn.execute(
                """INSERT INTO llm_usage
                    (operation, model, input_tokens, output_tokens, total_tokens, request_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    reserved["operation"], reserved["model"],
                    max(0, int(input_tokens)), max(0, int(output_tokens)),
                    max(0, int(total_tokens)), reserved["request_hash"],
                ),
            )
            conn.execute("DELETE FROM llm_reservations WHERE id = ?", (reservation_id,))


def mark_llm_call_failed(
    reservation_id: int, db_path: str | Path = DEFAULT_DB_PATH
) -> None:
    """Keep a conservative reservation when the provider's usage is unknown."""
    with _connection(db_path) as conn:
        conn.execute(
            "UPDATE llm_reservations SET status = 'failed' WHERE id = ?",
            (reservation_id,),
        )


def add_event(
    application_id: int,
    event_type: str,
    event_date: str | date,
    details: str = "",
    deadline_at: str | date | datetime | None = None,
    deadline_kind: str | None = None,
    source: str = "",
    source_quote: str = "",
    feedback_score: float | int | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    init_db(db_path)
    values = _event_values(
        {
            "event_type": event_type,
            "event_date": event_date,
            "details": details,
            "deadline_at": deadline_at,
            "deadline_kind": deadline_kind,
            "source": source,
            "source_quote": source_quote,
            "feedback_score": feedback_score,
        }
    )
    values["application_id"] = application_id
    with _connection(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO events
                (application_id, event_type, event_date, details, deadline_at,
                 deadline_kind, source, source_quote, feedback_score)
                VALUES (:application_id, :event_type, :event_date, :details,
                        :deadline_at, :deadline_kind, :source, :source_quote,
                        :feedback_score)""",
            values,
        )
        conn.execute(
            "UPDATE applications SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (application_id,),
        )
        return int(cursor.lastrowid)


def list_events(
    application_id: int, db_path: str | Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connection(db_path) as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM events WHERE application_id = ?
                   ORDER BY event_date, id""",
                (application_id,),
            )
        ]


def update_event(
    event_id: int, fields: dict[str, Any], db_path: str | Path = DEFAULT_DB_PATH
) -> None:
    values = _event_values(fields)
    if not values:
        return
    init_db(db_path)
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    values["id"] = event_id
    with _connection(db_path) as conn:
        conn.execute(
            f"UPDATE events SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = :id",
            values,
        )
        conn.execute(
            """UPDATE applications SET updated_at = CURRENT_TIMESTAMP
               WHERE id = (SELECT application_id FROM events WHERE id = ?)""",
            (event_id,),
        )


def delete_event(event_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    with _connection(db_path) as conn:
        row = conn.execute(
            "SELECT application_id FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if row is not None:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            conn.execute(
                "UPDATE applications SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["application_id"],),
            )
