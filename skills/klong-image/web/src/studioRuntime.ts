import { openDB, type IDBPDatabase } from 'idb'
import JSZip from 'jszip'
import {
  imageModelIds,
  imageModelProtocol,
  isExactImageModel,
  isNanoBananaModel,
  modelQualityOptions,
  normalizeNanoBananaSize,
} from './imageModels'
import {
  IMAGE_RATIOS,
  IMAGE_RESOLUTIONS,
  constrainImageSizeValue,
  imageSizePreset,
  type ImageRatio,
  type ImageResolution,
} from './imageSizes'

export type RuntimeMode = 'local' | 'browser'

export interface StudioRuntime {
  readonly mode: RuntimeMode
  request(path: string, options?: RequestInit): Promise<any>
  archive(payload: Record<string, unknown>): Promise<Blob>
  promptPreview(item: { id: string; preview?: string }): string
  downloadUrl(url: string): string
  dispose(): void
}

type StoredConnection = {
  id: string
  name: string
  base_url: string
  default_model: string
  models: string[]
  models_synced_at: string
  key_hint: string
  key_cipher?: ArrayBuffer
  key_iv?: Uint8Array
  created_at: string
}

type StoredImage = {
  id: string
  name: string
  bytes: number
  created_at: string
  blob: Blob
  prompt: string
  model: string
  protocol: string
  mode: string
  connection_id?: string
  connection_name?: string
  width?: number
  height?: number
  duration_seconds?: number
  job_id: string
  batch_id?: string
  size?: string
}

type PromptLibrary = {
  items: Array<Record<string, any>>
  sources: Array<Record<string, any>>
  synced_at: string
  registry?: {
    url?: string
    revision?: string
    generated_at?: string
  }
}

type PromptRegistrySource = {
  id: string
  name: string
  homepage: string
  upstreamUrl: string
  count: number
  path: string
  sha256: string
}

const DB_NAME = 'klong-prompt-studio'
const DB_VERSION = 1
const DEFAULT_BASE_URL = 'https://api.klong.lat'
const DEFAULT_MODEL = 'gpt-image-2'
const MAX_INPUT_IMAGES = 5
const MAX_INPUT_BYTES = 20 * 1024 * 1024
const INPUT_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp'])
const PROMPT_REGISTRY_BASE = 'https://raw.githubusercontent.com/yukkcat/image-prompts/main/dist'
const PROMPT_REGISTRY_MANIFEST_MAX_BYTES = 128 * 1024
const PROMPT_REGISTRY_PAYLOAD_MAX_BYTES = 8 * 1024 * 1024
const PROMPT_REGISTRY_TIMEOUT_MS = 30_000
const PROMPT_REGISTRY_MANIFEST_FIELDS = new Set([
  'schemaVersion', 'generatedAt', 'registryHash', 'total', 'promptsPath', 'sources',
])
const PROMPT_REGISTRY_SOURCE_FIELDS = new Set([
  'id', 'name', 'homepage', 'upstreamUrl', 'count', 'path', 'sha256',
])
const PROMPT_REGISTRY_ITEM_FIELDS = new Set([
  'id', 'sourceId', 'title', 'prompt', 'description', 'coverUrl', 'referenceImageUrls',
  'tags', 'author', 'sourceUrl', 'createdAt', 'imageMode', 'imageModel', 'imageSize', 'imageCount',
])
const PROMPT_REGISTRY_REQUIRED_ITEM_FIELDS = new Set(
  [...PROMPT_REGISTRY_ITEM_FIELDS].filter((field) => !['imageSize', 'imageCount'].includes(field)),
)
const encoder = new TextEncoder()
const decoder = new TextDecoder()

function nowIso() {
  return new Date().toISOString()
}

function randomId() {
  return crypto.randomUUID().replaceAll('-', '')
}

function plainRecord(value: unknown): value is Record<string, any> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeInputImages(payload: Record<string, any>) {
  let values: unknown[]
  if ('input_images' in payload) {
    if (!Array.isArray(payload.input_images)) throw new Error('input_images 必须是数组')
    values = payload.input_images
  } else {
    values = payload.input_image ? [payload.input_image] : []
  }
  if (values.length > MAX_INPUT_IMAGES) throw new Error(`参考图片最多 ${MAX_INPUT_IMAGES} 张`)

  return values.map((value, index) => {
    if (typeof value !== 'string') throw new Error(`第 ${index + 1} 张参考图片不是有效的数据 URL`)
    const comma = value.indexOf(',')
    const header = comma >= 0 ? value.slice(0, comma).toLowerCase() : ''
    const data = comma >= 0 ? value.slice(comma + 1) : ''
    const mimeType = header.match(/^data:(image\/(?:png|jpeg|webp));base64$/)?.[1] || ''
    if (!INPUT_IMAGE_TYPES.has(mimeType) || !data || data.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(data)) {
      throw new Error(`第 ${index + 1} 张参考图片必须是 PNG、JPEG 或 WebP`)
    }
    const padding = data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0
    const byteLength = Math.floor(data.length * 3 / 4) - padding
    if (byteLength <= 0 || byteLength > MAX_INPUT_BYTES) {
      throw new Error(`第 ${index + 1} 张参考图片不能超过 ${MAX_INPUT_BYTES / 1024 / 1024} MiB`)
    }
    return value
  })
}

function normalizeGeminiImageConfig(payload: Record<string, any>, size: string) {
  let aspectRatio = String(payload.aspect_ratio || '').trim()
  let imageSize = String(payload.image_size || '').trim().toUpperCase()
  if (aspectRatio.toLowerCase() === 'auto') aspectRatio = ''
  if (imageSize.toLowerCase() === 'auto') imageSize = ''
  if (aspectRatio && !IMAGE_RATIOS.slice(1).includes(aspectRatio as ImageRatio)) {
    throw new Error(`Gemini 不支持 aspect_ratio=${aspectRatio}`)
  }
  if (imageSize && !IMAGE_RESOLUTIONS.slice(1).includes(imageSize as ImageResolution)) {
    throw new Error(`Gemini 不支持 image_size=${imageSize}`)
  }
  const fallback = imageSizePreset(size)
  if (!aspectRatio && fallback.ratio !== 'auto') aspectRatio = fallback.ratio
  if (!imageSize && fallback.resolution !== 'auto') imageSize = fallback.resolution
  return { aspect_ratio: aspectRatio, image_size: imageSize }
}

function exactFields(value: Record<string, any>, expected: Set<string>, label: string) {
  const fields = Object.keys(value)
  if (fields.length !== expected.size || fields.some((field) => !expected.has(field))) {
    throw new Error(`${label}字段与 schema v1 不匹配`)
  }
}

function registryInline(value: unknown, field: string) {
  if (typeof value !== 'string') throw new Error(`提示词 registry 的 ${field} 必须是字符串`)
  return value.trim().replace(/\s+/gu, ' ')
}

function registryMultiline(value: unknown, field: string) {
  if (typeof value !== 'string') throw new Error(`提示词 registry 的 ${field} 必须是字符串`)
  return value
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(/\s+$/u, ''))
    .join('\n')
    .trim()
}

