# 小恐龙 Skills

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
- API Key 仅保留在本机或当前浏览器；网页可直接配置，命令行可使用环境变量
- 本地图库集中查看生成结果，并按需加载缩略图
- Codex 与网页共用任务历史、提示词和逐图生成数据
- 同一套网页可直接部署到 Vercel，云端用户的数据互相隔离

## 快速开始

### 本地提示词工作台

安装 Skill 后，把下面这句话发送给 Codex；Codex 会自动启动本地服务并打开网页，不需要手动运行 Python 或 `npm run dev`：

```text
使用 $klong-image 打开本地提示词工作台。
```

程序会打开 `http://127.0.0.1:8765`。首次启动会在后台自动下载全部内置提示词源；后续启动直接读取本地缓存。网页支持搜索、来源与分类筛选、文生图/图生图、批量数量、并发设置和实时生成状态。

点击右上角的设置按钮可以切换“连接”和“存储”。连接设置支持填写 API 地址和 API Key，并通过 `/v1/models` 测试连接、更新模型列表。Windows 会使用当前用户的 DPAPI 加密保存 Key，网页与 Codex 直调会读取同一份 `~/.klong-image/settings.json`，并严格使用其中当前选中的连接；其他系统只在本次工作台进程中保留网页填写的 Key，跨进程调用仍建议使用环境变量。已保存的 Key 不会返回给网页。

网页和 Codex 直接生成的图片及任务记录共用同一个目录：默认保存在启动目录的 `outputs/prompt-studio` 下，任务元数据写入其中的 `.klong/jobs`。可在“设置 → 存储”中选择、打开或恢复图库位置；新位置会持久化到当前用户的 `~/.klong-image/settings.json`，Codex 后续直接生成也会自动使用。切换位置不会移动旧作品。`KLONG_OUTPUT_DIR` 可以锁定目录，网页单次启动也可用 `--output-dir` 覆盖；优先级为 `--output-dir`、`KLONG_OUTPUT_DIR`、网页保存位置、默认位置。

图库支持按文件名、模型或提示词搜索，可切换排序方式和每页数量。可以选择本页，也可以一次选择当前搜索结果中的全部作品，然后批量打包为 ZIP 下载或删除。删除前会要求确认，确认后文件会直接从本地磁盘移除且无法恢复。点击图片仍可查看生成信息、复制提示词、下载原图或单独删除该作品。

> 不熟悉 Codex 配置？查看图文教程：[使用 CC Switch 配置 Codex 接入小恐龙 API](docs/codex-cc-switch.md)。

### 部署到 Vercel

把本仓库导入 Vercel 后直接部署即可，根目录的 `vercel.json` 已包含构建命令和输出目录，不需要在 Vercel 配置 API Key 或其他环境变量。同一套 Vue 页面会自动判断运行环境：由 Python 启动时使用本地 API、共享图库和 Codex 历史；静态部署时使用浏览器运行时并直接请求小恐龙 API。

云端页面中的连接、加密 Key、图片和任务历史都保存在当前浏览器的 IndexedDB 中，不会上传到 Vercel，也不会与其他访问者共享。浏览器 Key 使用不可导出的 Web Crypto 密钥加密保存。清除站点数据会同时清除这些本地数据；不同浏览器或设备之间不会自动同步，关闭页面也会中断尚未完成的生成任务。

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

