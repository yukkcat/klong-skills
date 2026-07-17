# Klong Skills

小恐龙 API 的 Codex Skills 集合。安装后，可直接让 Codex 调用小恐龙 API 完成图片生成和批量出图，无需在对话中传递 API Key。

当前提供 `klong-image`：兼容 OpenAI Images 协议与 Gemini 原生图片协议，支持文生图、图生图、模型发现、并发生成、失败重试和本地文件输出。

## 功能

- 在 Codex 对话中直接生成图片
- 使用已有图片生成修改版本或多个变体
- 自动选择 OpenAI 或 Gemini 请求协议
- 通过 `/v1/models` 查询当前 Key 可用的模型
- 单次生成 1-100 张图片
- 并发数由用户按任务和账户能力设置
- 对 `429`、`5xx`、网络错误和超时进行指数退避重试
- 实时显示请求、等待、重试、成功和失败状态
- 输出包含耗时、实际分辨率和文件大小的 JSON 汇总
- 校验响应大小和图片格式，拒绝异常内容
- API Key 仅从本地环境变量读取

## 快速开始

> 不熟悉 Codex 配置？查看图文教程：[使用 CC Switch 配置 Codex 接入小恐龙 API](docs/codex-cc-switch.md)。

### 1. 安装 Skill

把下面这句话发送给 Codex：

```text
请从 https://github.com/yukkcat/klong-skills/tree/main/skills/klong-image 安装这个 Skill。
```

也可以直接运行 Codex 自带的 Skill 安装器。

Windows PowerShell：

```powershell
python "$env:USERPROFILE\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py" `
  --repo yukkcat/klong-skills `
  --path skills/klong-image
```

macOS、Linux 或 WSL：

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo yukkcat/klong-skills \
  --path skills/klong-image
```

安装完成后，重新打开 Codex 或开始一个新任务。

### 2. 配置 API Key

在小恐龙 API 控制台创建 Key，并将它保存到 `KLONG_API_KEY` 环境变量。

Windows PowerShell：

```powershell
[Environment]::SetEnvironmentVariable(
  "KLONG_API_KEY",
  "sk-替换成你的密钥",
  "User"
)
```

macOS、Linux 或 WSL：

```bash
export KLONG_API_KEY="sk-替换成你的密钥"
```

设置用户环境变量后，需要完全退出并重新打开 Codex。不要把真实 Key 写入聊天、代码、`.env`、Issue 或截图。

### 3. 开始使用

生成单张图片：

```text
使用 $klong-image，通过 gpt-image-2 生成一张白底产品图，保存到 outputs/product.png。
```

批量生成：

```text
使用 $klong-image，通过 gpt-image-2 生成 10 张产品图，并发数为 2，保存到 outputs/product.png。
```

批量文件会自动编号为 `product-001.png`、`product-002.png`，依次类推。

图生图：

```text
使用 $klong-image，以 assets/source.png 为输入，保持主体不变，把背景改成雪山，保存到 outputs/edited.png。
```

图生图支持 PNG、JPEG 和 WebP，输入文件最大 20 MiB。OpenAI 兼容模型使用 `/v1/images/edits`，Gemini 模型会把图片作为 `inlineData` 与提示词一起发送。

## 模型与协议

| 模型 | 协议 | 并发建议 |
| --- | --- | --- |
| `gpt-image-2` | OpenAI Images | 通用默认，可按需并发 |
| `gpt-image-2-c` | OpenAI Images | 可按需并发 |
| `gpt-image-2-codex` | OpenAI Images | 固定并发 1 |
| `gpt-image-2-vip` | OpenAI Images | 固定并发 1 |
| `gemini-3-pro-image-preview` | Gemini `generateContent` | 从低并发开始 |
| `gemini-3.1-flash-image-preview` | Gemini `generateContent` | Gemini 默认选择 |

模型表是已知模型说明，不是固定白名单。服务新增模型后可直接通过 `--model` 使用。以 `gemini-` 开头的新模型会自动采用 Gemini 协议，也可以通过 `--protocol` 显式指定。

查看当前 Key 可用的全部模型：

```powershell
python .\skills\klong-image\scripts\generate.py --list-models
```

## 命令行使用

仓库要求 Python 3.10 或更高版本。安装 Skill 后，也可以直接运行其中的脚本：

```powershell
python .\skills\klong-image\scripts\generate.py `
  --model gpt-image-2 `
  --prompt "白底产品图，无文字" `
  --size 1024x1024 `
  --output outputs\product.png `
  --count 10 `
  --concurrency 2