function registryUrl(value: unknown, field: string, allowEmpty = false) {
  if (typeof value !== 'string') throw new Error(`提示词 registry 的 ${field} 必须是字符串`)
  const raw = value.trim()
  if (!raw && allowEmpty) return ''
  let parsed: URL
  try {
    parsed = new URL(raw)
  } catch {
    throw new Error(`提示词 registry 的 ${field} 不是有效 URL`)
  }
  if (/\s/u.test(raw) || !['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) {
    throw new Error(`提示词 registry 的 ${field} 必须是绝对 HTTP(S) URL`)
  }
  return raw
}

function registryPath(value: unknown) {
  if (typeof value !== 'string') throw new Error('提示词 registry 路径必须是字符串')
  const path = value.replaceAll('\\', '/').replace(/^\/+/, '')
  if (!path || path.split('/').includes('..') || !/^[A-Za-z0-9._/-]+$/.test(path)) {
    throw new Error('提示词 registry 路径无效')
  }
  return path
}

function registryCreatedAt(value: unknown) {
  const createdAt = registryInline(value, 'createdAt')
  if (!createdAt) return ''
  const date = createdAt.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (date) {
    const year = Number(date[1])
    const month = Number(date[2])
    const day = Number(date[3])
    const parsed = new Date(Date.UTC(year, month - 1, day))
    if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) {
      throw new Error('提示词 registry 的 createdAt 日期无效')
    }
    return createdAt
  }
  const dateTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
  if (!dateTime.test(createdAt) || Number.isNaN(Date.parse(createdAt))) {
    throw new Error('提示词 registry 的 createdAt 必须是日期或 RFC 3339 时间')
  }
  return createdAt
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (plainRecord(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`
  }
  const serialized = JSON.stringify(value)
  if (serialized === undefined) throw new Error('提示词 registry 包含无法序列化的数据')
  return serialized
}

async function sha256Hex(bytes: Uint8Array) {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', Uint8Array.from(bytes).buffer))
  return [...digest].map((value) => value.toString(16).padStart(2, '0')).join('')
}

async function fetchRegistryBytes(path: string, maxBytes: number, force: boolean) {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), PROMPT_REGISTRY_TIMEOUT_MS)
  try {
    const response = await fetch(`${PROMPT_REGISTRY_BASE}/${registryPath(path)}`, {
      cache: force ? 'reload' : 'default',
      credentials: 'omit',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`提示词 registry 下载失败：HTTP ${response.status}`)
    const declaredLength = response.headers.get('Content-Length')
    if (declaredLength) {
      const length = Number(declaredLength)
      if (!Number.isSafeInteger(length) || length < 0) throw new Error('提示词 registry 返回了无效长度')
      if (length > maxBytes) throw new Error(`提示词 registry 响应超过 ${maxBytes} 字节`)
    }
    const bytes = new Uint8Array(await response.arrayBuffer())
    if (bytes.byteLength > maxBytes) throw new Error(`提示词 registry 响应超过 ${maxBytes} 字节`)
    return bytes
  } catch (error: any) {
    if (error?.name === 'AbortError') throw new Error('提示词 registry 下载超时')
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}

function parseRegistryJson(bytes: Uint8Array, label: string) {
  try {
    return JSON.parse(decoder.decode(bytes).replace(/^\uFEFF/u, ''))
  } catch {
    throw new Error(`提示词 registry 的 ${label} 不是有效 JSON`)
  }
}

function normalizeRegistrySource(value: unknown): PromptRegistrySource {
  if (!plainRecord(value)) throw new Error('提示词 registry 的来源必须是对象')
  exactFields(value, PROMPT_REGISTRY_SOURCE_FIELDS, '提示词 registry 来源')
  const id = registryInline(value.id, 'source.id')
  const name = registryInline(value.name, 'source.name')
  const homepage = registryUrl(value.homepage, 'source.homepage')
  const upstreamUrl = registryUrl(value.upstreamUrl, 'source.upstreamUrl')
  const path = registryPath(value.path)
  const sha256 = registryInline(value.sha256, 'source.sha256').toLowerCase()
  if (!/^[a-z0-9-]+$/.test(id) || !name) throw new Error(`提示词 registry 来源无效：${id.slice(0, 80)}`)
  if (!Number.isInteger(value.count) || value.count < 0 || !/^[a-f0-9]{64}$/.test(sha256)) {
    throw new Error(`提示词 registry 来源元数据无效：${id}`)
  }
  return { id, name: name.slice(0, 160), homepage, upstreamUrl, count: value.count, path, sha256 }
}

function normalizeRegistryItem(
  value: unknown,
  source: PromptRegistrySource,
  sortOrder: number,
) {
  if (!plainRecord(value)) throw new Error('提示词 registry 的记录必须是对象')
  const fields = Object.keys(value)
  const unknown = fields.filter((field) => !PROMPT_REGISTRY_ITEM_FIELDS.has(field))
  const missing = [...PROMPT_REGISTRY_REQUIRED_ITEM_FIELDS].filter((field) => !(field in value))
  if (unknown.length) throw new Error(`提示词 registry 记录包含未知字段：${unknown.sort().join(', ')}`)
  if (missing.length) throw new Error(`提示词 registry 记录缺少字段：${missing.sort().join(', ')}`)

  const sourceId = registryInline(value.sourceId, 'sourceId')
  const id = registryInline(value.id, 'id')
  if (sourceId !== source.id || !new RegExp(`^${source.id}:[a-f0-9]{16}$`).test(id)) {
    throw new Error(`提示词 registry 记录的 id/sourceId 无效：${id.slice(0, 120)}`)
  }
  const title = registryInline(value.title, 'title')
  const prompt = registryMultiline(value.prompt, 'prompt')
  const description = registryMultiline(value.description, 'description')
  if (!title || !prompt) throw new Error(`提示词 registry 记录缺少标题或提示词：${id}`)
  const coverUrl = registryUrl(value.coverUrl, 'coverUrl', true)
  const sourceUrl = registryUrl(value.sourceUrl, 'sourceUrl')

  if (!Array.isArray(value.referenceImageUrls)) throw new Error('提示词 registry 的 referenceImageUrls 必须是数组')
  const references = value.referenceImageUrls.map((url: unknown) => registryUrl(url, 'referenceImageUrls[]'))
  if (new Set(references).size !== references.length) throw new Error('提示词 registry 的参考图 URL 必须唯一')

  if (!Array.isArray(value.tags)) throw new Error('提示词 registry 的 tags 必须是数组')
  const tags = value.tags.map((tag: unknown) => registryInline(tag, 'tags[]'))
  if (tags.some((tag: string) => !tag) || new Set(tags).size !== tags.length) {
    throw new Error('提示词 registry 的 tags 必须是非空且唯一的字符串')
  }

  const imageMode = registryInline(value.imageMode, 'imageMode')
  if (!['', 'generate', 'edit'].includes(imageMode)) throw new Error(`提示词 registry 的 imageMode 无效：${id}`)
  const imageModel = registryInline(value.imageModel, 'imageModel')
  const result: Record<string, any> = {
    id,
    title: title.slice(0, 160),
    description: description.slice(0, 500),
    prompt,
    category: (tags[0] || '').slice(0, 80),
    sub_category: (tags[1] || '').slice(0, 80),
    preview: coverUrl.slice(0, 1200),
    author: registryInline(value.author, 'author').slice(0, 120),
    source_id: source.id,
    source_name: source.name,
    source_homepage: source.homepage,
    source_url: sourceUrl.slice(0, 1200),
    reference_image_urls: references.slice(0, 12),
    tags: tags.slice(0, 24),
    image_mode: imageMode,
    image_model: imageModel.slice(0, 80),
    created_at: registryCreatedAt(value.createdAt),
    sort_order: sortOrder,
  }
  if ('imageSize' in value) {
    const imageSize = registryInline(value.imageSize, 'imageSize')
    if (!imageSize) throw new Error(`提示词 registry 的 imageSize 不能为空：${id}`)
    result.image_size = imageSize.slice(0, 40)
  }
  if ('imageCount' in value) {
    if (!Number.isInteger(value.imageCount) || value.imageCount < 1) {
      throw new Error(`提示词 registry 的 imageCount 无效：${id}`)
    }
    result.image_count = value.imageCount
  }
  return result
}

async function fetchPromptRegistry(force = false): Promise<PromptLibrary> {
  const started = performance.now()
  const manifestBytes = await fetchRegistryBytes('manifest.json', PROMPT_REGISTRY_MANIFEST_MAX_BYTES, force)
  const manifest = parseRegistryJson(manifestBytes, 'manifest')
  if (!plainRecord(manifest)) throw new Error('提示词 registry manifest 必须是对象')
  exactFields(manifest, PROMPT_REGISTRY_MANIFEST_FIELDS, '提示词 registry manifest')
  if (manifest.schemaVersion !== 1) throw new Error(`不支持的提示词 registry schema：${String(manifest.schemaVersion)}`)
  const revision = registryInline(manifest.registryHash, 'registryHash').toLowerCase()
  const generatedAt = registryInline(manifest.generatedAt, 'generatedAt')
  const promptsPath = registryPath(manifest.promptsPath)
  if (!/^[a-f0-9]{64}$/.test(revision) || !generatedAt) throw new Error('提示词 registry manifest 缺少有效版本')
  if (!Array.isArray(manifest.sources) || !manifest.sources.length) throw new Error('提示词 registry manifest 没有来源')
  const sources = manifest.sources.map(normalizeRegistrySource)
  if (new Set(sources.map((source) => source.id)).size !== sources.length) {
    throw new Error('提示词 registry manifest 包含重复来源')
  }
  if (!Number.isInteger(manifest.total) || manifest.total < 0
      || manifest.total !== sources.reduce((total, source) => total + source.count, 0)) {
    throw new Error('提示词 registry manifest 总数与来源计数不一致')
  }

  const promptBytes = await fetchRegistryBytes(promptsPath, PROMPT_REGISTRY_PAYLOAD_MAX_BYTES, force)
  const canonicalSources = encoder.encode(canonicalJson(manifest.sources))
  const revisionInput = new Uint8Array(canonicalSources.byteLength + promptBytes.byteLength)
  revisionInput.set(canonicalSources)
  revisionInput.set(promptBytes, canonicalSources.byteLength)
  if (await sha256Hex(revisionInput) !== revision) throw new Error('提示词 registry manifest 与提示词快照哈希不一致')

  const rawItems = parseRegistryJson(promptBytes, '提示词快照')
  if (!Array.isArray(rawItems) || rawItems.length !== manifest.total) {
    throw new Error('提示词 registry 快照数量与 manifest 不一致')
  }
  const sourceById = new Map(sources.map((source) => [source.id, source]))
  const sourceCounts = new Map(sources.map((source) => [source.id, 0]))
  const seenIds = new Set<string>()
  const items = rawItems.map((rawItem) => {
    const sourceId = plainRecord(rawItem) && typeof rawItem.sourceId === 'string' ? rawItem.sourceId : ''
    const source = sourceById.get(sourceId)
    if (!source) throw new Error(`提示词 registry 记录引用了未知来源：${sourceId.slice(0, 80)}`)
    const sortOrder = sourceCounts.get(sourceId) || 0
    const item = normalizeRegistryItem(rawItem, source, sortOrder)
    if (seenIds.has(item.id)) throw new Error(`提示词 registry 记录 ID 重复：${item.id}`)
    seenIds.add(item.id)
    sourceCounts.set(sourceId, sortOrder + 1)
    return item
  })
  for (const source of sources) {
    if (sourceCounts.get(source.id) !== source.count) throw new Error(`提示词 registry 来源计数不一致：${source.id}`)
  }
  const syncedAt = nowIso()
  const fetchMs = Math.max(0, Math.round(performance.now() - started))
  return {
    items,
    sources: sources.map((source) => ({
      id: source.id,
      name: source.name,
      adapter: 'registry',
      url: source.upstreamUrl,
      homepage: source.homepage,
      registry_path: source.path,
      count: source.count,
      sha256: source.sha256,
      status: 'ready',
      error: '',
      synced_at: syncedAt,
      fetch_ms: fetchMs,
    })),
    synced_at: syncedAt,
    registry: { url: PROMPT_REGISTRY_BASE, revision, generated_at: generatedAt },
  }
}

function nonnegativeInteger(value: unknown, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : fallback
}

function continuationState(job?: Record<string, any>) {
  if (!job) {
    const result = { requested: 0, succeeded: 0, failed: 0, duration_seconds: 0, images: [], failures: [], batches: [] }
    return { result, batches: [] as Array<Record<string, any>> }
  }
  const rawResult = job.result && typeof job.result === 'object' ? structuredClone(job.result) : {}
  const images = Array.isArray(rawResult.images) ? rawResult.images.map((image: any) => ({ ...image })) : []
  const failures = Array.isArray(rawResult.failures) ? rawResult.failures.map((failure: any) => ({ ...failure })) : []
  const requested = nonnegativeInteger(rawResult.requested, nonnegativeInteger(job.count, images.length + failures.length))
  const legacyBatchId = String(job.batch_id || `${job.id || 'legacy'}-initial`)
  images.forEach((image: Record<string, any>, index: number) => {
    image.index ||= index + 1
    image.batch_index ||= index + 1
    image.batch_id ||= legacyBatchId
    image.prompt ??= String(job.prompt || '')
    image.model ??= String(job.model || '')
    image.size ??= String(job.size || '')
    image.mode ??= String(rawResult.mode || job.mode || '')
    image.protocol ??= String(rawResult.protocol || job.protocol || '')
    image.connection_id ??= String(job.connection_id || '')
    image.connection_name ??= String(job.connection_name || '')
    image.created_at ??= String(job.completed_at || job.created_at || '')
  })
  let batches = Array.isArray(rawResult.batches) ? rawResult.batches.map((batch: any) => ({ ...batch })) : []
  if (!batches.length && (requested || images.length || failures.length)) {
    batches = [{
      id: legacyBatchId,
      status: String(job.status || 'completed'),
      created_at: String(job.created_at || ''),
      completed_at: String(job.completed_at || ''),
      prompt: String(job.prompt || ''),
      model: String(job.model || ''),
      size: String(job.size || ''),
      mode: String(rawResult.mode || job.mode || ''),
      protocol: String(rawResult.protocol || job.protocol || ''),
      connection_id: String(job.connection_id || ''),
      connection_name: String(job.connection_name || ''),
      count: requested,
      concurrency: nonnegativeInteger(job.concurrency, 1),
      succeeded: nonnegativeInteger(rawResult.succeeded, images.length),
      failed: nonnegativeInteger(rawResult.failed, failures.length),
      duration_seconds: Number(rawResult.duration_seconds || 0),
    }]
  }
  const result = {
    ...rawResult,
    requested,
    succeeded: nonnegativeInteger(rawResult.succeeded, images.length),
    failed: nonnegativeInteger(rawResult.failed, failures.length),
    duration_seconds: Number(rawResult.duration_seconds || 0),
    images,
    failures,
    batches,
  }
  delete result.current_batch
  return { result, batches }
}

function mergeGenerationBatch(
  previousResult: Record<string, any>,
  previousBatches: Array<Record<string, any>>,
  batch: Record<string, any>,
  batchResult: Record<string, any>,
  status: string,
) {
  const previousImages = Array.isArray(previousResult.images) ? previousResult.images.map((image: any) => ({ ...image })) : []
  const previousFailures = Array.isArray(previousResult.failures) ? previousResult.failures.map((failure: any) => ({ ...failure })) : []
  const offset = previousImages.reduce((maximum: number, image: any) => Math.max(maximum, nonnegativeInteger(image.index)), 0)
  const currentImages = (Array.isArray(batchResult.images) ? batchResult.images : []).map((rawImage: any, index: number) => ({
    ...rawImage,
    index: offset + index + 1,
    batch_index: nonnegativeInteger(rawImage.index, index + 1) || index + 1,
    batch_id: batch.id,
    prompt: batch.prompt,
    model: batch.model,
    size: batch.size,
    mode: batchResult.mode || batch.mode || '',
    protocol: batchResult.protocol || batch.protocol || '',
    connection_id: batch.connection_id || '',
    connection_name: batch.connection_name || '',
    created_at: rawImage.created_at || batch.completed_at || batch.created_at || '',
  }))
  const currentFailures = (Array.isArray(batchResult.failures) ? batchResult.failures : []).map((rawFailure: any, index: number) => ({
    ...rawFailure,
    index: nonnegativeInteger(rawFailure.index, index + 1) || index + 1,
    batch_id: batch.id,
  }))
  const currentSucceeded = nonnegativeInteger(batchResult.succeeded, currentImages.length)
  const currentFailed = nonnegativeInteger(batchResult.failed, currentFailures.length)
  const batchSummary = {
    ...batch,
    status,
    protocol: batchResult.protocol || batch.protocol || '',
    mode: batchResult.mode || batch.mode || '',
    succeeded: currentSucceeded,
    failed: currentFailed,
    duration_seconds: Number(batchResult.duration_seconds || 0),
  }
  return {
    protocol: batchResult.protocol || batch.protocol || previousResult.protocol || '',
    mode: batchResult.mode || batch.mode || previousResult.mode || '',
    model: batch.model,
    requested: nonnegativeInteger(previousResult.requested) + nonnegativeInteger(batch.count),
    concurrency: batch.concurrency || 1,
    succeeded: nonnegativeInteger(previousResult.succeeded, previousImages.length) + currentSucceeded,
    failed: nonnegativeInteger(previousResult.failed, previousFailures.length) + currentFailed,
    duration_seconds: Math.round((Number(previousResult.duration_seconds || 0) + Number(batchResult.duration_seconds || 0)) * 10) / 10,
    images: [...previousImages, ...currentImages],
    failures: [...previousFailures, ...currentFailures],
    batches: [...previousBatches.map((item) => ({ ...item })), batchSummary],
    current_batch: batchSummary,
  }
}

function parseBody(options: RequestInit) {
  if (!options.body) return {}
  if (typeof options.body !== 'string') throw new Error('浏览器运行时只接受 JSON 请求')
  return JSON.parse(options.body)
}

function cleanBaseUrl(value: unknown) {
  const raw = String(value || DEFAULT_BASE_URL).trim().replace(/\/+$/, '')
  let parsed: URL
  try {
    parsed = new URL(raw)
  } catch {
    throw new Error('API 地址格式不正确')
  }
  const localHttp = parsed.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname)
  if (parsed.protocol !== 'https:' && !localHttp) throw new Error('API 地址必须使用 HTTPS；本机 localhost 可使用 HTTP')
  if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error('API 地址不能包含账号、查询参数或片段')
  return raw.replace(/\/v1$/i, '')
}

function apiUrl(baseUrl: unknown, path: string) {
  if (!path.startsWith('/')) throw new Error('API endpoint path must start with /')
  return `${cleanBaseUrl(baseUrl)}${path}`
}

async function responseError(response: Response) {
  const bytes = new Uint8Array(await response.arrayBuffer())
  const contentType = response.headers.get('Content-Type') || ''
  const charset = contentType.match(/charset\s*=\s*["']?([^;\s"']+)/i)?.[1] || ''
  const encodings = Array.from(new Set([charset, 'utf-8', 'gb18030'].filter(Boolean)))
  let text = ''
  for (const encoding of encodings) {
    try {
      text = new TextDecoder(encoding, { fatal: true }).decode(bytes)
      break
    } catch {}
  }
  if (!text) text = new TextDecoder('utf-8').decode(bytes)
  const body = (() => {
    try { return JSON.parse(text) } catch { return null }
  })()
  const message = body?.error?.message || body?.error || body?.message || `HTTP ${response.status}`
  return new Error(String(message === `HTTP ${response.status}` && text.trim() ? text.trim().slice(0, 1000) : message))
}

async function dataUrlToBlob(value: string) {
  const response = await fetch(value)
  return response.blob()
}

async function inputImageBlob(value: string, index: number) {
  const blob = await dataUrlToBlob(value)
  if (!INPUT_IMAGE_TYPES.has(blob.type) || !blob.size || blob.size > MAX_INPUT_BYTES) {
    throw new Error(`第 ${index + 1} 张参考图片无效或超过 ${MAX_INPUT_BYTES / 1024 / 1024} MiB`)
  }
  return blob
}

function httpUrl(value: unknown) {
  const raw = String(value || '').trim()
  try {
    const parsed = new URL(raw)
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : ''
  } catch {
    return ''
  }
}

type GeminiImageReference =
  | { kind: 'url'; url: string }
  | { kind: 'inline'; data: string; mimeType: string }

function geminiImageReference(data: any): GeminiImageReference | null {
  const candidates = Array.isArray(data?.candidates) ? data.candidates : []
  const parts = candidates.flatMap((candidate: any) => (
    Array.isArray(candidate?.content?.parts) ? candidate.content.parts : []
  ))

  for (const part of parts) {
    const file = part?.fileData || part?.file_data
    const url = httpUrl(file?.fileUri || file?.file_uri)
    if (url) return { kind: 'url', url }
  }

  const markdownImage = /!\[[^\]]*\]\(\s*<?(https?:\/\/[^)\s>]+)>?\s*\)/i
  for (const part of parts) {
    const match = typeof part?.text === 'string' ? part.text.match(markdownImage) : null
    const url = httpUrl(match?.[1])
    if (url) return { kind: 'url', url }
  }

  for (const part of parts) {
    const inline = part?.inlineData || part?.inline_data
    if (inline?.data) {
      return {
        kind: 'inline',
        data: String(inline.data),
        mimeType: String(inline.mimeType || inline.mime_type || 'image/png'),
      }
    }
  }
  return null
}

function imageMimeFromBytes(bytes: Uint8Array, fallback = '') {
  if (bytes.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => bytes[index] === value)) return 'image/png'
  if (bytes.length >= 3 && bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return 'image/jpeg'
  if (bytes.length >= 12 && String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP') return 'image/webp'
  if (bytes.length >= 6 && ['GIF87a', 'GIF89a'].includes(String.fromCharCode(...bytes.slice(0, 6)))) return 'image/gif'
  return fallback.startsWith('image/') ? fallback : 'image/png'
}

function blobFromBase64(value: string, mime = '') {
  const binary = atob(value)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return new Blob([bytes], { type: imageMimeFromBytes(bytes, mime) })
}

async function imageDimensions(blob: Blob): Promise<{ width?: number; height?: number }> {
  try {
    const bitmap = await createImageBitmap(blob)
    const dimensions = { width: bitmap.width, height: bitmap.height }
    bitmap.close()
    return dimensions
  } catch {
    return {}
  }
}

class LocalRuntime implements StudioRuntime {
  readonly mode = 'local' as const

  constructor(private readonly token: string) {}

  async request(path: string, options: RequestInit = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...(options.headers || {}), 'X-Klong-Token': this.token },
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`)
    return data
  }

  async archive(payload: Record<string, unknown>) {
    const response = await fetch('/api/gallery/archive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Klong-Token': this.token },
      body: JSON.stringify(payload),
    })
    if (!response.ok) throw await responseError(response)
    return response.blob()
  }

  promptPreview(item: { id: string }) {
    return `/api/preview?id=${encodeURIComponent(item.id)}`
  }

  downloadUrl(url: string) {
    return `${url}${url.includes('?') ? '&' : '?'}download=1`
  }

  dispose() {}
}

