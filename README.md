# my-work-assist

A Webex-to-OpenCode bridge is available in `my-webex-bridge/bridge/bridge.py`.
It polls a Webex room for messages that mention the bot, passes each request to
OpenCode, and posts OpenCode's response back to the room.

## Run the Webex bridge

### Prerequisites

- Python 3.9 or later
- The `requests` Python package
- [OpenCode](https://opencode.ai/) installed and available as `opencode` on your
  `PATH`, with its authentication configured
- A Webex bot added to the room you want to monitor
- The bot's access token and the Webex room ID

Open a PowerShell terminal at the repository root and install the Python
dependency:

```powershell
py -m pip install requests
```

Set the required environment variables for the current terminal. Replace the
example values with your bot token and room ID:

```powershell
$env:WEBEX_BOT_TOKEN = "your-webex-bot-token"
$env:WEBEX_ROOM_ID = "your-webex-room-id"
```

`WEBEX_ROOM_ID` may be the room's Webex ID or its UUID. If your network requires
a proxy, set the optional proxy URL as well:

```powershell
$env:WEBEX_PROXY_URL = "http://proxy.example.com:8080"
```

Start the bridge from the repository root:

```powershell
py .\my-webex-bridge\bridge\bridge.py
```

Keep the process running while the bridge is in use. It checks for new
mentioned messages every three seconds. The bot ignores its own messages and
does not process the room's existing messages when it starts.

### Send a request

Mention the bot in the monitored Webex room and include your request, for
example:

```text
@My assistant summarize my open tickets
```

The leading `@My`, `My`, or `My assistant` text is removed before the request
is passed to OpenCode. OpenCode runs with
`my-webex-bridge/ai_workspace` as its working directory and continues its
session between requests. Configure any OpenCode project settings or MCP
servers needed for your tasks in that workspace.

The bridge writes the request to `my-webex-bridge/ai_workspace/input.txt` and
uses `output.txt` for the response. These files are created or updated while
the bridge runs; do not put credentials in them.

To start a fresh OpenCode session, send one of these messages:

```text
@My assistant /reset
@My assistant /new
@My assistant new session
```

### Stop the bridge

Press **Ctrl+C** in the terminal running the script. The token and proxy values
set with `$env:` are scoped to that PowerShell terminal; set them again in a
new terminal before restarting the bridge.

Do not commit or share your Webex bot token. If it has been exposed, revoke it
and create a replacement.
