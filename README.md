# 🚀 dexport — Discord Desktop CLI & AI Agent Remote Control

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Typer](https://img.shields.io/badge/CLI-Typer-green.svg)](https://typer.tiangolo.com/)
[![Protocol](https://img.shields.io/badge/Protocol-Chrome_DevTools_Protocol-red.svg)](https://chromedevtools.github.io/devtools-protocol/)

> **Điều khiển Discord Desktop từ Command Line & Telegram qua Chrome DevTools Protocol (CDP)** — Không cần Bot Token, không cần quyền Admin, không sửa đổi client. Tích hợp AI Summarizer, Telegram Remote Control & Auto-Chat Ghostwriter Persona.

---

## 🌟 Tính năng nổi bật

1. **Token không lưu đĩa, mượn từ RAM**: Kết nối qua **Chrome DevTools Protocol (CDP)** (`--remote-debugging-port=41829`), mượn Authorization headers từ context Chromium của Discord Desktop.
2. **Request từ Chromium thật**: Giữ trọn TLS fingerprint, User-Agent, cookies, headers — không lo bị Cloudflare/WAF chặn.
3. **CLI-First, tên thay vì ID**: Hỗ trợ tìm kiếm Server và Channel bằng tên tiếng Việt (có dấu hoặc không dấu, fuzzy match) thay vì bắt buộc nhớ ID.
4. **📊 AI Summarizer**: Tóm tắt & phân tích thảo luận theo ngày, người dùng, từ khóa bằng DeepSeek V4, Claude 3.7, GPT-4o, Gemini 2.5, Qwen, Kimi.
5. **📱 Telegram Bot Remote Control**: Bàn phím nút bấm tương tác 1 chạm và ra lệnh bằng tiếng Việt tự nhiên ngay trên điện thoại.
6. **🤖 AI Auto-Chat Persona**: Tự động trả lời tin nhắn trên Discord theo đúng phong cách & Persona của bạn, có mô phỏng "đang gõ phím..." chân thực.

---

## 🚀 Cài đặt nhanh

```bash
# 1. Clone repository
git clone https://github.com/Coder6pack/dexport.git
cd dexport

# 2. Tạo virtualenv và cài đặt package
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 3. Tạo file cấu hình từ template
cp .env.example .env
```


### 2. Các lệnh thông dụng

#### 🔹 Xem thông tin tài khoản hiện tại & trạng thái CDP
```bash
dexport status
```

#### 🔹 Liệt kê danh sách Server (Guilds)
```bash
dexport guilds
```

#### 🔹 Liệt kê kênh trong Server
```bash
dexport channels -g "cú đêm"
```

#### 🔹 Đọc tin nhắn với bộ lọc nâng cao (Ngày, Người dùng, Từ khóa, File/Link)
```bash
# Đọc tin nhắn hôm nay / 3 ngày gần đây
dexport read -g "cú đêm" -c "lười-chat-tổng" --since today
dexport read -g "cú đêm" -c "lười-chat-tổng" --since 3d

# Lọc tin nhắn của 1 người trong 24 giờ qua
dexport read -g "cú đêm" -c "lười-chat-tổng" -u "nguyen" --since 24h

# Tìm kiếm tin nhắn chứa từ khóa "release" hoặc "lỗi"
dexport read -g "cú đêm" -c "lười-chat-tổng" -q "release"

# Chỉ lấy tin nhắn có file đính kèm hoặc có link website
dexport read -g "cú đêm" -c "lười-chat-tổng" --has-file
dexport read -g "cú đêm" -c "lười-chat-tổng" --has-link --human-only
```

#### 🔹 Tổng hợp & Tóm tắt với nhiều Model AI (OpenCode Go, Gemini, OpenAI, Claude, DeepSeek, Ollama)
```bash
# Xem danh sách các model và nhà cung cấp hỗ trợ
dexport models

# 1. Tóm tắt bằng OpenCode Go API (Gói $10/tháng xài DeepSeek-R1, Claude 3.7, Kimi, Qwen...) ⭐
export OPENCODE_API_KEY="opencode-..."

# Dùng DeepSeek-R1 qua OpenCode Go
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -u "nguyen" -m deepseek-r1 --since 3d -o bao_cao.md

# Dùng Claude 3.7 Sonnet qua OpenCode Go
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -m claude-3-7-sonnet --since today -o bao_cao.md

# Dùng Kimi K1.5 / Qwen 2.5 Coder
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -m kimi-k1.5 --since 1w -o bao_cao.md

# 2. Tóm tắt bằng Google Gemini (mặc định)
export GEMINI_API_KEY="AIzaSy..."
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -u "nguyen" --since 3d -o bao_cao.md

# 3. Tóm tắt bằng OpenAI GPT-4o / GPT-4o-mini
export OPENAI_API_KEY="sk-..."
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -m gpt-4o --since today -o bao_cao_today.md

# 4. Tóm tắt bằng Anthropic Claude trực tiếp
export ANTHROPIC_API_KEY="sk-ant-..."
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -m claude-3-7-sonnet-latest -o bao_cao.md

# 5. Tóm tắt OFFLINE bằng Ollama / Local LLM (Llama 3, Qwen 2.5, Mistral...)
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -m llama3.2 --base-url http://localhost:11434/v1 -o bao_cao.md
```



#### 🔹 Xuất tin nhắn ra file Markdown hoặc JSON
Tất cả file báo cáo và xuất dữ liệu mặc định sẽ được lưu tại **`/Users/mac/Documents/report`** với tên định dạng **`report-DD-MM-YYYY.md`** (ví dụ: `report-28-08-2026.md`).

```bash
# 1. Tự động lưu tóm tắt vào /Users/mac/Documents/report/report-DD-MM-YYYY.md
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -m deepseek-v4-flash --since today

# 2. Xuất tin nhắn kênh ra Markdown tại /Users/mac/Documents/report/
dexport read -g "cú đêm" -c "lười-chat-tổng" --limit 100 --export md

# 3. Hoặc chỉ định tên file / đường dẫn tùy ý:
dexport summarize -g "cú đêm" -c "lười-chat-tổng" -o custom_report.md
```


#### 🔹 Gửi tin nhắn
```bash
dexport send -g "cú đêm" -c "lười-chat-tổng" -m "Xin chào anh em từ CLI!"
```

#### 🔹 Trả lời (Reply) tin nhắn
```bash
dexport reply -g "cú đêm" -c "lười-chat-tổng" --msg-id 123456789012345678 -m "Đã nhận thông tin nhé"
```

#### 🔹 Thả reaction cảm xúc
```bash
dexport react -g "cú đêm" -c "lười-chat-tổng" --msg-id 123456789012345678 -e "🔥"
```

#### 🔹 Theo dõi tin nhắn trực tiếp theo thời gian thực (Live Watch)
```bash
dexport watch -g "cú đêm" -c "lười-chat-tổng"
```

---

### 📱 5. Điều khiển từ xa bằng Telegram Bot (Remote Control)

Bạn có thể điều khiển `dexport` đọc, gửi và tóm tắt Discord ngay trên điện thoại thông qua Telegram:

#### 1. Tạo Bot Telegram (30 giây):
1. Mở Telegram, chat với **`@BotFather`**, gõ `/newbot` và làm theo hướng dẫn để lấy `TELEGRAM_BOT_TOKEN`.
2. Lấy User ID của bạn qua bot **`@userinfobot`** để khóa bảo mật (chỉ bạn mới được điều khiển).

#### 2. Khởi chạy Bot:
```bash
export OPENCODE_API_KEY="opencode-..."
export TELEGRAM_BOT_TOKEN="123456789:ABCdef..."
export TELEGRAM_USER_ID="987654321"

# Bật bot lắng nghe
dexport bot
```

#### 3. Nhắn tin tự nhiên cho Bot trên điện thoại:
- *"Tóm tắt kênh ai-lười-chat-tổng bên server Cú Đêm hôm nay bằng deepseek"* ➔ Bot cào chat, tóm tắt và gửi trả lại text + file `.md` về điện thoại!
- *"Đọc 10 tin nhắn mới nhất kênh thông-báo"*
- *"Gửi 'Hello anh em' vào kênh nội-quy"*
- *"Bật autochat kênh lười-chat, trả lời vui vẻ ngắn gọn"*

---

### 🤖 6. AI Auto-Chat Persona (Tự động nhắn tin theo phong cách & Prompt)


Tool có thể tự động trả lời người khác trên Discord như bạn đang online thật:

#### Tính năng:
- **Tuân thủ Prompt 100%**: Bạn chỉ đạo phong cách, xưng hô, tính cách gì (ngắn gọn, hài hước, trả lời code, xưng bro/ae, v.v.), AI sẽ tuân thủ tuyệt đối.
- **Mô phỏng người thật**: Bật trạng thái *"Philip đang soạn tin nhắn..."* trong 2–5 giây và có độ trễ ngẫu nhiên trước khi gửi.
- **Anti-Loop an toàn**: Không bao giờ rep bot khác, có cooldown chống spam.
- **Bật / Tắt linh hoạt**: Qua CLI hoặc trực tiếp trên Telegram.

#### 1. Chạy trên Terminal:
```bash
# Chỉ trả lời khi được tag tên hoặc rep tin nhắn
dexport autochat -g "Cú Đêm AI" -c "ai-lười-chat-tổng" -P "Nói chuyện vui vẻ, ngắn gọn, xưng anh em, hỗ trợ nhiệt tình về code"

# Tự động tham gia trò chuyện cả kênh
dexport autochat -g "Cú Đêm AI" -c "ai-lười-chat-tổng" --all-chat --cooldown 30 -P "Thỉnh thoảng góp vui 1 câu tự nhiên"
```

#### 2. Bật / Tắt qua Telegram từ điện thoại:
- `/autochat on ai-lười-chat-tổng Hãy trả lời ngắn gọn, thân thiện` (Bật)
- `/autochat off` (Tắt)
- `/autochat status` (Xem trạng thái & số tin đã gửi)
- *Hoặc nhắn tự nhiên:* *"Bật autochat kênh lười-chat, giả làm chuyên gia thân thiện"*


