---
name: klong-image
description: Generate single images or controlled concurrent image batches through the third-party Klong API using its OpenAI-compatible GPT image models or native Gemini image models. Use when the user asks Codex to draw, generate one or more images, create visual assets, compare Klong image models, or save generated raster images locally.
---

# Klong Image

Use `scripts/generate.py` for deterministic API calls. Never put an API key in a prompt, source file, command argument, or committed config; read it from `KLONG_API_KEY`.

## Workflow

1. Confirm `KLONG_API_KEY` exists in the process environment.
2. If current availability matters, run `--list-models`. Choose `gpt-image-2` by default, or `gemini-3.1-flash-image-preview` when native Gemini is requested.
3. Pick an output path inside the current workspace. Create its parent directory when needed.
4. Run:

```shell
python <skill-dir>/scripts/generate.py --model <model> --prompt "<prompt>" --output <path>
```

5. Verify that the output file exists and is non-empty. When visual inspection tools are available, inspect the image before reporting completion.
6. Report the selected model, protocol, and absolute output path. Do not report the secret or raw Base64 payload.

## Batch Generation

Generate ten images with at most two requests in flight:

```shell
python <skill-dir>/scripts/generate.py --model gpt-image-2 --prompt "<prompt>" --output image.png --count 10 --concurrency 2
```

For multiple images, the script writes `image-001.png`, `image-002.png`, and so on. `--count` accepts 1-100 and `--concurrency` accepts any positive integer; effective concurrency never exceeds `--count`. Keep concurrency at 1 unless the user requests batching. The script rejects concurrency above 1 for `gpt-image-2-codex` and `gpt-image-2-vip` because the operator marks those routes as unsuitable for high concurrency.

Transient `429`, `5xx`, network, and timeout failures are retried twice by default with exponential backoff. Customize this with `--retries 0-5` and `--retry-delay 0-60`. Warn the user that retrying a timeout can create a duplicate billable generation if the upstream completed the request but its response was lost.

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

For Gemini models, omit `--size`; the script sends the native `generateContent` request and extracts `inlineData`.

## Failures

- `401` or `403`: check that `KLONG_API_KEY` is present in the current Codex process and still valid.
- `404`: keep the default base URL. OpenAI uses `/v1/images/generations`; Gemini uses `/v1beta/models/{model}:generateContent`.
- Model missing from `--check`: the key's group or current routing does not expose that model.
- No image in a successful response: preserve the response summary, do not print large Base64 data, and ask the user whether to retry with another model.
- Partial batch failure: successful numbered files remain on disk; report the failure and inspect existing outputs before retrying to avoid duplicate charges.
