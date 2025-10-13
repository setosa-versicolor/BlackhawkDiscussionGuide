// static/js/viewStore.js
// Single-live-view-per-room store with optional Firestore sync.
// If Firebase isn't connected, everything stays local (per-room in localStorage).

const LS_KEY = "bhq-rooms-v1";

let memory = { rooms: {} }; // { CODE: { live: { cards, ts } } }
let currentRoom = null;

// Firebase (lazy)
let fb = null;
let unsub = null;

function loadLS() {
  try { memory = JSON.parse(localStorage.getItem(LS_KEY)) || memory; } catch {}
}
function saveLS() {
  localStorage.setItem(LS_KEY, JSON.stringify(memory));
}

export async function connectFirebase(config) {
  if (fb) return fb;
  const [{ initializeApp }, { getFirestore, doc, setDoc, getDoc, onSnapshot, collection, getDocs, serverTimestamp, deleteDoc }] =
    await Promise.all([
      import("https://www.gstatic.com/firebasejs/10.12.4/firebase-app.js"),
      import("https://www.gstatic.com/firebasejs/10.12.4/firebase-firestore.js")
    ]);
  const app = initializeApp(config);
  const db = getFirestore(app);
  fb = { app, db, firestore: { doc, setDoc, getDoc, onSnapshot, collection, getDocs, serverTimestamp, deleteDoc } };
  return fb;
}

export function currentRoomCode() { return currentRoom; }

// Join or create a room. Sets a live listener on that room's "__live" doc.
export async function joinRoom(code, onChange) {
  loadLS();
  currentRoom = code?.trim() || null;

  if (unsub) { try { unsub(); } catch {} unsub = null; }

  if (!currentRoom) {
    // local-only mode (no remote listener)
    if (!memory.rooms["__local__"]) memory.rooms["__local__"] = { live: { cards: [], ts: Date.now() } };
    saveLS();
    if (onChange) onChange({ roomCode: null });
    return;
  }

  // Ensure local room bucket
  if (!memory.rooms[currentRoom]) memory.rooms[currentRoom] = { live: { cards: [], ts: 0 } };

  if (!fb) {
    saveLS();
    if (onChange) onChange({ roomCode: currentRoom });
    return;
  }

  const { db, firestore } = fb;
  const roomDoc = firestore.doc(db, "rooms", currentRoom);
  const liveDoc = firestore.doc(db, "rooms", currentRoom, "views", "__live");

  // Create/update the room index doc so it shows up in room listing
  try { await firestore.setDoc(roomDoc, { updated: firestore.serverTimestamp() }, { merge: true }); } catch {}

  // Prime from server
  try {
    const snap = await firestore.getDoc(liveDoc);
    if (snap.exists()) {
      const data = snap.data();
      memory.rooms[currentRoom].live = { cards: data.cards || [], ts: data.ts || Date.now() };
      saveLS();
    }
  } catch (e) {
    console.warn("Initial live load failed:", e);
  }

  // Live updates
  unsub = firestore.onSnapshot(liveDoc, (dsnap) => {
    if (dsnap.exists()) {
      const data = dsnap.data();
      memory.rooms[currentRoom].live = { cards: data.cards || [], ts: data.ts || Date.now() };
      saveLS();
      if (onChange) onChange({ roomCode: currentRoom });
    }
  }, (err) => console.error("live listener error:", err));

  if (onChange) onChange({ roomCode: currentRoom });
}

// Save current cards to the room's live doc (and local cache)
export async function saveLive(cards) {
  loadLS();
  const ts = Date.now();
  const code = currentRoom || "__local__";
  memory.rooms[code] = memory.rooms[code] || { live: { cards: [], ts: 0 } };
  memory.rooms[code].live = { cards, ts };
  saveLS();

  if (fb && currentRoom) {
    const { db, firestore } = fb;
    const liveDoc = firestore.doc(db, "rooms", currentRoom, "views", "__live");
    const roomDoc = firestore.doc(db, "rooms", currentRoom);
    try {
      await firestore.setDoc(liveDoc, { cards, ts, _updated: firestore.serverTimestamp() }, { merge: true });
      await firestore.setDoc(roomDoc, { updated: firestore.serverTimestamp() }, { merge: true });
    } catch (e) {
      console.error("saveLive Firestore error:", e);
    }
  }
}

export async function deleteRoom(code) {
  loadLS();
  const name = (code || currentRoom || "").trim();
  if (!name) return;

  // Remove local cache
  if (memory.rooms[name]) {
    delete memory.rooms[name];
    saveLS();
  }
  if (currentRoom === name) currentRoom = null;

  // Remove remote docs if connected
  if (fb) {
    const { db, firestore } = fb;
    const liveDoc = firestore.doc(db, "rooms", name, "views", "__live");
    const roomDoc = firestore.doc(db, "rooms", name);
    try {
      await firestore.deleteDoc(liveDoc).catch(()=>{});
      await firestore.deleteDoc(roomDoc).catch(()=>{});
    } catch (e) {
      console.error("deleteRoom Firestore error:", e);
      throw e;
    }
  }
}


export function loadLive() {
  loadLS();
  const code = currentRoom || "__local__";
  return memory.rooms[code]?.live?.cards || [];
}

// List available rooms (from Firestore if connected; otherwise from local cache)
export async function listRooms() {
  loadLS();
  const localRooms = Object.keys(memory.rooms).filter(r => r !== "__local__");
  if (!fb) return localRooms;

  try {
    const { db, firestore } = fb;
    const coll = firestore.collection(db, "rooms");
    const qs = await firestore.getDocs(coll);
    const ids = [];
    qs.forEach(d => ids.append ? ids.append(d.id) : ids.push(d.id));
    const merged = Array.from(new Set([...ids, ...localRooms])).sort();
    return merged;
  } catch (e) {
    console.warn("listRooms failed, falling back to local:", e);
    return localRooms;
  }
}
