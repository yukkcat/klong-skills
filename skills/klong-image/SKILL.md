---
name: klong-image
description: Generate or edit single images and controlled concurrent image batches through the third-party Klong API using its OpenAI-compatible GPT image models or native Gemini image models. Use when the user asks Codex to draw, generate one or more images, perform image-to-image editing, create variants from a source image, create visual assets, compare Klong image models, or save generated raster images locally.
---

# Klong Image

Use `scripts/generate.py` for deterministic API calls. Never put an API key in a prompt, source file, command argument, or committed config; read it from `KLONG_API_KEY`.

## Workflow

1. Confirm `KLONG_API_KEY` exists in the process environment.
2. If current availability matters, run `--list-models`. Choose `gpt-image-2` by default, or `gemini-3.1-flash-image-preview` when native Gemini is requested.
3. Pick an output path inside the current workspace. Create its parent directory when needed.
4. For image-to-image work, identify the source PNG, JPEG, or WebP file and pass it with `--input-image`.
5. Run:

```shell
python <skill-dir>/scripts/generate.py --model <model> --prompt "<prompt>" --output <path>
```

6. While the command runs, relay meaningful progress from stderr when the user is waiting. The script reports request starts, retries, completions, failures, and a heartbeat every 30 seconds. Never invent a percentage because the upstream API does not expose one.
7. Verify that each successful output file exists and is non-empty. When visual inspection tools are available, inspect the images before reporting completion.
8. Parse the final JSON from stdout and report a concise summary plus a Markdown result table. Include model, mode, total duration, requested/succeeded/failed counts, and per-image status, duration, actual dimensions, file size, attempts, and absolute output path or error. Do not report the secret or raw Base64 payload.

Use a result table shaped like this:

| Index | Status | Duration | Dimensions | Size | Attempts | Output / Error |
| --- | --- | ---: | --- | ---: | ---: | --- |
| 1 | Success | 91.2s | 3840x2160 | 14.4 MiB | 1 | `/absolute/path/image.png` |

Progress is written to stderr and final machine-readable JSON is written to stdout. Use `--no-progress` only when a caller explicitly needs silent stderr.

## Image-to-Image Editing

Edit an existing image:

```shell
python <skill-dir>/scripts/generate.py --model gpt-image-2-c --input-image source.png --prompt "Keep the subject and change the background to a snowy mountain" --output edited.png
```

OpenAI-compatible models send multipart requests to `/v1/images/edits`. Gemini models send the source image as `inlineData` alongside the prompt. Input images must be PNG, JPEG, or WebP and no larger than 20 MiB.

Use `--count` and `--concurrency` to create multiple variants from the same source image. Each request receives the original source image; outputs are not chained into later requests.

## Batch Generation

Generate ten images with at most two requests in flight:

```shell
python <skill-dir>/scripts/generate.py --model gpt-image-2 --prompt "<prompt>" --output image.png --count 10 --concurrency 2
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
python <skill-dir>/scripts/generate.py --model gpt-image-2 --size 1024x1024 --prompt "A red paper lantern on white" --output lantern.png
```

For Gemini models, omit `--size`; the script sends the native `generateContent` request and extracts `inlineData`. This applies to both text-to-image and image-to-image requests.

## Failures

- `401` or `403`: check that `KLONG_API_KEY` is present in the current Codex process and still valid.
- `404`: keep the default base URL. OpenAI uses `/v1/images/generations`; Gemini uses `/v1beta/models/{model}:generateContent`.
- Model missing from `--check`: the key's group or current routing does not expose that model.
- No image in a successful response: preserve the response summary, do not print large Base64 data, and ask the user whether to retry with another model.
- Partial batch failure: successful numbered files remain on disk; report the failure and inspect existing outputs before retrying to avoid duplicate charges.
