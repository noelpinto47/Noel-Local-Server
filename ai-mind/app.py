import os
import json
import time

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI

from database import (
    init_database,
    create_conversation,
    add_message,
    get_messages,
    get_conversation,
    list_conversations,
    rename_conversation,
    delete_conversation,
    add_memory,
    get_memories,
    add_ignored_memory,
    is_memory_ignored,
    get_ignored_memories,
    delete_memory,
    update_memory,
    memory_exists,
    get_ai_status,
    save_ai_status,
    get_communication_style,
    update_communication_style,
)

load_dotenv()

app = Flask(__name__)

init_database()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    default_headers={
        "X-OpenRouter-Title": "Noel AI Mind"
    }
)

MODEL = os.getenv("AI_MODEL", "openrouter/free")
MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "800"))

AI_STATUS = get_ai_status()

def update_ai_rate_limit_status(error):
    """
    Store OpenRouter rate-limit information from a 429 error.
    """

    global AI_STATUS

    try:
        error_text = str(error)

        if "429" not in error_text:
            return

        AI_STATUS["available"] = False
        AI_STATUS["status"] = "Rate Limited"
        AI_STATUS["last_error"] = error_text

        # The OpenRouter error contains the reset timestamp.
        # Example:
        # X-RateLimit-Reset: 1789257600000

        marker = "X-RateLimit-Reset"

        if marker in error_text:
            reset_part = error_text.split(marker, 1)[1]

            # Extract the first large integer after the marker.
            import re

            match = re.search(r"\d{10,}", reset_part)

            if match:
                reset_ms = int(match.group(0))
                AI_STATUS["reset_at"] = reset_ms / 1000

        # Extract request limit
        marker = "X-RateLimit-Limit"

        if marker in error_text:
            import re

            limit_part = error_text.split(marker, 1)[1]
            match = re.search(r"\d+", limit_part)

            if match:
                AI_STATUS["limit"] = int(match.group(0))

        # Extract remaining requests
        marker = "X-RateLimit-Remaining"

        if marker in error_text:
            import re

            remaining_part = error_text.split(marker, 1)[1]
            match = re.search(r"\d+", remaining_part)

            if match:
                AI_STATUS["remaining"] = int(match.group(0))
        
        save_ai_status(
            AI_STATUS["available"],
            AI_STATUS["status"],
            AI_STATUS["limit"],
            AI_STATUS["remaining"],
            AI_STATUS["reset_at"],
            AI_STATUS["last_error"]
        )

    except Exception as e:
        print("AI status tracking error:", e)

def extract_memory_candidates(messages):
    """
    Ask the AI to identify useful long-term memories
    from the conversation.
    """

    conversation_text = "\n".join(
        f"{message['role']}: {message['content']}"
        for message in messages
    )

    prompt = f"""
Analyze the following conversation and identify information
that would be useful to remember about the user in future conversations.

Only suggest information that is reasonably stable or useful long-term.

Good examples:
- Communication preferences
- Stable technical preferences
- Long-term projects
- Important decisions
- Recurring preferences
- Facts the user explicitly states about themselves

Do NOT suggest:
- Temporary thoughts
- One-time questions
- Things the AI said
- Information merely inferred about the user
- Sensitive personal information

Return ONLY valid JSON in this format:

{{
    "memories": [
        {{
            "content": "Short description of the memory",
            "type": "communication_preference",
            "confidence": 0.0
        }}
    ]
}}

If there are no useful memories, return:

{{
    "memories": []
}}

Conversation:

{conversation_text}
"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=500
        )

        content = response.choices[0].message.content

        if not content:
            return []

        # Remove markdown code fences if the model adds them
        content = content.strip()

        if content.startswith("```"):
            content = content.replace("```json", "", 1)
            content = content.replace("```", "", 1)
            content = content.strip()

        # Convert JSON string into a Python object
        result = json.loads(content)

        candidates = result.get("memories", [])

        # Remove memories that have already been ignored
        filtered_candidates = []

        for candidate in candidates:
            candidate_content = candidate.get("content", "").strip()

            if not candidate_content:
               continue

            if is_memory_ignored(candidate_content):
               continue

            # Also don't suggest something that is already saved
            if memory_exists(candidate_content):
               continue

            filtered_candidates.append(candidate)

        return filtered_candidates

    except Exception as e:
        print("Memory extraction error:", e)
        return []


def extract_communication_style(messages, current_profile):
    """Extract repeated communication preferences, never factual memories."""

    conversation_text = "\n".join(
        f"{message['role']}: {message['content']}"
        for message in messages
    )

    prompt = f"""
Analyze this conversation only for the user's communication preferences.
Return preferences about how the assistant should respond, not facts about
the user, their projects, identity, or life.

