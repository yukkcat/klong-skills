# 小恐龙图片模型

信息基准：Infinite Canvas 稳定补丁 v1.2.0 与其使用说明，2026-08-17。价格、成功率、延迟和在线状态会变化；需要实时费用时查询小恐龙定价页：https://api.klong.lat/pricing

| 模型 | 协议 | 路由定位 | 4K / 并发说明 |
| --- | --- | --- | --- |
| gpt-image-2 | OpenAI Images | 普通性价比路由 | 非企业级；服务方称支持高并发，稳定性取决于官网上游 |
| gpt-image-2-c | OpenAI Images | 企业级 -c 路由 | 服务方称更稳定、支持并发和原生 4K |
| gpt-image-2-vip | OpenAI Images，非流式 | VIP 路由 | 原生 4K，质量固定为 medium；强制并发 1 |
| gemini-3-pro-image-preview | 原生 Gemini | Pro 普通路由 | 不把未明确能力推断为保证 |
| gemini-3-pro-image-preview-c | 原生 Gemini | Pro 企业级 -c 路由 | 服务方称更稳定、支持并发和原生 4K |
| gemini-3.1-flash-image-preview | 原生 Gemini | Flash 普通路由 | 默认 Gemini 选择 |
| gemini-3.1-flash-image-preview-c | 原生 Gemini | Flash 企业级 -c 路由 | 服务方称更稳定、支持并发和原生 4K |

## 共同调用规则

- 只允许上表 7 个模型；拒绝 gpt-image-2-codex 和未知模型。
- 三个 GPT 模型调用 POST /v1/images/generations 或 POST /v1/images/edits。
- 四个 Gemini 模型调用 POST /v1beta/models/{model}:generateContent，图片用原生 inlineData 传入和解析。
- 默认请求超时为 600 秒；允许显式覆盖。
- 默认自动重试为 0。连接中断后先检查历史和输出，不自动重提付费请求。
- gpt-image-2-vip 使用普通非流式 OpenAI 请求并强制并发 1。
- -c、4K、并发和稳定性描述属于服务方的动态声明，不作为永久保证。
