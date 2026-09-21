import re
from collections import defaultdict

from database import get_connection


DEFAULT_LIMIT = 3
CONTEXT_BEFORE = 2
CONTEXT_AFTER = 2


def _tokenize(text):
    """Extract useful words from text."""
    stop_words = {
        "the", "and", "that", "this", "with", "from",
        "what", "when", "where", "which", "would",
        "could", "should", "have", "about", "into",
        "your", "you", "how", "why", "can", "for",
        "are", "was", "were", "has", "does", "not",
        "but", "then", "than", "did", "our", "we"
    }

    return {
        word
        for word in re.findall(
            r"\b[a-zA-Z0-9_]{3,}\b",
            text.lower()
        )
        if word not in stop_words
    }


def _score_message(query_words, content):
    """Calculate keyword overlap relevance."""
    message_words = _tokenize(content)

    if not query_words or not message_words:
        return 0.0

    overlap = query_words & message_words

    if not overlap:
        return 0.0

    return len(overlap) / len(query_words)


def _get_context_window(
    connection,
    conversation_id,
    message_id
):
    """
    Retrieve a small number of messages around the
    matching message.
    """

    rows = connection.execute(
        """
        SELECT id, role, content, created_at
        FROM messages
        WHERE conversation_id = ?
          AND id BETWEEN ? AND ?
        ORDER BY id ASC
        """,
        (
            conversation_id,
            max(0, message_id - CONTEXT_BEFORE),
            message_id + CONTEXT_AFTER
        )
    ).fetchall()

    return [
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"]
        }
        for row in rows
    ]


def retrieve_relevant_conversations(
    user_message,
    current_conversation_id=None,
    limit=DEFAULT_LIMIT
):
    """
    Search previous conversations for relevant messages.

    Returns a small context window around the strongest
    matching message in each conversation.
    """

    if not user_message or not user_message.strip():
        return []

    limit = max(1, int(limit))

    query_words = _tokenize(user_message)

    if not query_words:
        return []

    connection = get_connection()

    exclude_conversation_id = (
        current_conversation_id
        if current_conversation_id is not None
        else -1
    )

    rows = connection.execute(
        """
        SELECT
            m.id,
            m.conversation_id,
            m.role,
            m.content,
            m.created_at,
            c.title
        FROM messages m
        JOIN conversations c
            ON c.id = m.conversation_id
        WHERE m.conversation_id != ?
        ORDER BY m.id DESC
        """,
        (exclude_conversation_id,)
    ).fetchall()

    # Find the strongest matching message in each conversation.
    best_matches = {}

    for row in rows:
        score = _score_message(
            query_words,
            row["content"]
        )

        if score <= 0:
            continue

        conversation_id = row["conversation_id"]

        existing = best_matches.get(conversation_id)

        if existing is None or score > existing["score"]:
            best_matches[conversation_id] = {
                "conversation_id": conversation_id,
                "title": row["title"],
                "message_id": row["id"],
                "score": score
            }

    # Rank conversations by relevance.
    ranked = sorted(
        best_matches.values(),
        key=lambda item: item["score"],
        reverse=True
    )

    results = []

    for match in ranked[:limit]:

        context_messages = _get_context_window(
            connection,
            match["conversation_id"],
            match["message_id"]
        )

        results.append({
            "conversation_id": match["conversation_id"],
            "title": match["title"],
            "score": round(match["score"], 3),
            "messages": context_messages
        })

    connection.close()

    return results


def format_conversations_for_prompt(conversations):
    """
    Convert retrieved conversations into compact
    context for the main AI prompt.
    """

    if not conversations:
        return ""

    sections = []

    for conversation in conversations:

        lines = [
            f"Previous conversation: {conversation['title']}",
            f"Relevance score: {conversation['score']}"
        ]

        for message in conversation["messages"]:
            role = message["role"].capitalize()

            lines.append(
                f"{role}: {message['content']}"
            )

        sections.append(
            "\n".join(lines)
        )

    return "\n\n---\n\n".join(sections)