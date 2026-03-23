// static/js/viewStore.js
// Single-live-view-per-room store with optional Firestore sync.
// If Firebase isn’t connected, everything stays local (per-room in localStorage).

const LS_KEY = “bhq-rooms-v1”;
const BACKUP_KEY = “bhq-backup-v1”;
const BACKUP_INTERVAL_MS = 30_000; // auto-backup every 30 seconds

let memory = { rooms: {} }; // { CODE: { live: { cards, ts } } }
let currentRoom = null;
let _backupTimer = null;

// Firebase (lazy)
let fb = null;
let unsub = null;

// Track whether this client just wrote to Firestore,
// so the echo snapshot can be skipped safely.
let _lastWriteTs = 0;

function loadLS() {
try { memory = JSON.parse(localStorage.getItem(LS_KEY)) || memory; } catch {}
}
function saveLS() {
try {
localStorage.setItem(LS_KEY, JSON.stringify(memory));
} catch (e) {
console.error(“localStorage write failed:”, e);
}
}

// –– Rolling backup (independent of rooms/Firestore) ––
function saveBackup() {
try {
const code = currentRoom || “**local**”;
const live = memory.rooms[code]?.live;
if (!live || !live.cards || live.cards.length === 0) return;
const payload = {
room: code,
cards: live.cards,
ts: live.ts || Date.now(),
savedAt: Date.now(),
};
localStorage.setItem(BACKUP_KEY, JSON.stringify(payload));
} catch (e) {
console.error(“Backup write failed:”, e);
}
}

export function loadBackup() {
try {
const raw = localStorage.getItem(BACKUP_KEY);
if (!raw) return null;
return JSON.parse(raw);
} catch { return null; }
}

function startBackupTimer() {
if (_backupTimer) clearInterval(_backupTimer);
_backupTimer = setInterval(saveBackup, BACKUP_INTERVAL_MS);
}

// –– Firebase ––