Only return a preference when it is explicitly stated or supported by at
least two conversational signals. Ignore one-off requests and preferences
that are uncertain. Existing preferences may be confirmed or changed only
when the conversation provides strong repeated evidence.

Use stable keys such as response_length, technical_detail, tone,
prefers_step_by_step, prefers_examples, and avoids_unnecessary_questions.
Return ONLY valid JSON:
{{
  "observations": [
    {{"key": "response_length", "value": "concise", "confidence": 0.9,
     "evidence_count": 2, "explicit": false}}
  ]
}}

Existing style profile:
{json.dumps(current_profile)}

Conversation:
{conversation_text}
"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400
        )
        content = response.choices[0].message.content

        if not content:
            return []

        content = content.strip()
        if content.startswith("```"):
            content = content.replace("```json", "", 1)
            content = content.replace("```", "", 1).strip()

        observations = json.loads(content).get("observations", [])
        valid = []

        for observation in observations:
            key = observation.get("key", "").strip()
            value = observation.get("value")
            confidence = float(observation.get("confidence", 0))
            evidence_count = int(observation.get("evidence_count", 0))
            explicit = bool(observation.get("explicit", False))

            if (
                key
                and value is not None
                and confidence >= 0.75
                and (explicit or evidence_count >= 2)
            ):
                valid.append({"key": key, "value": value})

        return valid
    except Exception as e:
        print("Communication style extraction error:", e)
        return []


def learn_communication_style(messages):
    """Update style only periodically, after enough user messages exist."""

    user_message_count = sum(message["role"] == "user" for message in messages)

    if user_message_count < 3 or user_message_count % 3 != 0:
        return

    style = get_communication_style()
    observations = extract_communication_style(messages, style["profile"])

    if not observations:
        return

    profile = dict(style["profile"])
    for observation in observations:
        profile[observation["key"]] = observation["value"]

    update_communication_style(
        profile,
        style["observation_count"] + len(observations)
    )

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()

    conversation_id = data.get("conversation_id")
    message = data.get("message", "").strip()

    if not message:
        return jsonify({
            "error": "Message cannot be empty"
        }), 400

    # Create a conversation if one doesn't exist
    if not conversation_id:
        conversation_id = create_conversation()
    elif not get_conversation(conversation_id):
        return jsonify({
            "error": "Conversation not found"
        }), 404

    try:
        # Save user's message
        add_message(
            conversation_id,
            "user",
            message
        )

        # Get conversation history
        messages = get_messages(conversation_id)

        # Get approved long-term memories
        memories = get_memories()
        communication_style = get_communication_style()["profile"]

        # Build memory context
        memory_text = ""

        if memories:
            memory_text = "\n".join(
                f"- {memory['content']}"
                for memory in memories
            )

        style_text = ""
        if communication_style:
            style_text = "\n".join(
                f"- {key.replace('_', ' ').capitalize()}: {value}"
                for key, value in communication_style.items()
            )

        system_message = """
You are Noel's personal AI assistant.

You should be helpful, natural, and conversational.

The following information has been explicitly saved as
long-term memory about the user:

{memory_text}

Use these memories when they are relevant to the user's question.

The following communication style preferences were learned from repeated
conversation patterns. Use them when appropriate, but do not mention them
or treat them as factual memories:

{style_text}

Do not mention the memory system or say that you are retrieving
memories unless the user explicitly asks about it.

Do not invent additional facts about the user.
""".format(
            memory_text=memory_text
            if memory_text
            else "No saved memories yet.",
            style_text=style_text
            if style_text
            else "No learned communication preferences yet."
        )

        # Send system instructions + conversation to OpenRouter
        messages_for_ai = [
            {
                "role": "system",
                "content": system_message
            }
        ] + messages

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages_for_ai,
            max_tokens=MAX_TOKENS
        )

        choice = response.choices[0]
        content = choice.message.content

        if content is None:
            return jsonify({
                "error": "The model returned no text response.",
                "finish_reason": choice.finish_reason
            }), 500

        # Save AI response
        add_message(
            conversation_id,
            "assistant",
            content
        )

        learn_communication_style(messages + [{"role": "assistant", "content": content}])

        return jsonify({
            "conversation_id": conversation_id,
            "response": content
        })

    except Exception as e:
        update_ai_rate_limit_status(e)
        return jsonify({
            "error": str(e)
        }), 500

@app.route("/api/conversation/<int:conversation_id>", methods=["GET"])
def conversation(conversation_id):
    conversation_data = get_conversation(conversation_id)

    if not conversation_data:
        return jsonify({
            "error": "Conversation not found"
        }), 404

    messages = get_messages(conversation_id)

    return jsonify({
        "conversation_id": conversation_id,
        "title": conversation_data["title"],
        "created_at": conversation_data["created_at"],
        "updated_at": conversation_data["updated_at"],
        "messages": messages
    })


