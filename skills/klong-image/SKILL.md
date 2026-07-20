---
name: klong-image
description: Browse image prompt libraries, launch or deploy the image workspace, or generate and edit single images and controlled concurrent batches through the 小恐龙 API using OpenAI-compatible GPT image models or native Gemini image models. Use when the user asks Codex to find or browse image prompts, open a local or hosted image-generation webpage, draw, generate one or more images, perform image-to-image editing, create variants, compare 小恐龙 image models, or save generated raster images.
---

# 小恐龙生图

Use `scripts/generate.py` for deterministic API calls. Never put an API key in a prompt, source file, command argument, or committed config. Direct CLI calls and Prompt Studio share `~/.klong-image/settings.json` and use its active connection. Process or uncommitted `.env` credentials appear as a read-only `environment` connection and are used only when that connection is active. Copy `.env.example` to `.env` in the Skill directory when file-based configuration is needed.

## Prompt Studio

When the user asks to open, launch, or use the local prompt workspace, handle the launch for them. Do not ask the user to run Python or `npm run dev`. If Prompt Studio is already running at `http://127.0.0.1:8765`, open that page and reuse it. Otherwise, run:

```shell
python <skill-dir>/scripts/prompt_studio.py
```

Keep the server process running and report the opened local URL. The command binds to `127.0.0.1:8765`, opens the browser, and saves generated files under the shared output directory. Resolution order is `--output-dir`, `KLONG_OUTPUT_DIR`, the location saved in Prompt Studio, then `outputs/prompt-studio` in the launch directory. On the first launch it downloads all built-in prompt sources in the background, then caches them under `~/.klong-image`. Later launches load the cache immediately. Use `--refresh` to force a full update, `--port <port>` when 8765 is occupied by another application, or `--output-dir <path>` to lock the gallery/output directory for that launch. Use `npm run dev` only when explicitly developing the Vue source; it is not the end-user launch path.

Prompt Studio includes multi-connection management, `/v1/models` connection testing, persistent storage-location settings, real image generation, and a paginated gallery. Each connection keeps its own name, API address, encrypted key, synchronized model list, and default model. Users can add, switch, test, update, or delete connections from the local UI, and the creation workspace selects a connection before showing that connection's image models. On Windows, direct Codex generation automatically uses the same active connection even when an environment connection also exists. It indexes supported image files under the shared output directory and records task metadata under its `.klong/jobs` subdirectory. Direct Codex generation and web generation use the same manifest schema, so prompts, models, sizes, timings, failures, per-image details, history items, and gallery entries remain consistent. The gallery supports searching, sorting, configurable page sizes, current-page or all-filtered-result selection, batch ZIP downloads, and confirmed permanent deletion from local disk.

Keep the server local. Do not change its host binding or expose it through a tunnel. Existing keys are never returned to the browser; only masked hints are shown. On Windows, keys entered in the UI are encrypted with DPAPI for the current user; on other systems they remain in memory for the current process only. `KLONG_API_KEY` and `KLONG_BASE_URL` expose a read-only environment connection alongside UI-managed connections. Generation is delegated to `generate.py` by the local server, and each queued job captures its selected connection so later UI switching cannot change the key used by that job.

## Hosted Browser Workspace

The Vue workspace uses one build for local and hosted use. The local Python server injects its session token and the UI automatically uses the Python API, shared filesystem gallery, `.env`, and Codex history. A static deployment has no injected token and automatically uses direct API requests plus origin-scoped IndexedDB instead. Do not add a user-facing mode switch or require environment variables for hosted users.

In browser mode, each visitor configures their own connection. API keys are encrypted at rest with a non-extractable Web Crypto key, and keys, images, settings, and history stay in that browser; Vercel does not receive or store them. Browser generation stops if the page is closed, and another browser or device does not share the data. Use `scripts/export_prompt_library.py` after refreshing local prompt sources to update the bundled hosted snapshot before a release.

## Workflow

1. For direct CLI or Codex generation, use the active connection saved by Prompt Studio. Select the read-only `environment` connection in Prompt Studio when `KLONG_API_KEY` should be used. On macOS and Linux, UI-entered keys are process-only, so use the environment connection for direct CLI calls.
2. If current availability matters, run `--list-models`. Choose `gpt-image-2` by default, or `gemini-3.1-flash-image-preview` when native Gemini is requested.
3. Save direct Codex output in the directory resolved by `connection_store.resolve_output_directory()`, which honors `KLONG_OUTPUT_DIR`, Prompt Studio's saved location, then `<current-workspace>/outputs/prompt-studio`. Use a descriptive filename inside that directory. `generate.py` automatically records the task under `.klong/jobs`; do not create or edit manifests manually. Only write elsewhere when the user explicitly requests another location, and note that outputs outside the shared gallery are not added to web history.
4. For image-to-image work, identify the source PNG, JPEG, or WebP file and pass it with `--input-image`.
5. Run:

```shell
python <skill-dir>/scripts/generate.py --model <model> --prompt "<prompt>" --name "<descriptive-name>"
```

