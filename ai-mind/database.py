import json
import sqlite3

DATABASE = "ai_mind.db"


def get_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database():
    connection = get_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT 'New conversation',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (conversation_id)
                REFERENCES conversations(id)
                ON DELETE CASCADE
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            memory_type TEXT NOT NULL DEFAULT 'general',
            confidence REAL NOT NULL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS ignored_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS ai_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            available INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'Available',
            rate_limit INTEGER,
            remaining INTEGER,
            reset_at REAL,
            last_error TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS communication_style (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            profile TEXT NOT NULL DEFAULT '{}',
            observation_count INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    connection.close()


def create_conversation(title="New conversation"):
    connection = get_connection()

    cursor = connection.execute(
        """
        INSERT INTO conversations (title)
        VALUES (?)
        """,
        (title,)
    )

    conversation_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return conversation_id


def add_message(conversation_id, role, content):
    connection = get_connection()

    connection.execute(
        """
        INSERT INTO messages (
            conversation_id,
            role,
            content
        )
        VALUES (?, ?, ?)
        """,
        (conversation_id, role, content)
    )

    connection.execute(
        """
        UPDATE conversations
        SET title = CASE
                WHEN title = 'New conversation' AND ? = 'user'
                THEN SUBSTR(TRIM(?), 1, 80)
                ELSE title
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (role, content, conversation_id)
    )

    connection.commit()
    connection.close()


def get_messages(conversation_id):
    connection = get_connection()

    rows = connection.execute(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,)
    ).fetchall()

    connection.close()

    return [
        {
            "role": row["role"],
            "content": row["content"]
        }
        for row in rows
    ]


def list_conversations(search="", limit=100):
    search_term = f"%{search.strip()}%"
    connection = get_connection()

    rows = connection.execute(
        f"""
        SELECT
            c.id,
            CASE
                WHEN c.title = 'New conversation' THEN COALESCE(
                    (
                        SELECT SUBSTR(TRIM(content), 1, 80)
                        FROM messages first_title_message
                        WHERE first_title_message.conversation_id = c.id
                          AND first_title_message.role = 'user'
                        ORDER BY first_title_message.id ASC
                        LIMIT 1
                    ),
                    c.title
                )
                ELSE c.title
            END AS title,
            c.created_at,
            c.updated_at,
            COUNT(m.id) AS message_count,
            COALESCE(
                (
                    SELECT content
                    FROM messages first_message
                    WHERE first_message.conversation_id = c.id
                      AND first_message.role = 'user'
                    ORDER BY first_message.id ASC
                    LIMIT 1
                ),
                ''
            ) AS preview
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE (? = '' OR c.title LIKE ? OR EXISTS (
            SELECT 1
            FROM messages matching_message
            WHERE matching_message.conversation_id = c.id
              AND matching_message.content LIKE ?
        ))
        GROUP BY c.id
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ?
        """,
        (search.strip(), search_term, search_term, limit)
    ).fetchall()

    connection.close()

    return [dict(row) for row in rows]


def get_conversation(conversation_id):
    connection = get_connection()
    row = connection.execute(
        """
        SELECT
            id,
            CASE
                WHEN title = 'New conversation' THEN COALESCE(
                    (
                        SELECT SUBSTR(TRIM(content), 1, 80)
                        FROM messages first_title_message
                        WHERE first_title_message.conversation_id = conversations.id
                          AND first_title_message.role = 'user'
                        ORDER BY first_title_message.id ASC
                        LIMIT 1
                    ),
                    title
                )
                ELSE title
            END AS title,
            created_at,
            updated_at
        FROM conversations
        WHERE id = ?
        """,
        (conversation_id,)
    ).fetchone()
    connection.close()
    return dict(row) if row else None


def rename_conversation(conversation_id, title):
    connection = get_connection()
    cursor = connection.execute(
        """
        UPDATE conversations
        SET title = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (title, conversation_id)
    )
    connection.commit()
    connection.close()
    return cursor.rowcount > 0


def delete_conversation(conversation_id):
    connection = get_connection()
    cursor = connection.execute(
        "DELETE FROM conversations WHERE id = ?",
        (conversation_id,)
    )
    connection.commit()
    connection.close()
    return cursor.rowcount > 0

def add_memory(content, memory_type="general", confidence=0.5):
    connection = get_connection()

    cursor = connection.execute(
        """
        INSERT INTO memories (
            content,
            memory_type,
            confidence
        )
        VALUES (?, ?, ?)
        """,
        (content, memory_type, confidence)
    )

    memory_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return memory_id


def get_memories():
    connection = get_connection()

    rows = connection.execute(
        """
        SELECT id, content, memory_type, confidence
        FROM memories
        ORDER BY updated_at DESC
        """
    ).fetchall()

    connection.close()

    return [
        {
            "id": row["id"],
            "content": row["content"],
            "memory_type": row["memory_type"],
            "confidence": row["confidence"]
        }
        for row in rows
    ]

def delete_memory(memory_id):
    connection = get_connection()

    connection.execute(
        """
        DELETE FROM memories
        WHERE id = ?
        """,
        (memory_id,)
    )

    connection.commit()
    connection.close()

def update_memory(memory_id, content, memory_type, confidence):
    connection = get_connection()

    connection.execute(
        """
        UPDATE memories
        SET content = ?,
            memory_type = ?,
            confidence = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (content, memory_type, confidence, memory_id)
    )

    connection.commit()
    connection.close()


def memory_exists(content):
    connection = get_connection()

    row = connection.execute(
        """
        SELECT id
        FROM memories
        WHERE LOWER(TRIM(content)) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (content,)
    ).fetchone()

    connection.close()

    return row["id"] if row else None

def add_ignored_memory(content):
    connection = get_connection()

    connection.execute(
        """
        INSERT OR IGNORE INTO ignored_memories (content)
        VALUES (?)
        """,
        (content.strip(),)
    )

    connection.commit()
    connection.close()


def is_memory_ignored(content):
    connection = get_connection()

    row = connection.execute(
        """
        SELECT id
        FROM ignored_memories
        WHERE LOWER(TRIM(content)) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (content,)
    ).fetchone()

    connection.close()

    return row is not None


def get_ignored_memories():
    connection = get_connection()

    rows = connection.execute(
        """
        SELECT content
        FROM ignored_memories
        ORDER BY created_at DESC
        """
    ).fetchall()

    connection.close()

    return [
        row["content"]
        for row in rows
    ]

def get_ai_status():
    connection = get_connection()

    row = connection.execute(
        """
        SELECT
            available,
            status,
            rate_limit,
            remaining,
            reset_at,
            last_error
        FROM ai_status
        WHERE id = 1
        """
    ).fetchone()

    connection.close()

    if not row:
        return {
            "available": True,
            "status": "Available",
            "limit": None,
            "remaining": None,
            "reset_at": None,
            "last_error": None
        }

    return {
        "available": bool(row["available"]),
        "status": row["status"],
        "limit": row["rate_limit"],
        "remaining": row["remaining"],
        "reset_at": row["reset_at"],
        "last_error": row["last_error"]
    }


def save_ai_status(
    available,
    status,
    limit,
    remaining,
    reset_at,
    last_error
):
    connection = get_connection()

    connection.execute(
        """
        INSERT INTO ai_status (
            id,
            available,
            status,
            rate_limit,
            remaining,
            reset_at,
            last_error,
            updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)

        ON CONFLICT(id) DO UPDATE SET
            available = excluded.available,
            status = excluded.status,
            rate_limit = excluded.rate_limit,
            remaining = excluded.remaining,
            reset_at = excluded.reset_at,
            last_error = excluded.last_error,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            int(available),
            status,
            limit,
            remaining,
            reset_at,
            last_error
        )
    )

    connection.commit()
    connection.close()


def get_communication_style():
    connection = get_connection()

    row = connection.execute(
        """
        SELECT profile, observation_count, updated_at
        FROM communication_style
        WHERE id = 1
        """
    ).fetchone()

    connection.close()

    if not row:
        return {
            "profile": {},
            "observation_count": 0,
            "updated_at": None
        }

    try:
        profile = json.loads(row["profile"])
    except (TypeError, json.JSONDecodeError):
        profile = {}

    return {
        "profile": profile if isinstance(profile, dict) else {},
        "observation_count": row["observation_count"],
        "updated_at": row["updated_at"]
    }


def update_communication_style(profile, observation_count=None):
    connection = get_connection()

    if observation_count is None:
        existing = connection.execute(
            "SELECT observation_count FROM communication_style WHERE id = 1"
        ).fetchone()
        observation_count = existing["observation_count"] if existing else 0

    connection.execute(
        """
        INSERT INTO communication_style (id, profile, observation_count, updated_at)
        VALUES (1, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            profile = excluded.profile,
            observation_count = excluded.observation_count,
            updated_at = CURRENT_TIMESTAMP
        """,
        (json.dumps(profile), observation_count)
    )

    connection.commit()
    connection.close()