@app.route("/api/conversations", methods=["GET"])
def conversations():
    search = request.args.get("search", "")

    return jsonify({
        "conversations": list_conversations(search=search)
    })


@app.route("/api/conversations/<int:conversation_id>", methods=["PATCH"])
def rename_conversation_route(conversation_id):
    data = request.get_json() or {}
    title = " ".join(str(data.get("title", "")).split()).strip()

    if not title:
        return jsonify({
            "error": "Conversation title cannot be empty"
        }), 400

    if len(title) > 80:
        return jsonify({
            "error": "Conversation title cannot exceed 80 characters"
        }), 400

    if not rename_conversation(conversation_id, title):
        return jsonify({
            "error": "Conversation not found"
        }), 404

    return jsonify({
        "conversation": get_conversation(conversation_id)
    })


@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
def delete_conversation_route(conversation_id):
    if not delete_conversation(conversation_id):
        return jsonify({
            "error": "Conversation not found"
        }), 404

    return jsonify({
        "success": True,
        "conversation_id": conversation_id
    })

@app.route("/api/memories", methods=["POST"])
def save_memory():
    data = request.get_json()

    content = data.get("content", "").strip()
    memory_type = data.get("type", "general")
    confidence = float(data.get("confidence", 0.5))

    if not content:
        return jsonify({
            "error": "Memory content cannot be empty"
        }), 400

    # Prevent exact duplicate memories
    existing_id = memory_exists(content)

    if existing_id:
        return jsonify({
            "success": False,
            "duplicate": True,
            "id": existing_id,
            "message": "This memory already exists."
        })

    memory_id = add_memory(
        content,
        memory_type,
        confidence
    )

    return jsonify({
        "success": True,
        "id": memory_id
    })

@app.route("/api/memories", methods=["GET"])
def get_all_memories():
    return jsonify({
        "memories": get_memories()
    })

@app.route("/memories")
def memory_page():
    return render_template("memories.html")


@app.route("/api/communication-style", methods=["GET"])
def communication_style():
    return jsonify(get_communication_style())


@app.route("/api/communication-style", methods=["PUT"])
def save_communication_style():
    data = request.get_json() or {}
    profile = data.get("profile")

    if not isinstance(profile, dict):
        return jsonify({"error": "Style profile must be an object."}), 400

    cleaned_profile = {
        str(key).strip(): value
        for key, value in profile.items()
        if str(key).strip() and value is not None and value != ""
    }
    current = get_communication_style()
    update_communication_style(cleaned_profile, current["observation_count"])

    return jsonify(get_communication_style())


@app.route("/api/communication-style", methods=["DELETE"])
def reset_communication_style():
    update_communication_style({}, 0)
    return jsonify(get_communication_style())

@app.route("/api/memory-candidates/<int:conversation_id>", methods=["GET"])
def memory_candidates(conversation_id):

    messages = get_messages(conversation_id)

    if not messages:
        return jsonify({
            "error": "Conversation not found"
        }), 404

    candidates = extract_memory_candidates(messages)

    return jsonify({
        "candidates": candidates
    })


@app.route("/api/memories/<int:memory_id>", methods=["DELETE"])
def delete_memory_route(memory_id):

    delete_memory(memory_id)

    return jsonify({
        "success": True
    })

@app.route("/api/memories/<int:memory_id>", methods=["PUT"])
def update_memory_route(memory_id):
    data = request.get_json()

    content = data.get("content", "").strip()
    memory_type = data.get("type", "general")
    confidence = float(data.get("confidence", 0.5))

    if not content:
        return jsonify({
            "error": "Memory content cannot be empty"
        }), 400

    update_memory(
        memory_id,
        content,
        memory_type,
        confidence
    )

    return jsonify({
        "success": True
    })

@app.route("/api/memories/ignore", methods=["POST"])
def ignore_memory():
    data = request.get_json()

    content = data.get("content", "").strip()

    if not content:
        return jsonify({
            "error": "Memory content cannot be empty"
        }), 400

    add_ignored_memory(content)

    return jsonify({
        "success": True
    })

@app.route("/api/ai-status", methods=["GET"])
def ai_status():

    # Automatically become available again after reset.
    if (
        not AI_STATUS["available"]
        and AI_STATUS["reset_at"]
        and time.time() >= AI_STATUS["reset_at"]
    ):
        AI_STATUS["available"] = True
        AI_STATUS["status"] = "Available"
        AI_STATUS["last_error"] = None
        AI_STATUS["remaining"] = None

    save_ai_status(
        AI_STATUS["available"],
        AI_STATUS["status"],
        AI_STATUS["limit"],
        AI_STATUS["remaining"],
        AI_STATUS["reset_at"],
        AI_STATUS["last_error"]
    )

    return jsonify(AI_STATUS)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8081,
        debug=False
    )