先在[小恐龙 API 控制台](https://api.klong.lat/keys)创建 Key。在 Windows 上，只需在网页右上角打开“连接设置”，填写 Key 后点击“测试连接”和“保存设置”。网页生成和 Codex 直接调用 `generate.py` 都会使用当前选中的连接，无需再配置 `.env` 或系统环境变量。

自动化部署，或者在 macOS、Linux、WSL 中跨进程调用时，可以复制根目录的 `.env.example`：

```powershell
Copy-Item .env.example .env
```

如果只安装了 `klong-image` Skill，也可以把 Skill 目录内的示例复制为同目录的 `.env`：

```powershell
Copy-Item <skill-dir>\.env.example <skill-dir>\.env
```

程序会从当前目录、Skill 目录或仓库根目录读取 `.env`，但不会覆盖已经存在的系统环境变量。`.env` 已被 Git 忽略，里面的 Key 仍是明文，只适合当前用户可控的电脑。也可以直接设置系统环境变量：

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

设置用户环境变量后，需要完全退出并重新打开 Codex。不要把真实 Key 写入聊天、提交、Issue 或截图，也不要提交本地 `.env`。环境变量会在网页中显示为一个只读的“环境变量”连接，但不会覆盖已选中的其他连接；只有网页当前选中“环境变量”时，网页与 Codex 才会使用其中的 Key。若共享设置里记录的连接 ID 已不存在，系统按“可用的环境变量连接、首个已保存连接”的顺序回退。

### 3. 开始使用

生成单张图片：

```text
使用 $klong-image，通过 gpt-image-2 生成一张白底产品图，保存到 outputs/prompt-studio/product.png。
```

批量生成：

```text
使用 $klong-image，通过 gpt-image-2 生成 10 张产品图，并发数为 2，保存到 outputs/prompt-studio/product.png。
```

批量文件会自动编号为 `product-001.png`、`product-002.png`，依次类推。

图生图：

```text
使用 $klong-image，以 assets/source.png 为输入，保持主体不变，把背景改成雪山，保存到 outputs/prompt-studio/edited.png。
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
  --output outputs\prompt-studio\product.png `
  --count 10 `
  --concurrency 2
```

直接执行图生图：

```powershell
python .\skills\klong-image\scripts\generate.py `
  --model gpt-image-2-c `
  --input-image assets\source.png `
  --prompt "保持主体不变，把背景改成雪山" `
  --output outputs\prompt-studio\edited.png
```

常用参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--model` | `gpt-image-2` | 模型 ID |
| `--protocol` | `auto` | `auto`、`openai` 或 `gemini` |
| `--output` | 自动命名 | 默认写入 `KLONG_OUTPUT_DIR` 或 `outputs/prompt-studio`；显式路径优先 |
| `--name` | 输出文件名 | 网页历史中显示的任务名称 |
| `--gallery-dir` | 共享输出目录 | 写入网页任务历史和图库元数据的根目录 |
| `--input-image` | 无 | 图生图源文件，支持 PNG、JPEG、WebP，最大 20 MiB |
| `--count` | `1` | 生成数量，范围 1-100 |
| `--concurrency` | `1` | 并发请求数，至少为 1，实际不会超过生成数量 |
| `--timeout` | 自动 | 普通模型默认 360 秒，名称含 `4k` 的模型默认 420 秒 |
| `--retries` | `2` | 临时错误重试次数，范围 0-5 |
| `--retry-delay` | `3` | 首次重试等待秒数，范围 0-60 |
| `--check` | 关闭 | 只检查指定模型是否可用 |
| `--list-models` | 关闭 | 列出当前 Key 可见的模型 |
| `--no-progress` | 关闭 | 关闭 stderr 中的人类可读实时状态 |
| `--no-history` | 关闭 | 不写入网页任务历史元数据 |

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

脚本完成后在 stdout 输出与网页 `/api/jobs/<id>` 一致的任务 JSON：顶层包括任务 ID、名称、状态、提示词、模型、协议、尺寸、数量、并发和时间，`result` 中包括模式、总耗时、成功数、失败数，以及每张图片的耗时、尝试次数、实际分辨率、文件大小、路径或错误。Python 直调和网页创作历史读取同一份数据；任务异常时也会记录为失败，不会一直停留在“生成中”。只要有一张失败，进程退出码即为 `1`，已经成功的文件会保留。

Codex 会把结果整理成便于阅读的表格：

| 序号 | 状态 | 耗时 | 实际分辨率 | 大小 | 尝试次数 | 文件或错误 |
| --- | --- | ---: | --- | ---: | ---: | --- |
| 1 | 成功 | 91.2s | 3840x2160 | 14.4 MiB | 1 | `outputs/prompt-studio/image-001.png` |

## 并发与重试

脚本不设置固定并发上限，实际并发不会超过 `--count`；单批生成数量最多为 100。服务端仍可能根据账户、模型和线路状态执行限流，建议从 2-4 开始，再逐步调整。

本项目对 `gpt-image-2` 进行过 10 并发验证：10 个请求成功 8 个，2 个因上游 `image_poll_timeout` 失败。该结果仅表示测试时的线路状态，不构成可用性保证。

超时重试可能产生重复计费：上游可能已经完成生成，但客户端没有收到响应。对成本敏感的任务可使用 `--retries 0`，并在重试批次前检查已经生成的编号文件。

## 常见问题

| 错误 | 处理方式 |
| --- | --- |
| `未找到 API Key` | Windows 可在网页“连接设置”中保存；其他系统的跨进程调用请设置 `KLONG_API_KEY` |
| 图库没有显示新图片 | 点击图库中的刷新按钮，并确认图片位于当前工作台的输出目录 |
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
    ├── assets/prompt-studio/index.html
    └── scripts/
        ├── generate.py
        └── prompt_studio.py
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

模型、价格、限额和可用性以小恐龙 API 控制台及官方文档为准。小恐龙 Skills 使用 [MIT License](LICENSE) 发布。
