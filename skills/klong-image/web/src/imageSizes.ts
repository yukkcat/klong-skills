export type ImageResolution = 'auto' | '1K' | '2K' | '4K'
export type ImageRatio = 'auto' | '1:1' | '2:3' | '3:2' | '3:4' | '4:3' | '9:16' | '16:9'

export type ImageSizePreset = {
  value: string
  ratio: ImageRatio
  resolution: ImageResolution
  width?: number
  height?: number
  limited?: boolean
}

export const MAX_IMAGE_EDGE = 3840
export const MAX_IMAGE_PIXELS = 8_294_400

const BASE_IMAGE_SIZES: Record<Exclude<ImageRatio, 'auto'>, readonly [number, number]> = {
  '1:1': [1024, 1024],
  '2:3': [1024, 1536],
  '3:2': [1536, 1024],
  '3:4': [1024, 1365],
  '4:3': [1365, 1024],
  '9:16': [1080, 1920],
  '16:9': [1920, 1080],
}

const RESOLUTION_MULTIPLIERS: Record<Exclude<ImageResolution, 'auto'>, number> = {
  '1K': 1,
  '2K': 2,
  '4K': 4,
}

export const IMAGE_RATIOS: ImageRatio[] = ['auto', '1:1', '2:3', '3:2', '3:4', '4:3', '9:16', '16:9']
export const IMAGE_RESOLUTIONS: ImageResolution[] = ['auto', '1K', '2K', '4K']

export function constrainImageDimensions(width: number, height: number) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    throw new Error('Image dimensions must be positive numbers')
  }
  const scale = Math.min(
    1,
    MAX_IMAGE_EDGE / Math.max(width, height),
    Math.sqrt(MAX_IMAGE_PIXELS / (width * height)),
  )
  const constrainedWidth = Math.max(1, Math.floor(width * scale))
  const constrainedHeight = Math.max(1, Math.floor(height * scale))
  return {
    width: constrainedWidth,
    height: constrainedHeight,
    limited: scale < 1,
  }
}

export function constrainImageSizeValue(value: string): string {
  const normalized = String(value || '').trim()
  if (!normalized || normalized === 'auto') return ''
  const match = normalized.match(/^(\d+)\s*[xX×]\s*(\d+)$/)
  if (!match) return normalized
  const constrained = constrainImageDimensions(Number(match[1]), Number(match[2]))
  return `${constrained.width}x${constrained.height}`
}

export function imageSizeFor(ratio: ImageRatio, resolution: ImageResolution): ImageSizePreset {
  if (ratio === 'auto' || resolution === 'auto') {
    return { value: 'auto', ratio: 'auto', resolution: 'auto' }
  }
  const [baseWidth, baseHeight] = BASE_IMAGE_SIZES[ratio]
  const multiplier = RESOLUTION_MULTIPLIERS[resolution]
  const constrained = constrainImageDimensions(baseWidth * multiplier, baseHeight * multiplier)
  return {
    value: `${constrained.width}x${constrained.height}`,
    ratio,
    resolution,
    ...constrained,
  }
}

export const IMAGE_SIZE_PRESETS: ImageSizePreset[] = [
  imageSizeFor('auto', 'auto'),
  ...IMAGE_RATIOS.slice(1).flatMap((ratio) => (
    IMAGE_RESOLUTIONS.slice(1).map((resolution) => imageSizeFor(ratio, resolution))
  )),
]

export function imageSizePreset(value: string): ImageSizePreset {
  return IMAGE_SIZE_PRESETS.find((item) => item.value === value) || IMAGE_SIZE_PRESETS[0]
}

export function imageSizeLabel(value: string): string {
  if (!value || value === 'auto') return '自动尺寸'
  const selected = IMAGE_SIZE_PRESETS.find((item) => item.value === value)
  if (!selected) return value
  return `${selected.ratio} · ${selected.resolution} · ${selected.width}x${selected.height}${selected.limited ? ' · 接口上限' : ''}`
}
