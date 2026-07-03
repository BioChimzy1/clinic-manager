// ------------------------------------------------------------------
// ClinicManager offline outbox
// ------------------------------------------------------------------
// Loaded two ways:
//   1. As a normal <script> in pages like register.html / queue.html
//   2. Via importScripts() inside service-worker.js
// Both contexts expose IndexedDB and `self`, so this file avoids any
// `window`-only APIs and works identically in either place.
//
// Currently handles one outbox: patient queue registrations.
// To add offline support for payments or retail later, add a new
// object store + a matching cmQueue___ / cmSyncOne___ pair, following
// the same pattern as below.
// ------------------------------------------------------------------

const CM_DB_NAME = 'cm_outbox_v1';
const CM_DB_VERSION = 5;                              // was 4
const STORE_QUEUE_REG = 'queue_registrations';
const STORE_QUEUE_SNAPSHOT = 'queue_snapshot';
const STORE_PRICE_LIST_SNAPSHOT = 'price_list_snapshot';
const STORE_INVENTORY_SNAPSHOT = 'inventory_snapshot';
const STORE_DASHBOARD_SNAPSHOT = 'dashboard_snapshot';   // NEW

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
      if (!db.objectStoreNames.contains(STORE_DASHBOARD_SNAPSHOT)) {   // NEW
        db.createObjectStore(STORE_DASHBOARD_SNAPSHOT, { keyPath: 'key' });
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

// Caches the last successful /api/queue response, keyed under a fixed
// 'current' key since there's only ever one "current queue" per device.
// Called every time the queue page successfully loads live data, so the
// snapshot is always as fresh as the last time this device had a signal.
function cmSaveQueueSnapshot(data) {
  return cmOpenDB().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_QUEUE_SNAPSHOT, 'readwrite');
    tx.objectStore(STORE_QUEUE_SNAPSHOT).put({ key: 'current', ...data });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  }));
}

// Returns the last cached queue snapshot, or null if none exists yet
// (e.g. very first load ever happened offline).
function cmGetQueueSnapshot() {
  return cmOpenDB().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_QUEUE_SNAPSHOT, 'readonly');
    const req = tx.objectStore(STORE_QUEUE_SNAPSHOT).get('current');
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  }));
}

// Same pattern as cmSaveQueueSnapshot/cmGetQueueSnapshot, for the
// price list + live stock status shown on /price_list.
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

// Generates a UUID even on older WebViews lacking crypto.randomUUID.
// Uses `self.crypto` (not `window.crypto`) so this works in the
// service worker's global scope too.
function cmUuid() {
  if (self.crypto && self.crypto.randomUUID) return self.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Escapes text before it's ever placed into innerHTML, so a patient
// name typed with stray angle brackets can't break the queue table.
function cmEscapeHtml(str) {
  const div = (typeof document !== 'undefined') ? document.createElement('div') : null;
  if (!div) return String(str); // service worker context never renders HTML
  div.textContent = str;
  return div.innerHTML;
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

// Sends one registration to the server. Returns true only if the
// server confirmed receipt (either freshly processed, or recognized
// as already processed via client_uuid) — both count as success here,
// since idempotency is enforced server-side, not by the client.
async function cmSyncOneRegistration(record) {
  try {
    const res = await fetch('/api/queue/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(record.payload)
    });
    if (!res.ok) return false;
    const data = await res.json();
    record.synced = true;
    record.server_patient_id = data.patient_id;
    record.queue_status = data.queue_status;
    await cmSaveRegistration(record);
    return true;
  } catch (err) {
    // Offline or server unreachable — leave pending, no error surfaced.
    return false;
  }
}

// Retries every unsynced record. Safe to call repeatedly (page load,
// 'online' event, background sync, a timer) — already-synced records
// are skipped, and re-sending a record that actually succeeded last
// time but wasn't marked synced locally (e.g. app closed mid-request)
// is harmless: the server recognizes the client_uuid and reports
// "already_processed" instead of creating a duplicate patient.
async function cmSyncAllRegistrations() {
  const all = await cmGetAllRegistrations();
  const pending = all.filter((r) => !r.synced);
  for (const record of pending) {
    await cmSyncOneRegistration(record);
  }
  return cmGetAllRegistrations();
}

// Call from register.html's submit handler. Saves locally first (so
// nothing is lost even if the tab closes a moment later), attempts an
// immediate sync, then — if that failed — asks the service worker to
// retry later via Background Sync.
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

  if (!success && 'serviceWorker' in navigator && 'SyncManager' in self) {
    try {
      const reg = await navigator.serviceWorker.ready;
      await reg.sync.register('sync-queue-registrations');
    } catch (err) {
      // Background Sync unsupported (e.g. iOS Safari) — fine, queue.html
      // also retries on page load and on the 'online' event.
    }
  }

  return { record, success };
}