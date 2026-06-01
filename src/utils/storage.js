const KEY = 'rapeseed-pheno-tool:samples'
const SILIQUE_KEY = 'rapeseed-pheno-tool:siliques'

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

export function loadSiliqueRecords() {
  try {
    return JSON.parse(localStorage.getItem(SILIQUE_KEY) || '[]')
  } catch {
    return []
  }
}

export function saveSiliqueRecords(records) {
  localStorage.setItem(SILIQUE_KEY, JSON.stringify(records))
}
