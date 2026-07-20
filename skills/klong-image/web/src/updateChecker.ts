export type VersionTag = {
  name?: string
}

export function normalizeVersion(value: string): number[] | null {
  const match = String(value || '').trim().match(/^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/i)
  return match ? match.slice(1).map(Number) : null
}

export function compareVersions(left: string, right: string): number {
  const leftParts = normalizeVersion(left)
  const rightParts = normalizeVersion(right)
  if (!leftParts || !rightParts) return 0
  for (let index = 0; index < 3; index += 1) {
    if (leftParts[index] !== rightParts[index]) return leftParts[index] > rightParts[index] ? 1 : -1
  }
  return 0
}

export function latestVersionTag(tags: VersionTag[]): string {
  return tags
    .map((tag) => String(tag.name || '').trim())
    .filter((tag) => normalizeVersion(tag))
    .sort((left, right) => compareVersions(right, left))[0] || ''
}