class BrowserRuntime implements StudioRuntime {
  readonly mode = 'browser' as const
  private db!: IDBPDatabase
  private promptLibrary?: PromptLibrary
  private readonly objectUrls = new Map<string, string>()
  private readonly activeJobs = new Set<string>()

  async initialize() {
    this.db = await openDB(DB_NAME, DB_VERSION, {
      upgrade(database) {
        database.createObjectStore('settings')
        database.createObjectStore('connections', { keyPath: 'id' })
        database.createObjectStore('jobs', { keyPath: 'id' })
        database.createObjectStore('images', { keyPath: 'id' })
      },
    })
    await this.failInterruptedJobs()
    void navigator.storage?.persist?.().catch(() => false)
    return this
  }

  dispose() {
    for (const url of this.objectUrls.values()) URL.revokeObjectURL(url)
    this.objectUrls.clear()
    this.db?.close()
  }

  promptPreview(item: { preview?: string }) {
    return item.preview || ''
  }

  downloadUrl(url: string) {
    return url
  }

  async request(path: string, options: RequestInit = {}) {
    const url = new URL(path, window.location.origin)
    const method = String(options.method || 'GET').toUpperCase()
    const payload = method === 'GET' ? {} : parseBody(options)

    if (method === 'GET' && url.pathname === '/api/settings') return this.settingsSnapshot()
    if (method === 'GET' && url.pathname === '/api/storage') return this.storageSnapshot()
    if (method === 'POST' && url.pathname === '/api/storage') return this.storageAction(payload)
    if (method === 'GET' && url.pathname === '/api/library') return this.librarySnapshot()
    if (method === 'GET' && url.pathname === '/api/prompts') return this.promptPage(url.searchParams)
    if (method === 'POST' && url.pathname === '/api/refresh') return this.refreshLibrary()
    if (method === 'POST' && url.pathname === '/api/settings/test') return this.testConnection(payload)
    if (method === 'POST' && url.pathname === '/api/connections') return this.saveConnection('', payload)
    if (method === 'GET' && url.pathname === '/api/gallery') return this.galleryPage(url.searchParams)
    if (method === 'POST' && url.pathname === '/api/gallery/action') return this.galleryAction(payload)
    if (method === 'GET' && url.pathname === '/api/jobs') return this.jobHistory(url.searchParams)
    if (method === 'POST' && url.pathname === '/api/jobs') return this.createJob(payload)

    const connectionMatch = url.pathname.match(/^\/api\/connections\/([A-Za-z0-9_-]+)(?:\/(activate|delete))?$/)
    if (method === 'POST' && connectionMatch) {
      const [, connectionId, action] = connectionMatch
      if (action === 'activate') return this.activateConnection(connectionId)
      if (action === 'delete') return this.deleteConnection(connectionId)
      return this.saveConnection(connectionId, payload)
    }
    const jobMatch = url.pathname.match(/^\/api\/jobs\/([A-Za-z0-9_-]+)(?:\/(delete))?$/)
    if (method === 'GET' && jobMatch && !jobMatch[2]) return this.getJob(jobMatch[1])
    if (method === 'POST' && jobMatch?.[2] === 'delete') return this.deleteJobHistory(jobMatch[1])

    throw new Error(`浏览器运行时不支持 ${method} ${url.pathname}`)
  }

