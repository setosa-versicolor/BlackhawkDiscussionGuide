// static/js/viewStore.js
// LocalStorage store with optional Firestore sync by room code.
// Works offline; when connected to Firebase + joined to a room,
// saves/loads also sync to Firestore and live-update across devices.

const LS_KEY = "bhq-views-v2";

// in-memory cache
let memory = { rooms: {}, localViews: {} }; // rooms: { CODE: { views: {name: {cards, ts}} } }
let currentRoom = null;

// Firebase bits (lazy-loaded)
let fb = null; // { app, db, firestore: { getFirestore, doc, setDoc, onSnapshot, collection, getDocs, serverTimestamp } }
let unsubscribe = null;

// ---------- Local storage ----------
function loadLS() {
  try { memory = JSON.parse(localStorage.getItem(LS_KEY)) || memory; } catch {}
}
function saveLS() {
  localStorage.setItem(LS_KEY, JSON.stringify(memory));
}

// ---------- Public API ----------

// Call this once (optionally). If never called, everything stays local-only.
export async function connectFirebase(config) {
  if (fb) return fb;
  const [{ initializeApp }, { getFirestore, doc, setDoc, onSnapshot, collection, getDocs, serverTimestamp }] =
    await Promise.all([
      import("https://www.gstatic.com/firebasejs/10.12.4/firebase-app.js"),
      import("https://www.gstatic.com/firebasejs/10.12.4/firebase-firestore.js")
    ]);
  const app = initializeApp(config);
  const db = getFirestore(app);
  fb = { app, db, firestore: { getFirestore, doc, setDoc, onSnapshot, collection, getDocs, serverTimestamp } };
  return fb;
}

/**
 * Join a room. If Firebase is connected, attaches a live listener that keeps
 * memory.rooms[code].views in sync. Otherwise, falls back to local-only bucket.
 */
export async function joinRoom(code, onChange) {
  currentRoom = code?.trim() || null;
  loadLS();

  // Clean up old listener
  if (unsubscribe) { try { unsubscribe(); } catch {} unsubscribe = null; }

  if (!fb || !currentRoom) {
    // local-only
    if (!memory.rooms[currentRoom]) memory.rooms[currentRoom] = { views: {} };
    saveLS();
    if (onChange) onChange({ roomCode: currentRoom });
    return;
  }

  // Ensure room exists locally
  if (!memory.rooms[currentRoom]) memory.rooms[currentRoom] = { views: {} };

  const { db, firestore } = fb;
  const roomColl = firestore.collection(db, "rooms", currentRoom, "views");

  // Prime local from server (one-time)
  try {
    const snap = await firestore.getDocs(roomColl);
    const serverViews = {};
    snap.forEach(d => { serverViews[d.id] = d.data(); });
    memory.rooms[currentRoom].views = { ...serverViews };
    saveLS();
  } catch (e) {
    console.warn("Initial fetch failed (still fine for live updates):", e);
  }

  // Live updates
  unsubscribe = firestore.onSnapshot(roomColl, (qs) => {
    const incoming = {};
    qs.forEach(d => { incoming[d.id] = d.data(); });
    memory.rooms[currentRoom].views = incoming;
    saveLS();
    if (onChange) onChange({ roomCode: currentRoom });
  }, (err) => console.error("room listener error:", err));
}

/**
 * Save a view (cards array) under a name. If roomCode is provided (or you’ve joined),
 * it syncs to Firestore; otherwise it stays local-only.
 */
export async function saveView({ name, cards, roomCode }) {
  loadLS();
  const ts = Date.now();
  const payload = { cards, ts };

  const code = roomCode || currentRoom;
  if (code) {
    // room bucket
    memory.rooms[code] = memory.rooms[code] || { views: {} };
    memory.rooms[code].views[name] = payload;
    saveLS();

    if (fb) {
      const { db, firestore } = fb;
      const docRef = firestore.doc(db, "rooms", code, "views", name);
      try {
        await firestore.setDoc(docRef, { ...payload, _updated: firestore.serverTimestamp() }, { merge: true });
      } catch (e) {
        console.error("Failed to write to Firestore (local copy still saved):", e);
      }
    }
    return;
  }

  // local-only
  memory.localViews[name] = payload;
  saveLS();
}

export function loadView({ name, roomCode }) {
  loadLS();
  const bucket = roomCode ? memory.rooms[roomCode]?.views : memory.localViews;
  return bucket?.[name]?.cards || null;
}

export function listViews(roomCode) {
  loadLS();
  const bucket = roomCode ? memory.rooms[roomCode]?.views : memory.localViews;
  return Object.entries(bucket || {})
    .sort((a, b) => (b[1].ts || 0) - (a[1].ts || 0))
    .map(([name]) => name);
}

// convenience for reading current room in UI
export function currentRoomCode() { return currentRoom; }
