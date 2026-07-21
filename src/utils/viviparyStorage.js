const KEY = 'rapeseed-pheno-tool:vivipary-records'
const DB_NAME = 'rapeseed-vivipary'
const STORE = 'records'

export function loadViviparyRecords() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]')
  } catch {
    return []
  }
}

export async function loadViviparyRecordsFull() {
  try {
    const db = await openDb()
    const records = await requestResult(db.transaction(STORE, 'readonly').objectStore(STORE).getAll())
    db.close()
    return records.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
  } catch {
    return loadViviparyRecords()
  }
}

export async function saveViviparyRecords(records) {
  localStorage.setItem(KEY, JSON.stringify(records.map(({ imageDataUrl, ...record }) => ({ ...record, hasLocalImage: Boolean(imageDataUrl) }))))
  try {
    const db = await openDb()
    const tx = db.transaction(STORE, 'readwrite')
    const store = tx.objectStore(STORE)
    store.clear()
    records.forEach((record) => store.put(record))
    await transactionDone(tx)
    db.close()
  } catch {
    // The lightweight localStorage copy remains available if IndexedDB is blocked.
  }
}

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE, { keyPath: 'id' })
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result || [])
    request.onerror = () => reject(request.error)
  })
}

function transactionDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = resolve
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}
