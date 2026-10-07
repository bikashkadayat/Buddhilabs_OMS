/**
 * Local snapshot store (Phase 111.6) — the copy that survives a crash.
 *
 * IndexedDB rather than localStorage. A memo with a pasted table runs to
 * hundreds of kilobytes and localStorage is a synchronous ~5MB bucket shared
 * with everything else on the origin: a large document either blocks the main
 * thread on every save or hits QuotaExceededError partway through, which is the
 * one moment the feature must not fail. IndexedDB is async and roomy.
 *
 * Written with the raw API rather than a wrapper library, because the whole
 * surface used here is four operations and adding a dependency to a production
 * bundle for that is a poor trade.
 *
 * SCOPING AND CONFIDENTIALITY (blocker B4)
 * Every record is keyed by user id and wiped on logout, so a shared machine
 * does not leave one person's draft readable by the next. Memos typed as
 * CONFIDENTIAL are never written here at all — the system serves their
 * attachments through short-lived signed URLs specifically to avoid durable
 * local copies, and a plaintext body in IndexedDB would undo that. Those
 * documents still autosave to the server, which is authenticated storage.
 */

const DB_NAME = 'nifn-drafts';
const DB_VERSION = 1;
const STORE = 'snapshots';

let dbPromise = null;

/** Whether this browser can store anything at all. Private modes may refuse. */
export const isSupported = () => typeof indexedDB !== 'undefined';

const openDb = () => {
  if (!isSupported()) return Promise.reject(new Error('IndexedDB unavailable'));
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'key' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    // A blocked upgrade would hang the promise forever and with it every save.
    request.onblocked = () => reject(new Error('IndexedDB blocked'));
  });
  // Never cache a rejection: a transient failure must not disable local
  // recovery for the rest of the session.
  dbPromise.catch(() => { dbPromise = null; });
  return dbPromise;
};

const run = (mode, fn) => openDb().then((db) => new Promise((resolve, reject) => {
  const tx = db.transaction(STORE, mode);
  const request = fn(tx.objectStore(STORE));
  tx.onabort = () => reject(tx.error);
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
}));

/** Composite key. Includes the user so one machine can serve several people. */
export const snapshotKey = (userId, kind, documentKey) => `${userId}:${kind}:${documentKey}`;

/**
 * Write the local copy.
 *
 * Every call is wrapped: local persistence is a safety net, and a safety net
 * that throws into the editor is worse than one that quietly misses. A failure
 * here leaves the server save — the caller reflects that as "Local only"
 * or "Offline" rather than claiming success.
 */
export const putSnapshot = async ({ userId, kind, documentKey, payload, version }) => {
  try {
    await run('readwrite', (store) => store.put({
      key: snapshotKey(userId, kind, documentKey),
      userId: String(userId), kind, documentKey, payload,
      version: version ?? 0,
      savedAt: new Date().toISOString(),
    }));
    return true;
  } catch {
    return false;
  }
};

export const getSnapshot = async ({ userId, kind, documentKey }) => {
  try {
    return (await run('readonly',
      (store) => store.get(snapshotKey(userId, kind, documentKey)))) || null;
  } catch {
    return null;
  }
};

export const deleteSnapshot = async ({ userId, kind, documentKey }) => {
  try {
    await run('readwrite',
      (store) => store.delete(snapshotKey(userId, kind, documentKey)));
    return true;
  } catch {
    return false;
  }
};

/**
 * Drop every snapshot belonging to one user. Called on logout and on session
 * expiry so a draft cannot outlive the session that created it.
 */
export const clearUser = async (userId) => {
  try {
    const all = await run('readonly', (store) => store.getAll());
    const mine = (all || []).filter((row) => row.userId === String(userId));
    await Promise.all(mine.map((row) => run('readwrite',
      (store) => store.delete(row.key))));
    return mine.length;
  } catch {
    return 0;
  }
};

/** Every local snapshot for a user, newest first — the offline recovery list. */
export const listUser = async (userId) => {
  try {
    const all = await run('readonly', (store) => store.getAll());
    return (all || [])
      .filter((row) => row.userId === String(userId))
      .sort((a, b) => String(b.savedAt).localeCompare(String(a.savedAt)));
  } catch {
    return [];
  }
};
