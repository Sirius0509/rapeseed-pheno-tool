const KEY = 'rapeseed-pheno-tool:samples'
const SILIQUE_KEY = 'rapeseed-pheno-tool:siliques'
const DB_NAME = 'rapeseed-pheno-tool'
const DB_VERSION = 1
const SILIQUE_STORE = 'silique-records'

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
  try {
    localStorage.setItem(SILIQUE_KEY, JSON.stringify(stripHeavyImages(records)))
  } catch {
    localStorage.setItem(SILIQUE_KEY, JSON.stringify(stripHeavyImages(records).map(stripTrainingImages)))
  }
  return saveSiliqueRecordsIndexed(records)
}

export async function loadSiliqueRecordsFull() {
  const indexed = await loadSiliqueRecordsIndexed()
  if (indexed.length) return indexed
  return loadSiliqueRecords()
}

function stripHeavyImages(records) {
  return records.map((record) => ({
    ...record,
    imageDataUrl: '',
    seedImageDataUrl: '',
    siliqueImageDataUrl: '',
    localImageOnly: Boolean(record.imageDataUrl || record.seedImageDataUrl || record.siliqueImageDataUrl),
  }))
}

function stripTrainingImages(record) {
  return {
    ...record,
    seedPoints: [],
    autoSeedPoints: [],
    deletedSeedPoints: [],
  }
}

function openDb() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error('IndexedDB is not available'))
      return
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(SILIQUE_STORE)) db.createObjectStore(SILIQUE_STORE, { keyPath: 'id' })
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function transaction(storeMode, callback) {
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction(SILIQUE_STORE, storeMode)
        const store = tx.objectStore(SILIQUE_STORE)
        const result = callback(store)
        tx.oncomplete = () => {
          db.close()
          resolve(result)
        }
        tx.onerror = () => {
          db.close()
          reject(tx.error)
        }
        tx.onabort = () => {
          db.close()
          reject(tx.error)
        }
      }),
  )
}

async function loadSiliqueRecordsIndexed() {
  try {
    const db = await openDb()
    const records = await new Promise((resolve, reject) => {
      const tx = db.transaction(SILIQUE_STORE, 'readonly')
      const request = tx.objectStore(SILIQUE_STORE).getAll()
      request.onsuccess = () => resolve(request.result || [])
      request.onerror = () => reject(request.error)
      tx.oncomplete = () => db.close()
      tx.onerror = () => {
        db.close()
        reject(tx.error)
      }
    })
    return records.sort((a, b) => String(b.createdAt || b.measuredAt || '').localeCompare(String(a.createdAt || a.measuredAt || '')))
  } catch {
    return []
  }
}

async function saveSiliqueRecordsIndexed(records) {
  try {
    await transaction('readwrite', (store) => {
      store.clear()
      records.forEach((record) => store.put(record))
    })
    return true
  } catch {
    return false
  }
}

export async function upsertSiliqueRecordIndexed(record) {
  try {
    await transaction('readwrite', (store) => store.put(record))
    return true
  } catch {
    return false
  }
}

export async function deleteSiliqueRecordIndexed(recordId) {
  try {
    await transaction('readwrite', (store) => store.delete(recordId))
    return true
  } catch {
    return false
  }
}