6. While the command runs, relay meaningful progress from stderr when the user is waiting. The script reports request starts, retries, completions, failures, and a heartbeat every 30 seconds. Never invent a percentage because the upstream API does not expose one.
7. Verify that each successful output file exists and is non-empty. When visual inspection tools are available, inspect the images before reporting completion.
8. Parse the final job JSON from stdout. Its top-level task fields match `/api/jobs/<id>`, and generation details are under `result`, just like the web UI. Report a concise summary plus a Markdown result table. Include task name/status, prompt, model, mode, total duration, requested/succeeded/failed counts, and per-image status, duration, actual dimensions, file size, attempts, and absolute output path or error. Long prompts may be collapsed in prose, but do not omit them from the persisted task record. Do not report the secret or raw Base64 payload.

Use a result table shaped like this:

| Index | Status | Duration | Dimensions | Size | Attempts | Output / Error |
| --- | --- | ---: | --- | ---: | ---: | --- |
| 1 | Success | 91.2s | 3840x2160 | 14.4 MiB | 1 | `/absolute/path/image.png` |

Progress is written to stderr and final machine-readable JSON is written to stdout. Use `--no-progress` only when a caller explicitly needs silent stderr.

## Image-to-Image Editing

Edit an existing image:

```shell
python <skill-dir>/scripts/generate.py --model gpt-image-2-c --input-image source.png --prompt "Keep the subject and change the background to a snowy mountain" --output outputs/prompt-studio/edited.png
```

OpenAI-compatible models send multipart requests to `/v1/images/edits`. Gemini models send the source image as `inlineData` alongside the prompt. Input images must be PNG, JPEG, or WebP and no larger than 20 MiB.

Use `--count` and `--concurrency` to create multiple variants from the same source image. Each request receives the original source image; outputs are not chained into later requests.

## Batch Generation

Generate ten images with at most two requests in flight:

```shell
python <skill-dir>/scripts/generate.py --model gpt-image-2 --prompt "<prompt>" --output outputs/prompt-studio/image.png --count 10 --concurrency 2
```

For multiple images, the script writes `image-001.png`, `image-002.png`, and so on. `--count` accepts 1-100 and `--concurrency` accepts any positive integer; effective concurrency never exceeds `--count`. Keep concurrency at 1 unless the user requests batching. The script rejects concurrency above 1 for `gpt-image-2-codex` and `gpt-image-2-vip` because the operator marks those routes as unsuitable for high concurrency.

Transient `429`, `5xx`, network, and timeout failures are retried twice by default with exponential backoff. Customize this with `--retries 0-5` and `--retry-delay 0-60`. Warn the user that retrying a timeout can create a duplicate billable generation if the upstream completed the request but its response was lost.

The default timeout is 360 seconds, or 420 seconds when the model ID contains `4k`. Use `--timeout` to override it.

## Models

| Model | Protocol | Notes |
| --- | --- | --- |
| `gpt-image-2` | OpenAI | General generation; operator advertises high concurrency. |
| `gpt-image-2-c` | OpenAI | Operator advertises enterprise routing and native 4K. |
| `gpt-image-2-codex` | OpenAI | Alternate Codex route; operator warns against high concurrency. |
| `gpt-image-2-vip` | OpenAI | Operator advertises native 4K and no high concurrency. |
| `gemini-3-pro-image-preview` | Gemini | Native Gemini protocol only. |
| `gemini-3.1-flash-image-preview` | Gemini | Native Gemini protocol only; default Gemini choice. |

Do not infer undocumented quality, resolution, or concurrency guarantees from model names. Treat the operator's pricing-page descriptions as mutable service claims.

The table is a set of known models, not a permanent allowlist. The service can add or remove models. For a new Gemini model, pass `--protocol gemini`; other unknown model IDs default to the OpenAI-compatible protocol.

## Useful Commands

Check access without generating an image:

```shell
python <skill-dir>/scripts/generate.py --check --model gpt-image-2
```

List every model visible to the current key:

```shell
python <skill-dir>/scripts/generate.py --list-models
```

Set an explicit OpenAI-compatible size:

```shell
python <skill-dir>/scripts/generate.py --model gpt-image-2 --size 1024x1024 --prompt "A red paper lantern on white" --output outputs/prompt-studio/lantern.png
```

For Gemini models, omit `--size`; the script sends the native `generateContent` request and extracts `inlineData`. This applies to both text-to-image and image-to-image requests.

## Failures

- `401` or `403`: verify Prompt Studio's active connection, its Key, and the selected model's access.
- `404`: keep the default base URL. OpenAI uses `/v1/images/generations`; Gemini uses `/v1beta/models/{model}:generateContent`.
- Model missing from `--check`: the key's group or current routing does not expose that model.
- No image in a successful response: preserve the response summary, do not print large Base64 data, and ask the user whether to retry with another model.
- Partial batch failure: successful numbered files remain on disk; report the failure and inspect existing outputs before retrying to avoid duplicate charges.
