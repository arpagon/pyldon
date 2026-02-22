# Pyldon

Personal AI assistant written in Python. Runs [pi.dev](https://github.com/badlogic/pi-mono) agents in isolated Docker containers with per-room filesystem separation. Rewrite of [NanoClaw](https://github.com/arpagon/nanoclaw) (TypeScript).

## Why

NanoClaw proved the architecture: container-isolated agents, per-room memory, scheduled tasks, Matrix as the communication channel. But it's written in TypeScript, and the Python ecosystem is where AI tooling lives.

Pyldon is NanoClaw's architecture rewritten in Python. Same security model (OS-level container isolation, not application-level permission checks). Same philosophy (small enough to understand). Better ecosystem.

## Philosophy

**Small enough to understand.** ~3,400 lines of code. Single Python process. A handful of modules. No microservices, no message queues, no abstraction layers.

**Secure by isolation.** Agents run in Docker containers. They can only see explicitly mounted directories. Shell access is safe because commands execute inside the container.

**Built for one user.** Not a framework. Working software that fits your exact needs. Fork it and modify it.

## What It Does

- **Matrix I/O** - Message your assistant from any Matrix client (Element, FluffyChat, etc.)
- **E2EE** - End-to-end encryption support
- **Per-room isolation** - Each room gets its own filesystem, memory (`AGENTS.md`), and container sandbox
- **Main channel** - Admin room for cross-room management
- **Scheduled tasks** - Cron, interval, and one-shot tasks that run as full containerized agents
- **Voice-to-text** - Transcribes voice messages using [Parakeet TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) via [parakeet-rs](https://github.com/altunenes/parakeet-rs) (25 languages, ~2s on CPU)
- **Image vision** - Photos sent via Matrix are passed to the LLM for visual analysis
- **Markdown rendering** - Rich formatting (tables, code blocks, headings) rendered natively in Matrix
- **Web access** - Search and fetch content
- **Browser automation** - Chromium inside the sandbox for scraping, screenshots, PDFs
- **Configurable identity** - `IDENTITY.md` defines your assistant's personality

## Architecture

```
Matrix (matrix-nio) → SQLite → Event handler → Docker Container (pi.dev RPC) → Response
                                     │
                              Voice messages → parakeet-cli (Rust/ONNX) → text
                              Images → base64 → pi RPC images[] → Claude Vision
```

Single Python process. Agents execute in isolated Docker containers via [pi.dev](https://github.com/badlogic/pi-mono) in RPC mode. IPC via filesystem JSON files. No daemons, no queues.

## Quick Start

```bash
git clone https://github.com/arpagon/pyldon.git
cd pyldon
uv sync
./container/build.sh
cp .env.example .env  # Configure your provider credentials
uv run pyldon
```

## Requirements

- Linux or macOS
- Python 3.14+ with [uv](https://docs.astral.sh/uv/)
- [Docker](https://docker.com/products/docker-desktop)
- [ffmpeg](https://ffmpeg.org/) (for voice message conversion)
- A Matrix account with access token
- AI provider credentials (AWS Bedrock, Anthropic API, OpenAI, etc.)

## Building

### Agent container (required)

```bash
./container/build.sh
```

Builds `pyldon-agent:latest` — Node.js 22 + pi.dev + Chromium. This is the sandbox where the AI runs.

### Voice-to-text (optional)

Pyldon uses [parakeet-rs](https://github.com/altunenes/parakeet-rs), a Rust wrapper around NVIDIA's Parakeet TDT v3 ONNX model. Supports 25 languages including Spanish, English, French, German, etc.

**1. Build the CLI binary:**

```bash
cd container/stt/parakeet-cli
cargo build --release
cp target/release/parakeet-cli ../../data/stt/
```

Or with CUDA GPU support:
```bash
# Cargo.toml already has features = ["cuda"]
cargo build --release
```

**2. Download the ONNX model (int8, ~640MB):**

```bash
mkdir -p data/models/tdt
cd data/models/tdt
curl -L "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main/encoder-model.int8.onnx" -o encoder-model.onnx
curl -L "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main/decoder_joint-model.int8.onnx" -o decoder_joint-model.onnx
curl -L "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main/vocab.txt" -o vocab.txt
curl -L "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main/nemo128.onnx" -o nemo128.onnx
```

For full precision (larger, slightly better quality):
```bash
curl -L "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main/encoder-model.onnx" -o encoder-model.onnx
curl -L "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main/encoder-model.onnx.data" -o encoder-model.onnx.data
curl -L "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main/decoder_joint-model.onnx" -o decoder_joint-model.onnx
```

**3. Enable STT in `.env`:**

```bash
PYLDON_STT_ENABLED=true
```

**How it works:** Matrix voice messages (OGG/Opus, encrypted) → decrypt → ffmpeg converts to WAV 16kHz → parakeet-cli transcribes → text injected as `[🎤 Voz]: ...` → processed by pi agent normally.

## Configuration

Key environment variables in `.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `ASSISTANT_NAME` | Bot display name | `Andy` |
| `PYLDON_PI_MODEL` | LLM model ID | — |
| `PYLDON_PI_PROVIDER` | pi.dev provider | `amazon-bedrock` |
| `CONTAINER_IMAGE` | Agent Docker image | `pyldon-agent:latest` |
| `CONTAINER_TIMEOUT` | Container timeout (ms) | `300000` |
| `PYLDON_STT_ENABLED` | Enable voice-to-text | `false` |
| `TZ` | Timezone for scheduled tasks | `UTC` |

## Lineage

| Project | Role |
|---------|------|
| [NanoClaw](https://github.com/arpagon/nanoclaw) | Direct ancestor (TypeScript) |
| [pi-mono](https://github.com/badlogic/pi-mono) | Agent engine (pi.dev) |
| [parakeet-rs](https://github.com/altunenes/parakeet-rs) | Voice-to-text (Rust/ONNX) |

## License

MIT
