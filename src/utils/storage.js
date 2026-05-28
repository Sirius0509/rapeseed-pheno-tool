const KEY = 'rapeseed-pheno-tool:samples'

export function loadSamples() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]')
  } catch {
    return []
  }
}

export function saveSamples(samples) {
  localStorage.setItem(KEY, JSON.stringify(samples))
}
