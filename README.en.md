# RP — Private-message roleplay with persistent plans

[中文](README.md) · [English](README.en.md)

RP is a self-hosted roleplay service for **QQ private messages through OneBot v11 / NapCat**, with a separate Web administration interface. It uses **Python 3.13+, FastAPI, SQLite, Vue 3, TypeScript, Vite and Element Plus** in a single backend process.

Each contact has an independent storyline, conversation, relationship and memory, even when several contacts use the same character card. The current scope is personal deployment with one administrator. Group chats, social feeds, Web chat, voice and WeChat are outside the current release.

## Experience and execution are separate

Natural dialogue, emotional sensitivity, character consistency and the quality of future messages depend on the selected model, character card and preset. The bundled prompts describe desired behavior; they do not guarantee that every model will behave naturally.

The application manages context and permissions, validates plans, cancels obsolete work, persists scheduled actions and records gateway acknowledgements. A successful connection check is not proof of a valid roleplay plan, and a valid plan is not proof that QQ acknowledged the message.

## How conversations continue

A model response can include three kinds of messages:

| Field | Purpose |
| --- | --- |
| `messages` | Immediate replies, usually one or two bubbles, with a prompt preference for at most three |
| `followups` | An ordered sequence to send if no new user message arrives |
| `deliveries` | Longer-term arrangements that can be kept, revised or canceled |

For example, after helping with a problem, a character might plan a gentle check-in a few minutes later. If the user replies first, remaining short-term messages are canceled and the next model request uses the new conversation. If the user remains silent, the scheduler sends the saved message without another generation call. Planning still consumes output tokens; execution of a saved message does not require another inference call.

The entire plan must be received and validated before the first bubble is sent. Only messages acknowledged by the gateway enter the delivered conversation history. Drafts and canceled branches are not treated as something the character actually said. Delivery with an unknown outcome is not automatically resent.

## Context, memory and cache reuse

Context policies support cost-oriented and continuity-oriented modes, configurable context windows and batched history eviction for each conversation. Stable instructions and character material precede selected memory and history; current input and runtime information are added later. Original conversation records are stored independently from the subset sent to the model.

In some continuous short-text conversations in the personal deployment, using the same connection and stable settings without retrieval or images, the reported cache-read share was approximately **95–97%**. This is a conditional observation, not a cross-provider benchmark or a promised hit rate. Provider cache behavior and expiry, prefix changes, history eviction and new input size all affect the result. Statistics use paired provider-reported input/cache-read usage, and missing usage remains unknown. Explicit Gemini caching requires support from the selected endpoint and model.

Private memories retain evidence references. The administration interface supports memory review, context occupancy, conversation archives, delivery queues, canceled-plan review, usage estimates and backups. Clearing conversation context and forgetting private memories are separate actions.

## Model connections

Generation adapters cover Chat Completions, Responses, Anthropic Messages and Gemini Generate Content. Provider settings and protocol selection are explicit; the server does not infer support from a model name.

Under a model's tool strategy, choose **System default / JSON plan / Native tool submission**. System default uses JSON plans for DeepSeek Chat and Responses, and native tool submission for other providers. Read-only queries remain native tool calls; the final plan passes the same business validation in either mode. The application does not silently switch protocol or disable thinking after an error.

| Provider / protocol | Plan output and compatibility |
| --- | --- |
| DeepSeek Chat | JSON object via `response_format`; thinking settings retained |
| DeepSeek Responses | JSON object via `text.format`; native query continuation retained |
| DeepSeek Messages | Native tools; JSON format fields are not enabled for this adapter |
| OpenAI Chat / Responses | Native tools by default; strict JSON Schema available |
| Claude Messages | Native tools by default; optional `output_config.format` with thinking/effort compatibility handling |
| Gemini | Native tools by default; optional JSON MIME/Schema; native parts and thought signatures retained |
| Compatible gateways | Support depends on the endpoint's actual implementation; configure explicitly |

An endpoint may accept a minimal request while rejecting the full production schema or a combination of thinking, queries and structured output. Inspect the recorded request diagnostics when troubleshooting.

## Quick start

Requirements: **Python 3.13+**, **Node.js 20.19+ with npm**, and your own NapCat installation for QQ connectivity.

### Windows

From the repository directory, run these launchers in order:

