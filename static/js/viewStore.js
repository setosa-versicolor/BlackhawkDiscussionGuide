// viewStore.js
// Minimal pluggable store with localStorage fallback and optional Firebase sync.

const LS_KEY = "bhq-views-v2";
let memory = { rooms: {}, localViews: {} }; // { rooms: {CODE: {views:{name:{cards:[]}}}}, localViews: {name:{cards:[]}} }
let firebaseApi = null;

function load() {
  try {
    memory = JSON.parse(localStorage.getItem(LS_KEY)) || memory;
  } catch {}
}
function save() {
  localStorage.setItem(LS_KEY, JSON.stringify(memory));
}

// Optional Firebase hook (call initFirebase(config) once if you want cloud sync)
export function initFirebase(firebaseImpl) {
  firebaseApi = firebaseImpl; // { saveRoom(code, data), loadRoom(code, onChange) }
}

export async function joinRoom(code, onChange) {
  load();
  if (!code) return;
  if (firebaseApi) {
    // live updates
    firebaseApi.loadRoom(code, (data) => {
      memory.rooms[code] = data || { views: {} };
      save();
      onChange && onChange(listViews(code));
    });
  } else {
    // no backend; just create local “room” bucket
    memory.rooms[code] ||= { views: {} };
    save();
    onChange && onChange(listViews(code));
  }
}

export function saveView({ name, cards, roomCode }) {
  load();
  if (roomCode) {
    memory.rooms[roomCode] ||= { views: {} };
    memory.rooms[roomCode].views[name] = { cards, ts: Date.now() };
  } else {
    memory.localViews[name] = { cards, ts: Date.now() };
  }
  save();
  if (firebaseApi && roomCode) {
    firebaseApi.saveRoom(roomCode, memory.rooms[roomCode]);
  }
}

export function loadView({ name, roomCode }) {
  load();
  const bucket = roomCode ? memory.rooms[roomCode]?.views : memory.localViews;
  return bucket?.[name]?.cards || null;
}

export function listViews(roomCode) {
  load();
  const bucket = roomCode ? memory.rooms[roomCode]?.views : memory.localViews;
  return Object.entries(bucket || {})
    .sort((a,b)=> (b[1].ts||0)-(a[1].ts||0))
    .map(([name]) => name);
}