```

直接执行图生图：

```powershell
python .\skills\klong-image\scripts\generate.py `
  --model gpt-image-2-c `
  --input-image assets\source.png `
  --prompt "保持主体不变，把背景改成雪山" `
  --output outputs\edited.png
```

常用参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--model` | `gpt-image-2` | 模型 ID |
| `--protocol` | `auto` | `auto`、`openai` 或 `gemini` |
| `--input-image` | 无 | 图生图源文件，支持 PNG、JPEG、WebP，最大 20 MiB |
| `--count` | `1` | 生成数量，范围 1-100 |
| `--concurrency` | `1` | 并发请求数，至少为 1，实际不会超过生成数量 |
| `--timeout` | 自动 | 普通模型默认 360 秒，名称含 `4k` 的模型默认 420 秒 |
| `--retries` | `2` | 临时错误重试次数，范围 0-5 |
| `--retry-delay` | `3` | 首次重试等待秒数，范围 0-60 |
| `--check` | 关闭 | 只检查指定模型是否可用 |
| `--list-models` | 关闭 | 列出当前 Key 可见的模型 |
| `--no-progress` | 关闭 | 关闭 stderr 中的人类可读实时状态 |

## 运行状态与结果

生成过程中，脚本会在 stderr 实时输出请求开始、重试、完成和失败状态。超过 30 秒的任务会定期显示真实等待时间：

```text
[start] model=gpt-image-2-vip protocol=openai mode=text-to-image requests=4 concurrency=2 timeout=360s
[request 1/4] started
[waiting] elapsed=30.0s active=2 completed=0/4 succeeded=0 failed=0
[request 1/4] completed duration=91.2s size=14.40MiB dimensions=3840x2160
[complete] duration=143.8s requested=4 succeeded=3 failed=1
```

API 不提供真实生成百分比，因此脚本只报告可验证的状态和已用时间，不显示虚假进度条。

脚本完成后在 stdout 输出 JSON 汇总，包括模式、总耗时、请求数、成功数、失败数，以及每张图片的耗时、尝试次数、实际分辨率、文件大小、路径或错误。只要有一张失败，进程退出码即为 `1`，已经成功的文件会保留。

Codex 会把结果整理成便于阅读的表格：

| 序号 | 状态 | 耗时 | 实际分辨率 | 大小 | 尝试次数 | 文件或错误 |
| --- | --- | ---: | --- | ---: | ---: | --- |
| 1 | 成功 | 91.2s | 3840x2160 | 14.4 MiB | 1 | `outputs/image-001.png` |

## 并发与重试

脚本不设置固定并发上限，实际并发不会超过 `--count`；单批生成数量最多为 100。服务端仍可能根据账户、模型和线路状态执行限流，建议从 2-4 开始，再逐步调整。

本项目对 `gpt-image-2` 进行过 10 并发验证：10 个请求成功 8 个，2 个因上游 `image_poll_timeout` 失败。该结果仅表示测试时的线路状态，不构成可用性保证。

超时重试可能产生重复计费：上游可能已经完成生成，但客户端没有收到响应。对成本敏感的任务可使用 `--retries 0`，并在重试批次前检查已经生成的编号文件。

## 常见问题

| 错误 | 处理方式 |
| --- | --- |
| `KLONG_API_KEY is not set` | 重新设置环境变量并完全重启 Codex |
| `401` / `403` | 检查 Key、余额和模型分组权限 |
| `429` | 降低并发，等待自动重试或稍后再试 |
| `image_poll_timeout` | 上游生成超时；保留已成功文件，仅补跑失败任务 |
| 模型检查失败 | 使用 `--list-models` 查看当前 Key 实际可用模型 |
| Gemini 请求失败 | 确认使用 Gemini 协议，且没有传入 `--size` |

## 项目结构

```text
skills/
└── klong-image/
    ├── SKILL.md
    ├── agents/openai.yaml
    └── scripts/generate.py
```

## 文档与支持

- [Codex + CC Switch 图文配置教程](docs/codex-cc-switch.md)
- [模型与价格](https://api.klong.lat/pricing)
- [图片接口文档](https://docs.klong.lat/zh/docs/api/ai-model/images/openai/post-v1-images-generations)
- [Codex CLI 教程](https://docs.klong.lat/zh/docs/apps/codex-cli)
- [安全问题报告](SECURITY.md)

## 贡献者

- [yukkcat](https://github.com/yukkcat)
- [BaikalLamb](https://github.com/BaikalLamb)

模型、价格、限额和可用性以小恐龙 API 控制台及官方文档为准。Klong Skills 使用 [MIT License](LICENSE) 发布。
