import { openDB, type IDBPDatabase } from 'idb'
import JSZip from 'jszip'

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
  width?: number
  height?: number
  duration_seconds?: number
  job_id: string
}

type PromptLibrary = {
  items: Array<Record<string, any>>
  sources: Array<Record<string, any>>
  synced_at: string
}

const DB_NAME = 'klong-prompt-studio'
const DB_VERSION = 1
const DEFAULT_BASE_URL = 'https://api.klong.lat'
const DEFAULT_MODEL = 'gpt-image-2'
const SERIAL_MODELS = new Set(['gpt-image-2-codex', 'gpt-image-2-vip'])
const encoder = new TextEncoder()
const decoder = new TextDecoder()

function nowIso() {
  return new Date().toISOString()
}

function randomId() {
  return crypto.randomUUID().replaceAll('-', '')
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
  return raw
}

async function responseError(response: Response) {
  const body = await response.json().catch(() => null)
  const message = body?.error?.message || body?.error || body?.message || `HTTP ${response.status}`
  return new Error(String(message))
}

async function dataUrlToBlob(value: string) {
  const response = await fetch(value)
  return response.blob()
}

function blobFromBase64(value: string, mime = 'image/png') {
  const binary = atob(value)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return new Blob([bytes], { type: mime })
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
    const response = await fetch(`${cleanBaseUrl(baseUrl)}/v1/models`, {
      headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' },
      credentials: 'omit',
    })
    if (!response.ok) throw await responseError(response)
    const data = await response.json()
    const raw = Array.isArray(data) ? data : Array.isArray(data.data) ? data.data : []
    return [...new Set(raw.map((item: any) => typeof item === 'string' ? item : item?.id).filter(Boolean).map(String))]
  }

  private async testConnection(payload: Record<string, any>) {
    const current = payload.connection_id ? await this.db.get('connections', String(payload.connection_id)) as StoredConnection | undefined : undefined
    const key = String(payload.api_key || '').trim() || (current ? await this.decryptKey(current) : '')
    if (!key) throw new Error('请填写 API Key')
    const models = await this.fetchModels(String(payload.base_url || current?.base_url || DEFAULT_BASE_URL), key)
    return { ok: true, models, model_count: models.length }
  }

  private async loadPromptLibrary(force = false) {
    if (this.promptLibrary && !force) return this.promptLibrary
    const response = await fetch(new URL('prompt-library.json', document.baseURI), { cache: force ? 'reload' : 'default' })
    if (!response.ok) throw new Error(`提示词快照加载失败：HTTP ${response.status}`)
    const data = await response.json()
    if (!Array.isArray(data.items) || !Array.isArray(data.sources)) throw new Error('提示词快照格式不正确')
    this.promptLibrary = data
    return data as PromptLibrary
  }

  private async librarySnapshot() {
    const library = await this.loadPromptLibrary()
    return { sources: library.sources, syncing: false, synced_at: library.synced_at, prompt_count: library.items.length }
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
    const categories = [...new Set(sourceItems.map((item) => item.category || item.sub_category || '').filter(Boolean))]
      .sort((left, right) => String(left).localeCompare(String(right), 'zh-CN'))
    const items = sourceItems.filter((item) => {
      const itemCategory = item.category || item.sub_category || ''
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
      transaction.store.put({
        ...job,
        status: 'failed',
        completed_at: nowIso(),
        error: '页面在任务完成前关闭，浏览器无法继续后台生成。',
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
      completed_at: job.completed_at || '', model: job.model,
      connection_id: job.connection_id, connection_name: job.connection_name,
      count: job.count, concurrency: job.concurrency,
      succeeded: result.succeeded || 0, failed: result.failed || 0,
      duration_seconds: result.duration_seconds || 0,
      thumbnail_url: image?.url || '',
    }
  }

  private async jobHistory(params: URLSearchParams) {
    const limit = Math.min(100, Math.max(1, Number(params.get('limit') || 50)))
    const jobs = await this.db.getAll('jobs') as Array<Record<string, any>>
    jobs.sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)))
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
    if (SERIAL_MODELS.has(model) && concurrency !== 1) throw new Error(`${model} 仅支持并发数 1`)
    const { connection } = await this.resolveConnection(payload.connection_id)
    const job = {
      id: randomId().slice(0, 16),
      name: String(payload.filename || '').trim() || '创作任务',
      status: 'queued',
      created_at: nowIso(),
      prompt,
      model,
      connection_id: connection.id,
      connection_name: connection.name,
      protocol: String(payload.protocol || ''),
      mode: payload.input_image ? 'image-to-image' : 'text-to-image',
      size: String(payload.size || ''),
      count,
      concurrency,
      progress: [],
      result: null,
      error: '',
    }
    await this.db.put('jobs', job)
    this.activeJobs.add(job.id)
    void this.runJob(job.id, payload).finally(() => this.activeJobs.delete(job.id))
    return job
  }

  private async runJob(jobId: string, payload: Record<string, any>) {
    const started = performance.now()
    const job = await this.db.get('jobs', jobId) as Record<string, any>
    job.status = 'running'
    job.started_at = nowIso()
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
          const name = total === 1 ? `${stem}.${extension}` : `${stem}-${String(index).padStart(3, '0')}.${extension}`
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
            mode: payload.input_image ? 'image-to-image' : 'text-to-image',
            ...dimensions,
            duration_seconds: duration,
            job_id: jobId,
          }
          await this.db.put('images', stored)
          images.push({
            index, status: 'success', duration_seconds: duration, attempts: generated.attempts,
            bytes: stored.bytes, width: stored.width, height: stored.height,
            output: name, id: imageId,
          })
        } catch (error: any) {
          failures.push({
            index, status: 'failed', duration_seconds: Math.round((performance.now() - requestStarted) / 100) / 10,
            error: String(error?.message || error).slice(0, 1000), attempts: Number(error?.attempts || 1),
          })
        }
        const current = await this.db.get('jobs', jobId) as Record<string, any>
        current.result = this.jobResult(job, images, failures, started)
        await this.db.put('jobs', current)
      }
    }

    try {
      await Promise.all(Array.from({ length: Math.min(job.concurrency, total) }, () => worker()))
      const current = await this.db.get('jobs', jobId) as Record<string, any>
      current.status = failures.length ? 'failed' : 'completed'
      current.completed_at = nowIso()
      current.error = failures.map((failure) => failure.error).join('; ').slice(0, 1000)
      current.result = this.jobResult(job, images, failures, started)
      await this.db.put('jobs', current)
    } catch (error: any) {
      const current = await this.db.get('jobs', jobId) as Record<string, any>
      current.status = 'failed'
      current.completed_at = nowIso()
      current.error = String(error?.message || error).slice(0, 1000)
      current.result = this.jobResult(job, images, failures, started)
      await this.db.put('jobs', current)
    }
  }

  private jobResult(job: Record<string, any>, images: Array<Record<string, any>>, failures: Array<Record<string, any>>, started: number) {
    return {
      protocol: job.model.startsWith('gemini-') ? 'gemini' : 'openai',
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
    const retries = 2
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
    const controller = new AbortController()
    const timeoutSeconds = String(job.model).toLocaleLowerCase().includes('4k') ? 420 : 360
    const timeout = window.setTimeout(() => controller.abort(), timeoutSeconds * 1000)
    const protocol = String(payload.protocol || (job.model.startsWith('gemini-') ? 'gemini' : 'openai'))
    try {
      if (protocol === 'gemini') {
        const parts: Array<Record<string, any>> = [{ text: job.prompt }]
        if (payload.input_image) {
          const [header, data] = String(payload.input_image).split(',', 2)
          parts.push({ inlineData: { mimeType: header.match(/^data:([^;]+)/)?.[1] || 'image/png', data } })
        }
        const response = await fetch(`${connection.base_url}/v1beta/models/${encodeURIComponent(job.model)}:generateContent`, {
          method: 'POST',
          headers: { 'x-goog-api-key': apiKey, 'Content-Type': 'application/json' },
          body: JSON.stringify({ contents: [{ role: 'user', parts }], generationConfig: { responseModalities: ['TEXT', 'IMAGE'] } }),
          credentials: 'omit',
          signal: controller.signal,
        })
        if (!response.ok) throw await this.generationError(response)
        const data = await response.json()
        const responseParts = data.candidates?.[0]?.content?.parts || []
        const inline = responseParts.map((part: any) => part.inlineData || part.inline_data).find((part: any) => part?.data)
        if (!inline) throw new Error('Gemini 响应中没有图片数据')
        return { blob: blobFromBase64(inline.data, inline.mimeType || inline.mime_type || 'image/png'), protocol: 'gemini' }
      }

      let response: Response
      if (payload.input_image) {
        const image = await dataUrlToBlob(String(payload.input_image))
        const body = new FormData()
        body.set('model', job.model)
        body.set('prompt', job.prompt)
        body.set('n', '1')
        if (job.size) body.set('size', job.size)
        body.set('image', image, `reference.${image.type.includes('webp') ? 'webp' : image.type.includes('png') ? 'png' : 'jpg'}`)
        response = await fetch(`${connection.base_url}/v1/images/edits`, {
          method: 'POST', headers: { Authorization: `Bearer ${apiKey}` }, body, credentials: 'omit', signal: controller.signal,
        })
      } else {
        response = await fetch(`${connection.base_url}/v1/images/generations`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: job.model, prompt: job.prompt, n: 1, ...(job.size ? { size: job.size } : {}) }),
          credentials: 'omit',
          signal: controller.signal,
        })
      }
      if (!response.ok) throw await this.generationError(response)
      const data = await response.json()
      const item = data.data?.[0]
      if (item?.b64_json) return { blob: blobFromBase64(item.b64_json), protocol: 'openai' }
      if (item?.url) {
        const imageResponse = await fetch(item.url, { credentials: 'omit', signal: controller.signal })
        if (!imageResponse.ok) throw await this.generationError(imageResponse)
        return { blob: await imageResponse.blob(), protocol: 'openai' }
      }
      throw new Error('接口响应中没有图片数据')
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
