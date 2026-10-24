import base64
from datetime import datetime, timezone
import json
import logging
import os
import re
import subprocess
import time
from typing import Any, Optional
import requests

# Set logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s[%(name)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

WEBEX_BASE_URL = "https://webexapis.com/v1"
WEBEX_MESSAGES_URL = f"{WEBEX_BASE_URL}/messages"
WEBEX_PEOPLE_ME_URL = f"{WEBEX_BASE_URL}/people/me"

# Environment variables
WEBEX_TOKEN = os.getenv("WEBEX_BOT_TOKEN", "").strip()
TARGET_ROOM_ID = os.getenv("WEBEX_ROOM_ID", "").strip()
PROXY_URL = (os.getenv("WEBEX_PROXY_URL") or "").strip()
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# Isolated workspace directory
WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ai_workspace"))
INPUT_FILE = os.path.join(WORKSPACE_DIR, "input.txt")
OUTPUT_FILE = os.path.join(WORKSPACE_DIR, "output.txt")


def normalize_webex_room_id(room_id: str) -> str:
    """Return the canonical encoded Webex room ID form."""
    room_id = str(room_id).strip()
    if room_id.startswith("Y2lzY29zcGFyazovL3VzL1JPT00v"):
        return room_id
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        room_id,
    ):
        encoded = base64.b64encode(f"ciscospark://us/ROOM/{room_id}".encode("utf-8")).decode("utf-8")
        return encoded.rstrip("=")
    return room_id


def get_bot_person_id(token: str) -> Optional[str]:
    """Retrieve the bot's own personId to ignore its own posted messages."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(WEBEX_PEOPLE_ME_URL, headers=headers, proxies=PROXIES, timeout=10)
        resp.raise_for_status()
        return resp.json().get("id")
    except requests.exceptions.RequestException as e:
        logging.error("Failed to fetch bot personId: %s", e)
        return None


def send_to_webex(room_id: str, token: str, markdown_text: str) -> Optional[str]:
    """Send message to Webex room."""
    try:
        payload = {"roomId": room_id, "markdown": markdown_text}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.post(WEBEX_MESSAGES_URL, json=payload, headers=headers, proxies=PROXIES, timeout=15)
        resp.raise_for_status()
        resp_data = resp.json()
        return resp_data.get("id") if isinstance(resp_data, dict) else None
    except requests.exceptions.RequestException as e:
        logging.exception("Failed to send to Webex room %s: %s", room_id, e)
        return None


def fetch_latest_messages(room_id: str, token: str, max_messages: int = 5) -> list[dict[str, Any]]:
    """Poll the room for recent messages where the bot is mentioned."""
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "roomId": room_id,
        "mentionedPeople": "me",  # Required for bots in group rooms
        "max": max_messages,
    }
    try:
        resp = requests.get(WEBEX_MESSAGES_URL, headers=headers, params=params, proxies=PROXIES, timeout=10)
        resp.raise_for_status()
        return list(reversed(resp.json().get("items", [])))
    except requests.exceptions.RequestException as e:
        logging.error("Failed to fetch messages: %s", e)
        return []


def run_opencode_task(reset_session: bool = False) -> str:
    """Execute OpenCode on Windows inside the isolated directory, continuing the session."""
    if os.path.exists(OUTPUT_FILE):
        try:
            os.remove(OUTPUT_FILE)
        except OSError:
            pass

    user_prompt = ""
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            user_prompt = f.read().strip()

    instruction = (
        f"Task from input.txt: '{user_prompt}'. "
        f"If needed, use your Jira MCP tool to fetch or update tickets. "
        f"Write your final response to output.txt."
    )

    command = ["opencode", "run", "--auto"]

    # Continue the active session unless a reset was explicitly requested
    if not reset_session:
        command.append("-c")

    command.append(instruction)

    try:
        result = subprocess.run(
            command,
            cwd=WORKSPACE_DIR,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        cli_output = result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "❌ OpenCode execution timed out."
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        logging.error("OpenCode exited with error: %s", error_msg)
        return f"❌ OpenCode error: {error_msg}"

    # 1. Prefer reading output.txt if OpenCode generated it
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return content

    # 2. Fallback to CLI stdout if output.txt was not generated
    if cli_output:
        return cli_output

    return "❌ Error: AI completed execution without writing output."


def main():
    if not WEBEX_TOKEN or not TARGET_ROOM_ID:
        logging.error("WEBEX_BOT_TOKEN and WEBEX_ROOM_ID environment variables must be set.")
        return

    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    room_id = normalize_webex_room_id(TARGET_ROOM_ID)

    bot_person_id = get_bot_person_id(WEBEX_TOKEN)
    if not bot_person_id:
        logging.error("Could not determine bot identity. Check your WEBEX_BOT_TOKEN.")
        return

    logging.info("Bridge started. Monitoring Webex room: %s", room_id)

    processed_message_ids = set()
    start_time = datetime.now(timezone.utc)

    # Seed recent messages so history is not reprocessed on boot
    initial_messages = fetch_latest_messages(room_id, WEBEX_TOKEN, max_messages=20)
    for m in initial_messages:
        if m.get("id"):
            processed_message_ids.add(m["id"])

    logging.info("Seeded %d existing message(s). Listening for new messages...", len(processed_message_ids))

    while True:
        try:
            messages = fetch_latest_messages(room_id, WEBEX_TOKEN, max_messages=5)
            for msg in messages:
                msg_id = msg.get("id")
                sender_id = msg.get("personId")
                text = msg.get("text", "").strip()
                created_str = msg.get("created", "")

                if not msg_id or msg_id in processed_message_ids:
                    continue

                processed_message_ids.add(msg_id)

                if sender_id == bot_person_id:
                    continue

                # Ignore messages posted before this script process was launched
                if created_str:
                    try:
                        msg_time = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        if msg_time < start_time:
                            continue
                    except ValueError:
                        pass

                logging.info("Received new message from user: %s", text)

                # Clean leading bot mentions (@My assistant, My assistant, My)
                cleaned_text = re.sub(r"^@?My(?:\s+assistant)?\s*", "", text, flags=re.IGNORECASE).strip()
                final_prompt = cleaned_text if cleaned_text else text
                logging.info("Cleaned prompt for AI: '%s'", final_prompt)

                # Context reset command
                if final_prompt.lower() in ["/reset", "/new", "new session"]:
                    logging.info("Resetting OpenCode session context...")
                    run_opencode_task(reset_session=True)
                    send_to_webex(room_id, WEBEX_TOKEN, "🔄 *OpenCode session context has been reset.*")
                    continue

                send_to_webex(room_id, WEBEX_TOKEN, "⏳ *Working on your request ...*")

                with open(INPUT_FILE, "w", encoding="utf-8") as f:
                    f.write(final_prompt)

                ai_response = run_opencode_task(reset_session=False)

                # Handle Webex character limit
                if len(ai_response) > 7000:
                    chunks = [ai_response[i:i + 7000] for i in range(0, len(ai_response), 7000)]
                    for chunk in chunks:
                        send_to_webex(room_id, WEBEX_TOKEN, chunk)
                else:
                    send_to_webex(room_id, WEBEX_TOKEN, ai_response)

        except Exception as e:
            logging.exception("Unexpected error in poll loop: %s", e)

        time.sleep(3)


if __name__ == "__main__":
    main()