1. `安装依赖.bat` — create the local virtual environment, install dependencies and build the Web interface.
2. `初始化.bat` — create the administrator and set a password. There is no default password. Do not initialize an existing deployment again.
3. `启动.bat` — start the service and open `http://127.0.0.1:18765` in your browser.

### Linux / manual installation

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -e backend
npm --prefix web install
npm --prefix web run build
.venv/bin/python -m rp_server init
.venv/bin/python -m rp_server serve
```

### Docker Compose

```sh
cp deploy/.env.example deploy/.env
# Set RP_DOMAIN to your own domain, without a scheme or path.
docker compose --env-file deploy/.env -f deploy/compose.yaml build rp
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm rp init --username admin
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
```

The generic deployment includes Caddy for HTTPS. NapCat must be installed and logged in separately. To expose RP only to NapCat on the same host, also pass `-f deploy/compose.same-host.yaml` on Compose commands. A NapCat container must share the RP network and use its service name; its own loopback address does not point at the host.

## First configuration

1. Add a model connection, choose its provider and protocol, enter its base URL and API key, add a model and assign the main narrative purpose. A minimal plan check does not validate the entire production context.
2. Create or import a character and optional worldbook; review the bundled editable preset. SillyTavern character cards and compatible presets can be imported.
3. Add a QQ account in the administration interface. Configure NapCat with the generated reverse WebSocket URL, its independent connection token, the Universal channel and array message format.
4. Send a private message to the bot, bind the contact to a character, select a context policy and enable the relevant reply controls.

API keys are stored encrypted using the server master key and are not returned in plaintext by management APIs. The Web interface supports a consistent database backup. Full recovery also requires attachments and the matching master key. Stop the service before replacing its live database, retain the original data, and do not copy a live SQLite main file as a substitute for a consistent backup.

`RP_DATA_DIR` selects the data directory, `RP_MASTER_KEY_FILE` the master-key file, and `RP_STATIC_DIR` the built Web interface. Set `RP_PUBLIC_URL` to match your actual browser origin; extra development origins use `RP_ALLOWED_ORIGINS`. The application does not load `.env` automatically; Compose passes its configured environment. Keep the backend single-process and do not add workers or delete data volumes as a restart procedure.

## Troubleshooting

Follow **input record → model run → delivery record**:

- No input record: check NapCat login, reverse WebSocket, token and array message format.
- Input without a run: check global reply settings, contact pause status and debounce/cancellation.
- HTTP 400: inspect the actual endpoint and its model, tool, thinking and structured-output support.
- HTTP 200 without a usable response: inspect the empty-response diagnostic. Complete empty non-streaming RP responses have one controlled retry after three seconds, subject to the shared retry limit and run timeout.
- Missing or invalid plan: inspect truncation, schema validation and unauthorized source/material IDs. Plan repair and transport retries are separate.
- Pending or canceled messages: inspect due times, pause/quiet-time rules and intervening user input.
- Unknown delivery: check the actual QQ conversation before resolving it manually; the server does not resend automatically.

HTTP success does not mean generation is usable or free. Failed, canceled and retried requests may incur charges; only usage returned by the provider can be accounted for, and reasoning tokens are not added twice.

## Project files

`backend/` contains the service, `web/` the administration source, `deploy/` generic container templates, and `scripts/` Windows setup/start helpers. The two README files are the documentation included in this public snapshot.

The public repository excludes private chats, credentials, personal character data, local models, production server settings and private reference corpora. Its name is provisional.

## Sources and licensing

Model protocols, providers and some infrastructure were extracted or adapted from the author's own Python Harness. `SOURCE_ORIGINS.json` records relative source paths and content fingerprints. RP runs independently of that desktop project and does not include its private configuration, data or Qt interface.

Dependencies are listed in `backend/pyproject.toml`, `web/package.json` and `web/package-lock.json` and retain their own licenses. Downloaded reference projects and game dialogue corpora are not bundled. A project license has not yet been selected for this source publication.

Protocol and design references include [OneBot v11](https://github.com/botuniverse/onebot-11), [NapCatQQ](https://github.com/NapNeko/NapCatQQ), [SillyTavern](https://github.com/SillyTavern/SillyTavern), [HDS-Interlude](https://github.com/ISEKAI-MomoiCore/HDS-Interlude), [Character Card V2](https://github.com/malfoyslastname/character-card-spec-v2) and [Character Card V3](https://github.com/kwaroran/character-card-spec-v3). These repositories are not redistributed with this snapshot.
