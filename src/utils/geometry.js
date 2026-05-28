export function distance(a, b) {
  if (!a || !b) return 0
  return Math.hypot(b.x - a.x, b.y - a.y)
}

export function verticalDistance(a, b) {
  if (!a || !b) return 0
  return Math.abs(b.y - a.y)
}

export function angleFromThreePoints(a, vertex, c) {
  if (!a || !vertex || !c) return 0
  const ab = { x: a.x - vertex.x, y: a.y - vertex.y }
  const cb = { x: c.x - vertex.x, y: c.y - vertex.y }
  const dot = ab.x * cb.x + ab.y * cb.y
  const magA = Math.hypot(ab.x, ab.y)
  const magC = Math.hypot(cb.x, cb.y)
  if (!magA || !magC) return 0
  const cosine = Math.min(1, Math.max(-1, dot / (magA * magC)))
  return (Math.acos(cosine) * 180) / Math.PI
}

export function round(value, digits = 2) {
  if (!Number.isFinite(value)) return ''
  const factor = 10 ** digits
  return Math.round(value * factor) / factor
}
