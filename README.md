# 🚀 dexport — Discord Desktop CLI & AI Agent Remote Control

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Typer](https://img.shields.io/badge/CLI-Typer-green.svg)](https://typer.tiangolo.com/)
[![Protocol](https://img.shields.io/badge/Protocol-Chrome_DevTools_Protocol-red.svg)](https://chromedevtools.github.io/devtools-protocol/)

> **Control Discord Desktop from CLI & Telegram via Chrome DevTools Protocol (CDP)** — No Bot Token, no Admin rights, no client modifications required. Featuring Multi-Model AI Summarization, Interactive Telegram Remote Control & Autonomous Auto-Chat Ghostwriter Persona.

---

## 🌟 Key Highlights

1. **Zero Disk Tokens (In-Memory Auth)**: Connects via **Chrome DevTools Protocol (CDP)** (`--remote-debugging-port=41829`), safely borrowing authorization tokens from RAM in the native Electron runtime. No tokens stored on disk.
2. **Native Chromium TLS Fingerprint**: All API calls execute through `fetch()` inside the Discord desktop context, seamlessly bypassing Cloudflare/WAF bot protections.
3. **Fuzzy Search (Names over IDs)**: Intelligently resolves Server and Channel names using fuzzy matching (accents, slugs, substrings) without memorizing snowflake IDs.
4. **📊 Multi-LLM AI Summarizer**: Summarize and extract channel discussions by date (`--since today`, `3d`, `1w`), user, or keyword using DeepSeek V4, Claude 3.7, GPT-4o, Gemini 2.5, Qwen, or Kimi.
5. **📱 Telegram Remote Control**: Full mobile control with interactive touch buttons and natural language conversational AI agent right inside Telegram.
6. **🤖 Autonomous Auto-Chat Persona**: Automatically detects mentions/replies and generates persona-compliant responses with real typing indicators (`/typing`) and randomized delays.

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/Coder6pack/dexport.git
cd dexport

# Create virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Copy environment template
cp .env.example ~/.dexport.env
```

### 2. Configure Environment (`~/.dexport.env`)

Add your API keys to `~/.dexport.env` once (automatically loaded by CLI):

```env
# AI Provider (Choose one or more)
OPENCODE_API_KEY="opencode-..."         # OpenCode Go (DeepSeek, Claude, Qwen, Kimi, GLM)
OPENAI_API_KEY="sk-..."                 # OpenAI (GPT-4o, o3-mini)
GEMINI_API_KEY="AIza..."                # Google Gemini (Gemini 2.5 Flash/Pro)
ANTHROPIC_API_KEY="sk-ant-..."          # Anthropic (Claude 3.7 Sonnet)

# Telegram Remote Control (Optional)
TELEGRAM_BOT_TOKEN="123456789:ABC..."   # From @BotFather
TELEGRAM_USER_ID="987654321"            # From @userinfobot (Locks permissions to you)
```

---

## 💻 CLI Commands

### 🔹 Account Profile & CDP Status
```bash
dexport status
```

### 🔹 List Joined Servers (Guilds)
```bash
dexport guilds
```

### 🔹 List Channels in a Server
```bash
dexport channels -g "My Server"
```

### 🔹 Read Messages with Advanced Filtering
```bash
# Read today's or past 3 days' messages
dexport read -g "General" -c "chat" --since today
dexport read -g "General" -c "chat" --since 3d

# Filter messages from a specific user in the last 24h
dexport read -g "General" -c "chat" -u "john" --since 24h

# Search for keywords
dexport read -g "General" -c "chat" -q "release"

# Filter messages containing attachments or links
dexport read -g "General" -c "chat" --has-file
dexport read -g "General" -c "chat" --has-link --human-only
```

### 🔹 AI Discussion Summarization
Generates comprehensive summaries and automatically exports reports to `~/Documents/report/report-DD-MM-YYYY.md`.

```bash
# View all supported AI models
dexport models

# Summarize today's discussion using DeepSeek V4 Flash (OpenCode Go)
dexport summarize -g "Work" -c "dev-general" -m deepseek-v4-flash --since today

# Summarize past 3 days using Claude 3.7 Sonnet
dexport summarize -g "Work" -c "dev-general" -m claude-3-7-sonnet --since 3d

# Summarize discussion filtered by user
dexport summarize -g "Work" -c "dev-general" -u "alex" --since 1w

# Summarize OFFLINE using Local LLM (Ollama)
dexport summarize -g "Work" -c "dev-general" -m llama3.2 --base-url http://localhost:11434/v1
```

### 🔹 Send, Reply & React
```bash
# Send message
dexport send -g "Work" -c "general" -m "Hello team from CLI!"

# Reply to a message ID
dexport reply -g "Work" -c "general" --msg-id 123456789012345678 -m "Got it, thanks!"

# Add reaction
dexport react -g "Work" -c "general" --msg-id 123456789012345678 -e "🔥"

# Real-time live message watch
dexport watch -g "Work" -c "general"
```

---

## 📱 Telegram Remote Control (`dexport bot`)

Control Discord directly from your phone through Telegram with interactive buttons and natural conversational AI:

```bash
# Start Telegram daemon
dexport bot
```

### Features:
- **Interactive Touch Keyboards**: 1-tap buttons for `[ 📊 Summarize Today ]`, `[ 💬 Read Recent ]`, `[ 🤖 Enable Auto-Chat ]`, `[ ⚙️ Settings & Models ]`, `[ 🏰 Servers List ]`.
- **In-App Model Switching**: Switch between DeepSeek V4, Claude 3.7, GPT-4o, Gemini, Qwen, Kimi on the fly via Telegram inline buttons.
- **Full Conversational AI Agent**: Chat naturally, ask questions, explain code, or issue Discord commands in plain English.
- **Standby Mode**: Tap `[ ⏸️ Pause Bot ]` when not in use.

---

## 🤖 AI Auto-Chat Ghostwriter Persona (`dexport autochat`)

Automatically replies to mentions, replies, or general channel discussions mimicking your personal writing style:

```bash
# Reply only when tagged or replied to
dexport autochat -g "Work" -c "chat" -P "Friendly, concise (1-2 sentences), helpful with coding questions"

# Participate in general channel conversation with cooldown
dexport autochat -g "Work" -c "chat" --all-chat --cooldown 30 -P "Occasional friendly banter"
```

### Key Capabilities:
- **100% Prompt Compliant**: Strictly adheres to your custom persona instructions, tone, and knowledge.
- **Human Typing Simulation**: Displays *"typing..."* indicator for 2–5 seconds with dynamic delay before sending.
- **Anti-Loop Safety**: Never replies to bots or own messages, equipped with cooldown timers.
- **Telegram Live Alerts**: Sends push notifications to your phone whenever AutoChat replies on Discord.

---

## 🔒 Security & Privacy

- **No Token Storage**: Discord tokens are decrypted in RAM at runtime and never persisted to disk.
- **No Third-Party Telemetry**: Direct connection between your local Discord client, your AI provider, and your Telegram bot.
- **Permissions Locked**: Telegram bot strictly enforces `TELEGRAM_USER_ID` authentication.

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for more information.
