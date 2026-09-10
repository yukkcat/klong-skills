export type ImageModelProtocol = 'openai' | 'gemini'
export type ImageQuality = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

const MODEL_ID_PATTERN = /^[a-z0-9][a-z0-9._-]{0,119}$/
const QUALITY_OPTIONS: Readonly<Record<string, readonly ImageQuality[]>> = {
  'gpt-image-2-high': ['medium', 'high'],
  'gpt-image-2.5-flare': ['low', 'medium', 'high', 'xhigh', 'max'],
  'gpt-image-2.5-sunburst': ['low', 'medium', 'high', 'xhigh', 'max'],
}

export function imageModelProtocol(value: unknown): ImageModelProtocol | null {
  const model = String(value || '').trim().toLocaleLowerCase()
  if (!MODEL_ID_PATTERN.test(model)) return null
  if (model.startsWith('gpt-image-') && !model.includes('-codex')) return 'openai'
  if (model.startsWith('nano-banana')) return 'openai'
  if (model.startsWith('gemini-') && model.includes('image')) return 'gemini'
  return null
}

export function imageModelIds(items: unknown[]): string[] {
  const result: string[] = []
  const seen = new Set<string>()
  for (const item of items) {
    const model = String(typeof item === 'string' ? item : (item as Record<string, unknown> | null)?.id || '').trim()
    if (model && !seen.has(model) && imageModelProtocol(model)) {
      seen.add(model)
      result.push(model)
    }
  }
  return result
}

export function isExactImageModel(value: unknown): boolean {
  const model = String(value || '').trim().toLocaleLowerCase()
  return model.startsWith('gpt-image-') && model.endsWith('-exact')
}

export function isNanoBananaModel(value: unknown): boolean {
  return String(value || '').trim().toLocaleLowerCase().startsWith('nano-banana')
}

export function normalizeNanoBananaSize(value: unknown): string {
  const size = String(value || '').trim()
  if (!size || size === 'auto') return ''
  const dimensions = size.match(/^(\d+)\s*[xX×]\s*(\d+)$/)
  if (dimensions && !/^0+$/.test(dimensions[1]) && !/^0+$/.test(dimensions[2])) {
    return `${dimensions[1].replace(/^0+(?=\d)/, '')}x${dimensions[2].replace(/^0+(?=\d)/, '')}`
  }
  const ratio = size.match(/^(\d+)\s*:\s*(\d+)$/)
  if (ratio && !/^0+$/.test(ratio[1]) && !/^0+$/.test(ratio[2])) {
    return `${ratio[1].replace(/^0+(?=\d)/, '')}:${ratio[2].replace(/^0+(?=\d)/, '')}`
  }
  const tier = size.match(/^([124])K$/i)
  if (tier) return `${tier[1]}K`
  throw new Error('Nano Banana 尺寸必须是宽x高、宽:高或 1K/2K/4K')
}

export function modelQualityOptions(value: unknown): readonly ImageQuality[] {
  return QUALITY_OPTIONS[String(value || '').trim().toLocaleLowerCase()] || []
}
