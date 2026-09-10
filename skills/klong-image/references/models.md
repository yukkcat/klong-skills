# 小恐龙图片模型

信息基准：小恐龙官方文档，2026-09-10。调用细节以 https://docs.klong.lat/docs/image/gpt-image-2 与 https://docs.klong.lat/docs/image/nano-banana 为准；实际可用模型以当前 Key 的 `GET /v1/models` 响应和 API 控制台分配为准。

| 模型 | 协议 | 说明 |
| --- | --- | --- |
| `gpt-image-2` | OpenAI Images | GPT-Image 标准模型；默认选择 |
| `gpt-image-2.5` | OpenAI Images | 与 `gpt-image-2` 使用相同能力 |
| `gpt-image-2.5-flare` | OpenAI Images | GPT-Image 渠道中是同能力别名；官方渠道中速度优先 |
| `gpt-image-2.5-sunburst` | OpenAI Images | GPT-Image 渠道中是同能力别名；官方渠道中精度优先 |
| `gpt-image-2-exact` | OpenAI Images | 精确尺寸；边长 64-4096，总像素不超过 4096×4096 |
| `gpt-image-2.5-exact` | OpenAI Images | 与 `gpt-image-2-exact` 使用相同的精确尺寸能力 |
| `gpt-image-2-high` | OpenAI Images | 原生最高 4K；`quality=medium/high` |
| `gpt-image-2-vip` | OpenAI Images | 原生最高 4K；质量固定为 `medium` |
| `nano-banana2` | OpenAI Images | NewAPI 中转，只走 Images API |
| `nano-banana-pro` | OpenAI Images | NewAPI 中转，只走 Images API |
| `gemini-3.1-flash-image-preview` | Gemini 原生 | `generateContent`；默认 Gemini 选择 |
| `gemini-3-pro-image-preview` | Gemini 原生 | `generateContent` |

`gpt-image-2.5-flare` 和 `gpt-image-2.5-sunburst` 的实际能力取决于控制台分配的渠道。官方渠道允许 `quality=low/medium/high/xhigh/max`；GPT-Image 渠道中两者只是同能力别名。不要仅凭模型名推断渠道、价格或可用性。

## 调用规则

- 连接地址可填服务根地址 `https://api.klong.lat` 或 SDK Base URL `https://api.klong.lat/v1`；Skill 会统一为服务根地址，避免出现重复的 `/v1/v1`。
- GPT Image 与 Nano Banana 调用 `POST /v1/images/generations` 或 `POST /v1/images/edits`，鉴权使用 `Authorization: Bearer`。
- Gemini 图像模型调用 `POST /v1beta/models/{model}:generateContent`，鉴权优先使用 `x-goog-api-key`。
- Gemini 比例与分辨率只放在 `generationConfig.imageConfig`；不要发送 OpenAI 的 `response_format` 或 `responseFormat`。
- Gemini 当前主要在 `file_data.file_uri` / `fileData.fileUri` 返回图片 URL；也要兼容 Markdown 图片 URL 和 `inlineData` / `inline_data` Base64。
- Nano Banana 的 `size` 可使用像素尺寸、宽高比或 `1K` / `2K` / `4K` 档位；最终像素以服务响应为准。
- `-exact` 模型严格校正最终宽高，比例差异较大时可能居中裁切。
- 默认请求超时为 600 秒，默认不自动重试付费请求。

## 模型发现

`--list-models` 请求 `GET /v1/models`，保留当前 Key 可见的图片模型，并按模型族选择协议：

- `gpt-image-*`（排除 `-codex`）与 `nano-banana*` 使用 OpenAI Images。
- 名称中含 `image` 的 `gemini-*` 使用 Gemini 原生协议。

这种识别方式不会把对话模型混入工作台，也不会因新增同族图片模型而依赖固定白名单。未识别的模型不会自动调用；先核实其图片接口和协议，再更新路由规则。
