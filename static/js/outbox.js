// ------------------------------------------------------------------
// ClinicManager offline outbox
// ------------------------------------------------------------------
const CM_DB_NAME = 'cm_outbox_v1';
const CM_DB_VERSION = 6;
const STORE_QUEUE_REG = 'queue_registrations';
const STORE_QUEUE_SNAPSHOT = 'queue_snapshot';
const STORE_PRICE_LIST_SNAPSHOT = 'price_list_snapshot';
const STORE_INVENTORY_SNAPSHOT = 'inventory_snapshot';
const STORE_DASHBOARD_SNAPSHOT = 'dashboard_snapshot';
const STORE_ROLE_SNAPSHOT = 'role_snapshot';

function cmOpenDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(CM_DB_NAME, CM_DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_QUEUE_REG)) {
        db.createObjectStore(STORE_QUEUE_REG, { keyPath: 'client_uuid' });
      }
      if (!db.objectStoreNames.contains(STORE_QUEUE_SNAPSHOT)) {
        db.createObjectStore(STORE_QUEUE_SNAPSHOT, { keyPath: 'key' });
      }
      if (!db.objectStoreNames.contains(STORE_PRICE_LIST_SNAPSHOT)) {
        db.createObjectStore(STORE_PRICE_LIST_SNAPSHOT, { keyPath: 'key' });
      }
      if (!db.objectStoreNames.contains(STORE_INVENTORY_SNAPSHOT)) {
        db.createObjectStore(STORE_INVENTORY_SNAPSHOT, { keyPath: 'key' });
      }
      if (!db.objectStoreNames.contains(STORE_DASHBOARD_SNAPSHOT)) {
        db.createObjectStore(STORE_DASHBOARD_SNAPSHOT, { keyPath: 'key' });
      }
      if (!db.objectStoreNames.contains(STORE_ROLE_SNAPSHOT)) {
        db.createObjectStore(STORE_ROLE_SNAPSHOT, { keyPath: 'key' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function cmSaveRegistration(record) {
  return cmOpenDB().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_QUEUE_REG, 'readwrite');
    tx.objectStore(STORE_QUEUE_REG).put(record);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  }));
}

function cmGetAllRegistrations() {
  return cmOpenDB().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_QUEUE_REG, 'readonly');
    const req = tx.objectStore(STORE_QUEUE_REG).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }));
}

function cmSaveQueueSnapshot(data) {
  return cmOpenDB().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_QUEUE_SNAPSHOT, 'readwrite');
    tx.objectStore(STORE_QUEUE_SNAPSHOT).put({ key: 'current', ...data });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  }));
}

function cmGetQueueSnapshot() {
  return cmOpenDB().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_QUEUE_SNAPSHOT, 'readonly');
    const req = tx.objectStore(STORE_QUEUE_SNAPSHOT).get('current');
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  }));
}

function cmSavePriceListSnapshot(data) {
  return cmOpenDB().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_PRICE_LIST_SNAPSHOT, 'readwrite');
    tx.objectStore(STORE_PRICE_LIST_SNAPSHOT).put({ key: 'current', ...data });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  }));
}

function cmGetPriceListSnapshot() {
  return cmOpenDB().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_PRICE_LIST_SNAPSHOT, 'readonly');
    const req = tx.objectStore(STORE_PRICE_LIST_SNAPSHOT).get('current');
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  }));
}

function cmSaveInventorySnapshot(data) {
    return cmOpenDB().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_INVENTORY_SNAPSHOT, 'readwrite');
        tx.objectStore(STORE_INVENTORY_SNAPSHOT).put({ key: 'current', ...data });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    }));
}

function cmGetInventorySnapshot() {
    return cmOpenDB().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_INVENTORY_SNAPSHOT, 'readonly');
        const req = tx.objectStore(STORE_INVENTORY_SNAPSHOT).get('current');
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
    }));
}

function cmSaveDashboardSnapshot(data) {
    return cmOpenDB().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_DASHBOARD_SNAPSHOT, 'readwrite');
        tx.objectStore(STORE_DASHBOARD_SNAPSHOT).put({ key: 'current', ...data });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    }));
}

function cmGetDashboardSnapshot() {
    return cmOpenDB().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_DASHBOARD_SNAPSHOT, 'readonly');
        const req = tx.objectStore(STORE_DASHBOARD_SNAPSHOT).get('current');
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
    }));
}

function cmSaveRoleSnapshot(data) {
    return cmOpenDB().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_ROLE_SNAPSHOT, 'readwrite');
        tx.objectStore(STORE_ROLE_SNAPSHOT).put({ key: 'current', ...data });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    }));
}

function cmGetRoleSnapshot() {
    return cmOpenDB().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_ROLE_SNAPSHOT, 'readonly');
        const req = tx.objectStore(STORE_ROLE_SNAPSHOT).get('current');
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
    }));
}

function cmUuid() {
  if (self.crypto && self.crypto.randomUUID) return self.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Fixed to work uniformly across window and headless background contexts
function cmEscapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

async function cmSyncOneRegistration(record) {
  try {
    const res = await fetch('/api/queue/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(record.payload)
    });
    if (!res.ok) return false;
    const data = await res.json();
    
    // Explicitly mutate wrapper container records cleanly
    record.synced = true;
    record.server_patient_id = data.patient_id;
    record.queue_status = data.queue_status;
    await cmSaveRegistration(record);
    return true;
  } catch (err) {
    return false;
  }
}

async function cmSyncAllRegistrations() {
  const all = await cmGetAllRegistrations();
  const pending = all.filter((r) => !r.synced);
  for (const record of pending) {
    await cmSyncOneRegistration(record);
  }
  return cmGetAllRegistrations();
}

async function cmQueueRegistration(payload) {
  const clientUuid = cmUuid();
  const record = {
    client_uuid: clientUuid,
    payload: { ...payload, client_uuid: clientUuid },
    created_at: new Date().toISOString(),
    synced: false
  };
  await cmSaveRegistration(record);

  const success = await cmSyncOneRegistration(record);

  // Safe Environment Execution Verification Strategy
  const isBrowserWindow = typeof window !== 'undefined' && typeof navigator !== 'undefined';
  
  if (!success && isBrowserWindow && 'serviceWorker' in navigator) {
    try {
      const reg = await navigator.serviceWorker.ready;
      if (reg.sync) {
        await reg.sync.register('sync-queue-registrations');
      }
    } catch (err) {
      // Periodic background or continuous synchronization unavailable
    }
  }

  return { record, success };
}