export async function connectFirebase(config) {
if (fb) return fb;

const [
{ initializeApp },
{ getFirestore, doc, setDoc, getDoc, onSnapshot, collection, getDocs, serverTimestamp, deleteDoc },
{ getAuth, signInAnonymously },
{ initializeAppCheck, ReCaptchaEnterpriseProvider }
] = await Promise.all([
import(“https://www.gstatic.com/firebasejs/10.12.4/firebase-app.js”),
import(“https://www.gstatic.com/firebasejs/10.12.4/firebase-firestore.js”),
import(“https://www.gstatic.com/firebasejs/10.12.4/firebase-auth.js”),
import(“https://www.gstatic.com/firebasejs/10.12.4/firebase-app-check.js”),
]);

const app = initializeApp(config);
const db = getFirestore(app);

// silent login — no UI
const auth = getAuth(app);
await signInAnonymously(auth).catch(console.error);

fb = { app, db, auth, firestore: { doc, setDoc, getDoc, onSnapshot, collection, getDocs, serverTimestamp, deleteDoc } };

return fb;
}

export function currentRoomCode() { return currentRoom; }

// Join or create a room. Sets a live listener on that room’s “__live” doc.
export async function joinRoom(code, onChange) {
loadLS();
currentRoom = code?.trim() || null;

if (unsub) { try { unsub(); } catch {} unsub = null; }

if (!currentRoom) {
// local-only mode (no remote listener)
if (!memory.rooms[”**local**”]) memory.rooms[”**local**”] = { live: { cards: [], ts: Date.now() } };
saveLS();
startBackupTimer();
if (onChange) onChange({ roomCode: null });
return;
}

// Ensure local room bucket
if (!memory.rooms[currentRoom]) memory.rooms[currentRoom] = { live: { cards: [], ts: 0 } };

if (!fb) {
saveLS();
startBackupTimer();
if (onChange) onChange({ roomCode: currentRoom });
return;
}

const { db, firestore } = fb;
const roomDoc = firestore.doc(db, “rooms”, currentRoom);
const liveDoc = firestore.doc(db, “rooms”, currentRoom, “views”, “__live”);

// Create/update the room index doc so it shows up in room listing
try { await firestore.setDoc(roomDoc, { updated: firestore.serverTimestamp() }, { merge: true }); } catch {}

// Prime from server
try {
const snap = await firestore.getDoc(liveDoc);
if (snap.exists()) {
const data = snap.data();
const remoteTs = data.ts || 0;
const localTs = memory.rooms[currentRoom]?.live?.ts || 0;
// On initial join, accept whichever is newer
if (remoteTs >= localTs) {
memory.rooms[currentRoom].live = { cards: data.cards || [], ts: remoteTs };
}
// else: keep local — it’s newer
saveLS();
}
} catch (e) {
console.warn(“Initial live load failed, using local cache:”, e);
}

// Live updates — with timestamp conflict resolution
unsub = firestore.onSnapshot(liveDoc, (dsnap) => {
if (dsnap.exists()) {
const data = dsnap.data();
const remoteTs = data.ts || 0;
const localTs = memory.rooms[currentRoom]?.live?.ts || 0;

```
  // Skip if this is our own echo (written < 2s ago with same ts)
  if (_lastWriteTs && Math.abs(remoteTs - _lastWriteTs) < 2000) {
    return;
  }

  // Only accept remote if it's genuinely newer
  if (remoteTs > localTs) {
    memory.rooms[currentRoom].live = { cards: data.cards || [], ts: remoteTs };
    saveLS();
    if (onChange) onChange({ roomCode: currentRoom });
  }
}
```

}, (err) => console.error(“live listener error:”, err));

startBackupTimer();
if (onChange) onChange({ roomCode: currentRoom });
}

// Save current cards to the room’s live doc (and local cache)
export async function saveLive(cards) {
loadLS();
const ts = Date.now();
const code = currentRoom || “**local**”;
memory.rooms[code] = memory.rooms[code] || { live: { cards: [], ts: 0 } };
memory.rooms[code].live = { cards, ts };
saveLS();
saveBackup(); // immediate backup on every save

if (fb && currentRoom) {
const { db, firestore } = fb;
const liveDoc = firestore.doc(db, “rooms”, currentRoom, “views”, “__live”);
const roomDoc = firestore.doc(db, “rooms”, currentRoom);
_lastWriteTs = ts; // mark so our own echo is skipped
try {
await firestore.setDoc(liveDoc, { cards, ts, _updated: firestore.serverTimestamp() }, { merge: true });
await firestore.setDoc(roomDoc, { updated: firestore.serverTimestamp() }, { merge: true });
} catch (e) {
console.error(“saveLive Firestore error (saved locally):”, e);
}
}
}

export async function deleteRoom(code) {
loadLS();
const name = (code || currentRoom || “”).trim();
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
const liveDoc = firestore.doc(db, “rooms”, name, “views”, “__live”);
const roomDoc = firestore.doc(db, “rooms”, name);
try {
await firestore.deleteDoc(liveDoc).catch(()=>{});
await firestore.deleteDoc(roomDoc).catch(()=>{});
} catch (e) {
console.error(“deleteRoom Firestore error:”, e);
throw e;
}
}
}

export function loadLive() {
loadLS();
const code = currentRoom || “**local**”;
return memory.rooms[code]?.live?.cards || [];
}

// List available rooms (from Firestore if connected; otherwise from local cache)
export async function listRooms() {
loadLS();
const localRooms = Object.keys(memory.rooms).filter(r => r !== “**local**”);
if (!fb) return localRooms;

try {
const { db, firestore } = fb;
const coll = firestore.collection(db, “rooms”);
const qs = await firestore.getDocs(coll);
const ids = [];
qs.forEach(d => ids.push(d.id));
const merged = Array.from(new Set([…ids, …localRooms])).sort();
return merged;
} catch (e) {
console.warn(“listRooms failed, falling back to local:”, e);
return localRooms;
}
}