  async archive(payload: Record<string, unknown>) {
    const selected = await this.selectedImages(payload)
    const zip = new JSZip()
    const names = new Set<string>()
    for (const image of selected) {
      let name = image.name
      let sequence = 2
      while (names.has(name.toLocaleLowerCase())) {
        const dot = image.name.lastIndexOf('.')
        const stem = dot > 0 ? image.name.slice(0, dot) : image.name
        const suffix = dot > 0 ? image.name.slice(dot) : ''
        name = `${stem}-${sequence}${suffix}`
        sequence += 1
      }
      names.add(name.toLocaleLowerCase())
      zip.file(name, image.blob)
    }
    return zip.generateAsync({ type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 6 } })
  }

  private async cryptoKey() {
    let key = await this.db.get('settings', 'crypto-key') as CryptoKey | undefined
    if (!key) {
      key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'])
      await this.db.put('settings', key, 'crypto-key')
    }
    return key
  }

  private async encryptKey(value: string) {
    const key = await this.cryptoKey()
    const iv = crypto.getRandomValues(new Uint8Array(12))
    const cipher = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, encoder.encode(value))
    return { key_cipher: cipher, key_iv: iv, key_hint: `••••${value.slice(-4)}` }
  }

  private async decryptKey(connection: StoredConnection) {
    if (!connection.key_cipher || !connection.key_iv) return ''
    try {
      const key = await this.cryptoKey()
      const iv = new Uint8Array(connection.key_iv)
      const clear = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, connection.key_cipher)
      return decoder.decode(clear)
    } catch {
      return ''
    }
  }

  private connectionPublic(connection: StoredConnection) {
    const configured = Boolean(connection.key_cipher && connection.key_iv)
    return {
      id: connection.id,
      name: connection.name,
      base_url: connection.base_url,
      default_model: connection.default_model,
      models: connection.models || [],
      models_synced_at: connection.models_synced_at || '',
      key_configured: configured,
      key_source: configured ? 'browser' : 'none',
      key_hint: configured ? connection.key_hint : '',
      persistent_secret_storage: true,
      readonly: false,
    }
  }

  private async settingsSnapshot() {
    const stored = await this.db.getAll('connections') as StoredConnection[]
    const connections = stored
      .sort((left, right) => left.created_at.localeCompare(right.created_at))
      .map((connection) => this.connectionPublic(connection))
    let activeId = String(await this.db.get('settings', 'active-connection-id') || '')
    if (!connections.some((connection) => connection.id === activeId)) {
      activeId = connections[0]?.id || ''
      if (activeId) await this.db.put('settings', activeId, 'active-connection-id')
    }
    const fallback = {
      id: '', name: '未配置', base_url: DEFAULT_BASE_URL, default_model: DEFAULT_MODEL,
      models: [], models_synced_at: '', key_configured: false, key_source: 'none', key_hint: '',
      persistent_secret_storage: true, readonly: false,
    }
    const active = connections.find((connection) => connection.id === activeId) || fallback
    return {
      schema_version: 2,
      runtime: 'browser',
      active_connection_id: activeId,
      active_connection: active,
      connections,
      base_url: active.base_url,
      default_model: active.default_model,
      key_configured: active.key_configured,
      key_source: active.key_source,
      key_hint: active.key_hint,
      persistent_secret_storage: true,
      models: active.models,
    }
  }

  private async saveConnection(connectionId: string, payload: Record<string, any>) {
    const current = connectionId ? await this.db.get('connections', connectionId) as StoredConnection | undefined : undefined
    const name = String(payload.name || current?.name || '').trim()
    if (!name || name.length > 60) throw new Error('连接名称不能为空且不能超过 60 个字符')
    const defaultModel = String(payload.default_model || current?.default_model || DEFAULT_MODEL).trim()
    if (!defaultModel || defaultModel.length > 120) throw new Error('默认模型不能为空且不能超过 120 个字符')
    const record: StoredConnection = {
      id: current?.id || randomId().slice(0, 12),
      name,
      base_url: cleanBaseUrl(payload.base_url || current?.base_url),
      default_model: defaultModel,
      models: Array.isArray(payload.models) ? [...new Set(payload.models.map(String).filter(Boolean))] : current?.models || [],
      models_synced_at: Array.isArray(payload.models) ? nowIso() : current?.models_synced_at || '',
      key_hint: current?.key_hint || '',
      key_cipher: current?.key_cipher,
      key_iv: current?.key_iv,
      created_at: current?.created_at || nowIso(),
    }
    if (payload.clear_api_key) {
      delete record.key_cipher
      delete record.key_iv
      record.key_hint = ''
    } else if (String(payload.api_key || '').trim()) {
      Object.assign(record, await this.encryptKey(String(payload.api_key).trim()))
    }
    await this.db.put('connections', record)
    if (!connectionId || payload.activate) await this.db.put('settings', record.id, 'active-connection-id')
    return this.settingsSnapshot()
  }

  private async activateConnection(connectionId: string) {
    const connection = await this.db.get('connections', connectionId)
    if (!connection) throw new Error('连接不存在')
    await this.db.put('settings', connectionId, 'active-connection-id')
    return this.settingsSnapshot()
  }

  private async deleteConnection(connectionId: string) {
    await this.db.delete('connections', connectionId)
    const activeId = await this.db.get('settings', 'active-connection-id')
    if (activeId === connectionId) await this.db.delete('settings', 'active-connection-id')
    return this.settingsSnapshot()
  }

  private async resolveConnection(connectionId: unknown) {
    const id = String(connectionId || await this.db.get('settings', 'active-connection-id') || '')
    const connection = id ? await this.db.get('connections', id) as StoredConnection | undefined : undefined
    if (!connection) throw new Error('请先配置一个连接')
    const apiKey = await this.decryptKey(connection)
    if (!apiKey) throw new Error('当前连接没有可用的 API Key')
    return { connection, apiKey }
  }

  private async fetchModels(baseUrl: string, apiKey: string) {
    const response = await fetch(apiUrl(baseUrl, '/v1/models'), {
      headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' },
      credentials: 'omit',
    })
    if (!response.ok) throw await responseError(response)
    const data = await response.json()
    const raw = Array.isArray(data) ? data : Array.isArray(data?.data) ? data.data : Array.isArray(data?.models) ? data.models : null
    if (!raw) throw new Error('模型接口响应中没有模型数组')
    return imageModelIds(raw)
  }

  private async testConnection(payload: Record<string, any>) {
    const current = payload.connection_id ? await this.db.get('connections', String(payload.connection_id)) as StoredConnection | undefined : undefined
    const key = String(payload.api_key || '').trim() || (current ? await this.decryptKey(current) : '')
    if (!key) throw new Error('请填写 API Key')
    const models = await this.fetchModels(String(payload.base_url || current?.base_url || DEFAULT_BASE_URL), key)
    return { ok: true, models, model_count: models.length }
  }

  private async loadPromptLibrary(force = false) {
    if (force) {
      this.promptLibrary = await fetchPromptRegistry(true)
      return this.promptLibrary
    }
    if (this.promptLibrary) return this.promptLibrary
    const response = await fetch(new URL('prompt-library.json', document.baseURI))
    if (!response.ok) throw new Error(`提示词快照加载失败：HTTP ${response.status}`)
    const data = await response.json()
    if (!Array.isArray(data.items) || !Array.isArray(data.sources)) throw new Error('提示词快照格式不正确')
    this.promptLibrary = data
    return data as PromptLibrary
  }

  private async librarySnapshot() {
    const library = await this.loadPromptLibrary()
    return {
      sources: library.sources,
      syncing: false,
      synced_at: library.synced_at,
      prompt_count: library.items.length,
      registry_revision: library.registry?.revision || '',
      registry_generated_at: library.registry?.generated_at || '',
    }
  }

  private async refreshLibrary() {
    await this.loadPromptLibrary(true)
    return { ok: true }
  }

  private async promptPage(params: URLSearchParams) {
    const library = await this.loadPromptLibrary()
    const offset = Math.max(0, Number(params.get('offset') || 0))
    const limit = Math.min(60, Math.max(1, Number(params.get('limit') || 24)))
    const keyword = String(params.get('keyword') || '').trim().toLocaleLowerCase()
    const source = String(params.get('source') || '')
    const category = String(params.get('category') || '')
    const sourceItems = library.items.filter((item) => !source || item.source_id === source)
    const categoryLabel = (item: Record<string, any>) => [item.category, item.sub_category]
      .map((value) => String(value || '').trim())
      .filter(Boolean)
      .join(' / ')
    const categories = [...new Set(sourceItems.map(categoryLabel).filter(Boolean))]
      .sort((left, right) => String(left).localeCompare(String(right), 'zh-CN'))
    const items = sourceItems.filter((item) => {
      const itemCategory = categoryLabel(item)
      if (category && itemCategory !== category) return false
      if (!keyword) return true
      return ['title', 'description', 'prompt', 'category', 'sub_category', 'author', 'source_name']
        .map((field) => String(item[field] || '')).join(' ').toLocaleLowerCase().includes(keyword)
    })
    return {
      items: items.slice(offset, offset + limit), total: items.length, offset, limit,
      has_more: offset + limit < items.length, categories,
    }
  }

  private async storageSnapshot() {
    const images = await this.db.getAll('images') as StoredImage[]
    const estimate: StorageEstimate = await navigator.storage?.estimate?.().catch(() => ({})) || {}
    const persisted = await navigator.storage?.persisted?.().catch(() => false) || false
    return {
      output_dir: '此浏览器 / 小恐龙图库',
      default_output_dir: '此浏览器 / 小恐龙图库',
      source: 'browser',
      locked: true,
      image_count: images.length,
      total_bytes: images.reduce((sum, image) => sum + Number(image.bytes || image.blob?.size || 0), 0),
      browser_persisted: persisted,
      quota_bytes: Number(estimate.quota || 0),
      usage_bytes: Number(estimate.usage || 0),
    }
  }

  private async storageAction(payload: Record<string, any>) {
    if (payload.action === 'persist') {
      const persisted = await navigator.storage?.persist?.().catch(() => false) || false
      return { ...(await this.storageSnapshot()), browser_persisted: persisted }
    }
    if (payload.action === 'clear') {
      const transaction = this.db.transaction(['jobs', 'images'], 'readwrite')
      await Promise.all([transaction.objectStore('jobs').clear(), transaction.objectStore('images').clear(), transaction.done])
      for (const url of this.objectUrls.values()) URL.revokeObjectURL(url)
      this.objectUrls.clear()
      return this.storageSnapshot()
    }
    throw new Error('浏览器图库不使用本地目录')
  }

  private async imageUrl(imageId: string) {
    const existing = this.objectUrls.get(imageId)
    if (existing) return existing
    const image = await this.db.get('images', imageId) as StoredImage | undefined
    if (!image) return ''
    const url = URL.createObjectURL(image.blob)
    this.objectUrls.set(imageId, url)
    return url
  }

  private imagePublic(image: StoredImage, url: string) {
    const { blob: _blob, job_id: _jobId, ...publicImage } = image
    return { ...publicImage, relative_path: image.name, url }
  }

  private async galleryRecords(keyword = '') {
    const value = keyword.trim().toLocaleLowerCase()
    const images = await this.db.getAll('images') as StoredImage[]
    return images.filter((image) => !value || `${image.name} ${image.prompt} ${image.model}`.toLocaleLowerCase().includes(value))
  }

  private async galleryPage(params: URLSearchParams) {
    const offset = Math.max(0, Number(params.get('offset') || 0))
    const limit = Math.min(60, Math.max(1, Number(params.get('limit') || 24)))
    const sort = String(params.get('sort') || 'created_desc')
    const records = await this.galleryRecords(String(params.get('keyword') || ''))
    records.sort((left, right) => {
      let result = 0
      if (sort.startsWith('name_')) result = left.name.localeCompare(right.name, 'zh-CN')
      else if (sort.startsWith('size_')) result = left.bytes - right.bytes
      else result = left.created_at.localeCompare(right.created_at)
      return sort.endsWith('_desc') ? -result : result
    })
    const safeOffset = records.length && offset >= records.length ? Math.floor((records.length - 1) / limit) * limit : offset
    const pageRecords = records.slice(safeOffset, safeOffset + limit)
    const items = await Promise.all(pageRecords.map(async (image) => this.imagePublic(image, await this.imageUrl(image.id))))
    return {
      items, total: records.length, offset: safeOffset, limit,
      page: Math.floor(safeOffset / limit) + 1,
      page_count: Math.max(1, Math.ceil(records.length / limit)),
      has_previous: safeOffset > 0,
      has_more: safeOffset + items.length < records.length,
      sort,
    }
  }

  private async selectedImages(payload: Record<string, any>) {
    const scope = String(payload.scope || 'ids')
    const records = await this.galleryRecords(scope === 'query' ? String(payload.keyword || '') : '')
    const ids = new Set(Array.isArray(payload.ids) ? payload.ids.map(String) : [])
    const excluded = new Set(Array.isArray(payload.exclude_ids) ? payload.exclude_ids.map(String) : [])
    const selected = scope === 'query' ? records.filter((image) => !excluded.has(image.id)) : records.filter((image) => ids.has(image.id))
    if (!selected.length) throw new Error('没有选中任何作品')
    if (selected.length > 1000) throw new Error('单次操作最多处理 1000 张作品')
    return selected
  }

  private async galleryAction(payload: Record<string, any>) {
    if (payload.action !== 'delete') throw new Error('不支持的图库操作')
    const images = await this.selectedImages(payload)
    const transaction = this.db.transaction('images', 'readwrite')
    for (const image of images) transaction.store.delete(image.id)
    await transaction.done
    for (const image of images) {
      const url = this.objectUrls.get(image.id)
      if (url) URL.revokeObjectURL(url)
      this.objectUrls.delete(image.id)
    }
    return { action: 'delete', affected: images.length, failed: 0, failures: [] }
  }

  private async failInterruptedJobs() {
    const jobs = await this.db.getAll('jobs') as Array<Record<string, any>>
    const interrupted = jobs.filter((job) => ['queued', 'running'].includes(job.status))
    if (!interrupted.length) return
    const transaction = this.db.transaction('jobs', 'readwrite')
    for (const job of interrupted) {
      const result = job.result && typeof job.result === 'object' ? structuredClone(job.result) : job.result
      const completedAt = nowIso()
      const currentBatch = result?.current_batch
      if (currentBatch) {
        const missing = Math.max(0, nonnegativeInteger(currentBatch.count) - nonnegativeInteger(currentBatch.succeeded) - nonnegativeInteger(currentBatch.failed))
        currentBatch.status = 'failed'
        currentBatch.completed_at = completedAt
        currentBatch.failed = nonnegativeInteger(currentBatch.failed) + missing
        result.failed = nonnegativeInteger(result.failed) + missing
        if (missing) {
          result.failures = [...(Array.isArray(result.failures) ? result.failures : []), {
            batch_id: currentBatch.id,
            error: `页面关闭，${missing} 个请求未完成。`,
          }]
        }
      }
      if (currentBatch && result?.batches?.length) result.batches[result.batches.length - 1] = { ...currentBatch }
      transaction.store.put({
        ...job,
        status: 'failed',
        updated_at: completedAt,
        completed_at: completedAt,
        error: '页面在任务完成前关闭，浏览器无法继续后台生成。',
        result,
      })
    }
    await transaction.done
  }

  private async hydrateJob(job: Record<string, any>) {
    const result = job.result ? { ...job.result } : job.result
    if (result?.images) {
      result.images = await Promise.all(result.images.map(async (image: Record<string, any>) => ({
        ...image,
        url: image.id ? await this.imageUrl(image.id) : '',
      })))
    }
    return { ...job, result }
  }

  private summary(job: Record<string, any>) {
    const result = job.result || {}
    const image = result.images?.[0]
    return {
      id: job.id, name: job.name, status: job.status, created_at: job.created_at,
      updated_at: job.updated_at || job.completed_at || job.created_at,
      completed_at: job.completed_at || '', model: job.model,
      connection_id: job.connection_id, connection_name: job.connection_name,
      count: result.requested || job.count, concurrency: job.concurrency,
      succeeded: result.succeeded || 0, failed: result.failed || 0,
      duration_seconds: result.duration_seconds || 0,
      thumbnail_url: image?.url || '',
    }
  }

  private async jobHistory(params: URLSearchParams) {
    const limit = Math.min(100, Math.max(1, Number(params.get('limit') || 50)))
    const jobs = await this.db.getAll('jobs') as Array<Record<string, any>>
    jobs.sort((left, right) => String(right.updated_at || right.created_at).localeCompare(String(left.updated_at || left.created_at)))
    const hydrated = await Promise.all(jobs.slice(0, limit).map((job) => this.hydrateJob(job)))
    return { items: hydrated.map((job) => this.summary(job)), total: jobs.length }
  }

  private async getJob(jobId: string) {
    const job = await this.db.get('jobs', jobId) as Record<string, any> | undefined
    if (!job) throw new Error('任务不存在')
    return this.hydrateJob(job)
  }

  private async deleteJobHistory(jobId: string) {
    const job = await this.db.get('jobs', jobId) as Record<string, any> | undefined
    if (!job) throw new Error('任务不存在')
    if (this.activeJobs.has(jobId) || ['queued', 'running'].includes(String(job.status))) {
      throw new Error('生成中的任务不能删除')
    }
    await this.db.delete('jobs', jobId)
    return { id: jobId, deleted: true, images_preserved: true }
  }

  private async createJob(payload: Record<string, any>) {
    const prompt = String(payload.prompt || '').trim()
    if (!prompt || prompt.length > 100_000) throw new Error('提示词不能为空且不能超过 100,000 个字符')
    const count = Number(payload.count || 1)
    const concurrency = Number(payload.concurrency || 1)
    if (!Number.isInteger(count) || count < 1 || count > 100 || !Number.isInteger(concurrency) || concurrency < 1 || concurrency > count) {
      throw new Error('生成数量必须为 1-100，并发数必须在 1 和生成数量之间')
    }
    const model = String(payload.model || DEFAULT_MODEL).trim()
    const modelProtocol = imageModelProtocol(model)
    if (!modelProtocol) throw new Error(`${model} 不是此 Skill 可识别的图片模型`)
    const quality = String(payload.quality || '')
    if (quality && !modelQualityOptions(model).includes(quality as any)) throw new Error(`${model} 不支持 quality=${quality}`)
    const requestedSize = String(payload.size || '')
    const size = isExactImageModel(model) || modelProtocol === 'gemini'
      ? requestedSize
      : isNanoBananaModel(model)
        ? normalizeNanoBananaSize(requestedSize)
        : constrainImageSizeValue(requestedSize)
    const geminiImageConfig = modelProtocol === 'gemini'
      ? normalizeGeminiImageConfig(payload, size)
      : { aspect_ratio: '', image_size: '' }
    const inputImages = normalizeInputImages(payload)
    const inputImageBlobs = await Promise.all(inputImages.map(inputImageBlob))
    const normalizedPayload: Record<string, any> = {
      ...payload,
      size,
      ...geminiImageConfig,
      input_images: inputImages,
      input_image_blobs: inputImageBlobs,
    }
    delete normalizedPayload.input_image
    const { connection } = await this.resolveConnection(payload.connection_id)
    const continueJobId = String(payload.continue_job_id || '').trim()
    if (continueJobId && !/^[A-Za-z0-9_-]{1,80}$/.test(continueJobId)) throw new Error('任务 ID 无效')
    const previousJob = continueJobId
      ? await this.db.get('jobs', continueJobId) as Record<string, any> | undefined
      : undefined
    if (continueJobId && !previousJob) throw new Error('要继续的任务不存在')
    if (continueJobId && (this.activeJobs.has(continueJobId) || ['queued', 'running'].includes(String(previousJob?.status)))) {
      throw new Error('当前任务仍在生成，请完成后再追加')
    }
    const { result: previousResult, batches: previousBatches } = continuationState(previousJob)
    const createdAt = nowIso()
    const batch = {
      id: randomId().slice(0, 16),
      status: 'queued',
      created_at: createdAt,
      completed_at: '',
      prompt,
      model,
      size,
      ...geminiImageConfig,
      mode: inputImages.length ? 'image-to-image' : 'text-to-image',
      protocol: String(payload.protocol || ''),
      connection_id: connection.id,
      connection_name: connection.name,
      count,
      concurrency,
    }
    const job = {
      id: continueJobId || randomId().slice(0, 16),
      batch_id: batch.id,
      name: String(previousJob?.name || payload.filename || '').trim() || '创作任务',
      status: 'queued',
      created_at: previousJob?.created_at || createdAt,
      updated_at: createdAt,
      completed_at: '',
      prompt,
      model,
      connection_id: connection.id,
      connection_name: connection.name,
      protocol: String(payload.protocol || ''),
      mode: batch.mode,
      size,
      ...geminiImageConfig,
      count,
      concurrency,
      progress: [],
      result: mergeGenerationBatch(previousResult, previousBatches, batch, {}, 'queued'),
      error: '',
    }
    await this.db.put('jobs', job)
    this.activeJobs.add(job.id)
    void this.runJob(job.id, normalizedPayload, previousResult, previousBatches, batch).finally(() => this.activeJobs.delete(job.id))
    return job
  }

  private async runJob(
    jobId: string,
    payload: Record<string, any>,
    previousResult: Record<string, any>,
    previousBatches: Array<Record<string, any>>,
    batch: Record<string, any>,
  ) {
    const started = performance.now()
    const job = await this.db.get('jobs', jobId) as Record<string, any>
    job.status = 'running'
    job.started_at = nowIso()
    job.updated_at = job.started_at
    if (job.result?.batches?.length) job.result.batches[job.result.batches.length - 1].status = 'running'
    if (job.result?.current_batch) job.result.current_batch.status = 'running'
    await this.db.put('jobs', job)
    const images: Array<Record<string, any>> = []
    const failures: Array<Record<string, any>> = []
    const total = Number(job.count)
    let cursor = 0

    const worker = async () => {
      while (cursor < total) {
        const index = ++cursor
        const requestStarted = performance.now()
        try {
          const generated = await this.generateWithRetry(job, payload, index)
          const dimensions = await imageDimensions(generated.blob)
          const extension = generated.blob.type.includes('jpeg') ? 'jpg' : generated.blob.type.includes('webp') ? 'webp' : 'png'
          const stem = String(payload.filename || 'generated').replace(/[\\/:*?"<>|]+/g, '-').trim().slice(0, 60) || 'generated'
          const uniqueStem = `${stem}-${String(batch.id).slice(0, 8)}`
          const name = total === 1 ? `${uniqueStem}.${extension}` : `${uniqueStem}-${String(index).padStart(3, '0')}.${extension}`
          const imageId = randomId()
          const duration = Math.round((performance.now() - requestStarted) / 100) / 10
          const stored: StoredImage = {
            id: imageId,
            name,
            bytes: generated.blob.size,
            created_at: nowIso(),
            blob: generated.blob,
            prompt: job.prompt,
            model: job.model,
            protocol: generated.protocol,
            mode: payload.input_images?.length ? 'image-to-image' : 'text-to-image',
            connection_id: batch.connection_id,
            connection_name: batch.connection_name,
            ...dimensions,
            duration_seconds: duration,
            job_id: jobId,
            batch_id: batch.id,
            size: batch.size,
          }
          await this.db.put('images', stored)
          images.push({
            index, status: 'success', duration_seconds: duration, attempts: generated.attempts,
            bytes: stored.bytes, width: stored.width, height: stored.height,
            output: name, id: imageId, created_at: stored.created_at,
          })
        } catch (error: any) {
          failures.push({
            index, status: 'failed', duration_seconds: Math.round((performance.now() - requestStarted) / 100) / 10,
            error: String(error?.message || error).slice(0, 1000), attempts: Number(error?.attempts || 1),
          })
        }
        const current = await this.db.get('jobs', jobId) as Record<string, any>
        const runningBatch = { ...batch, status: 'running' }
        current.result = mergeGenerationBatch(previousResult, previousBatches, runningBatch, this.batchResult(job, images, failures, started), 'running')
        current.updated_at = nowIso()
        await this.db.put('jobs', current)
      }
    }

    try {
      await Promise.all(Array.from({ length: Math.min(job.concurrency, total) }, () => worker()))
      const current = await this.db.get('jobs', jobId) as Record<string, any>
      const status = failures.length ? 'failed' : 'completed'
      const completedAt = nowIso()
      const completedBatch = { ...batch, status, completed_at: completedAt }
      current.status = status
      current.updated_at = completedAt
      current.completed_at = completedAt
      current.error = failures.map((failure) => failure.error).join('; ').slice(0, 1000)
      current.result = mergeGenerationBatch(previousResult, previousBatches, completedBatch, this.batchResult(job, images, failures, started), status)
      await this.db.put('jobs', current)
    } catch (error: any) {
      const current = await this.db.get('jobs', jobId) as Record<string, any>
      const completedAt = nowIso()
      const completedBatch = { ...batch, status: 'failed', completed_at: completedAt }
      current.status = 'failed'
      current.updated_at = completedAt
      current.completed_at = completedAt
      current.error = String(error?.message || error).slice(0, 1000)
      const failed = [...failures, { error: current.error }]
      current.result = mergeGenerationBatch(previousResult, previousBatches, completedBatch, this.batchResult(job, images, failed, started), 'failed')
      await this.db.put('jobs', current)
    }
  }

  private batchResult(job: Record<string, any>, images: Array<Record<string, any>>, failures: Array<Record<string, any>>, started: number) {
    return {
      protocol: imageModelProtocol(job.model) || '',
      mode: job.mode,
      model: job.model,
      requested: job.count,
      concurrency: job.concurrency,
      succeeded: images.length,
      failed: failures.length,
      duration_seconds: Math.round((performance.now() - started) / 100) / 10,
      images: [...images].sort((left, right) => left.index - right.index),
      failures: [...failures].sort((left, right) => left.index - right.index),
    }
  }

  private async generateWithRetry(job: Record<string, any>, payload: Record<string, any>, index: number) {
    const retries = 0
    let lastError: any
    for (let attempt = 1; attempt <= retries + 1; attempt += 1) {
      try {
        const generated = await this.generateOne(job, payload)
        return { ...generated, attempts: attempt, index }
      } catch (error: any) {
        lastError = error
        const transient = error?.transient === true
        if (!transient || attempt > retries) break
        await new Promise((resolve) => window.setTimeout(resolve, 3000 * 2 ** (attempt - 1)))
      }
    }
    lastError.attempts = lastError?.attempts || retries + 1
    throw lastError
  }

  private async generateOne(job: Record<string, any>, payload: Record<string, any>) {
    const { connection, apiKey } = await this.resolveConnection(job.connection_id)
    const expectedProtocol = imageModelProtocol(job.model)
    if (!expectedProtocol) throw new Error(`${job.model} 不是此 Skill 可识别的图片模型`)
    const protocol = String(payload.protocol || expectedProtocol)
    if (protocol !== expectedProtocol) throw new Error(`${job.model} 必须使用 ${expectedProtocol} 协议`)
    const controller = new AbortController()
    const timeoutSeconds = 600
    const timeout = window.setTimeout(() => controller.abort(), timeoutSeconds * 1000)
    try {
      const inputImages = Array.isArray(payload.input_images) ? payload.input_images as string[] : []
      const inputImageBlobs = Array.isArray(payload.input_image_blobs) ? payload.input_image_blobs as Blob[] : []
      if (protocol === 'gemini') {
        const parts: Array<Record<string, any>> = [{ text: job.prompt }]
        for (const inputImage of inputImages) {
          const [header, data] = inputImage.split(',', 2)
          parts.push({ inlineData: { mimeType: header.match(/^data:([^;]+)/)?.[1] || 'image/png', data } })
        }
        const imageSettings = normalizeGeminiImageConfig(job, String(job.size || ''))
        const imageConfig: Record<string, string> = {}
        if (imageSettings.aspect_ratio) imageConfig.aspectRatio = imageSettings.aspect_ratio
        if (imageSettings.image_size) imageConfig.imageSize = imageSettings.image_size
        const generationConfig: Record<string, any> = { responseModalities: ['IMAGE'] }
        if (Object.keys(imageConfig).length) generationConfig.imageConfig = imageConfig
        const response = await fetch(apiUrl(connection.base_url, `/v1beta/models/${encodeURIComponent(job.model)}:generateContent`), {
          method: 'POST',
          headers: { 'x-goog-api-key': apiKey, 'Content-Type': 'application/json' },
          body: JSON.stringify({ contents: [{ role: 'user', parts }], generationConfig }),
          credentials: 'omit',
          signal: controller.signal,
        })
        if (!response.ok) throw await this.generationError(response)
        const data = await response.json()
        const image = geminiImageReference(data)
        if (image?.kind === 'inline') return { blob: blobFromBase64(image.data, image.mimeType), protocol: 'gemini' }
        if (image?.kind === 'url') {
          const imageResponse = await fetch(image.url, { credentials: 'omit', signal: controller.signal })
          if (!imageResponse.ok) throw await this.generationError(imageResponse)
          return { blob: await imageResponse.blob(), protocol: 'gemini' }
        }
        throw new Error('Gemini 响应中没有文件地址、Markdown 图片地址或内联图片数据')
      }

      let response: Response
      if (inputImageBlobs.length) {
        const body = new FormData()
        body.set('model', job.model)
        body.set('prompt', job.prompt)
        body.set('n', '1')
        if (job.size) body.set('size', job.size)
        if (payload.quality) body.set('quality', String(payload.quality))
        inputImageBlobs.forEach((image, index) => {
          const extension = image.type.includes('webp') ? 'webp' : image.type.includes('png') ? 'png' : 'jpg'
          body.append('image', image, `reference-${index + 1}.${extension}`)
        })
        response = await fetch(apiUrl(connection.base_url, '/v1/images/edits'), {
          method: 'POST', headers: { Authorization: `Bearer ${apiKey}` }, body, credentials: 'omit', signal: controller.signal,
        })
      } else {
        response = await fetch(apiUrl(connection.base_url, '/v1/images/generations'), {
          method: 'POST',
          headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: job.model, prompt: job.prompt, n: 1, ...(job.size ? { size: job.size } : {}), ...(payload.quality ? { quality: payload.quality } : {}) }),
          credentials: 'omit',
          signal: controller.signal,
        })
      }
      if (!response.ok) throw await this.generationError(response)
      const data = await response.json()
      const items = Array.isArray(data?.data) ? data.data : []
      const item = items[0]
      if (!item || typeof item !== 'object') throw new Error('接口响应中没有 data[0] 图片对象')
      if (typeof item.b64_json === 'string' && item.b64_json) {
        return { blob: blobFromBase64(item.b64_json), protocol: 'openai' }
      }
      const imageUrl = httpUrl(item.url)
      if (imageUrl) {
        const imageResponse = await fetch(imageUrl, { credentials: 'omit', signal: controller.signal })
        if (!imageResponse.ok) throw await this.generationError(imageResponse)
        return { blob: await imageResponse.blob(), protocol: 'openai' }
      }
      throw new Error('接口响应中没有可用的 Base64 图片或 HTTP(S) 图片地址')
    } catch (error: any) {
      if (error?.name === 'AbortError' || error instanceof TypeError) error.transient = true
      throw error
    } finally {
      window.clearTimeout(timeout)
    }
  }

  private async generationError(response: Response) {
    const error: any = await responseError(response)
    error.transient = response.status === 429 || response.status >= 500
    return error
  }
}

export async function createStudioRuntime(token: string): Promise<StudioRuntime> {
  if (token && token !== '__KLONG_TOKEN__') return new LocalRuntime(token)
  return new BrowserRuntime().initialize()
}
