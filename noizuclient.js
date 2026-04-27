// ----------------------------
// CONFIG
// ----------------------------
const DB_NAME = "noizu_cache";
const STORE_CHUNKS = "chunks";
const STORE_META = "meta";

// ----------------------------
// INIT DB
// ----------------------------
function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);

    req.onupgradeneeded = () => {
      const db = req.result;

      if (!db.objectStoreNames.contains(STORE_CHUNKS)) {
        db.createObjectStore(STORE_CHUNKS);
      }

      if (!db.objectStoreNames.contains(STORE_META)) {
        db.createObjectStore(STORE_META);
      }
    };

    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

// ----------------------------
// SAVE CHUNK
// ----------------------------
async function saveChunk(id, blob) {
  const db = await openDB();
  const tx = db.transaction(STORE_CHUNKS, "readwrite");
  tx.objectStore(STORE_CHUNKS).put(blob, id);
  return tx.complete;
}

// ----------------------------
// LOAD CHUNK
// ----------------------------
async function loadChunk(id) {
  const db = await openDB();
  const tx = db.transaction(STORE_CHUNKS, "readonly");
  return tx.objectStore(STORE_CHUNKS).get(id);
}

// ----------------------------
// DOWNLOAD + CACHE PACK
// ----------------------------
async function downloadChunkPack(url = "/download-chunk-pack") {
  const res = await fetch(url);
  const blob = await res.blob();

  const zip = await unzip(blob);

  const index = JSON.parse(await zip["index.json"].text());

  for (const entry of index) {
    const file = zip[entry.path];
    const chunkBlob = await file.blob();
    await saveChunk(entry.id, chunkBlob);
  }

  console.log("[Noizu] Chunk pack cached:", index.length);
}

// ----------------------------
// SIMPLE ZIP READER (JSZip)
// ----------------------------
async function unzip(blob) {
  const JSZip = await import("https://cdn.jsdelivr.net/npm/jszip/+esm");
  const zip = await JSZip.default.loadAsync(blob);

  const files = {};
  for (const name of Object.keys(zip.files)) {
    files[name] = zip.files[name];
  }

  return files;
}

// ----------------------------
// RECONSTRUCTION
// ----------------------------
async function reconstructImage(encoding, width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;

  const ctx = canvas.getContext("2d");

  for (const chunk of encoding) {
    const { x, y } = chunk;

    if (chunk.type === "ref") {
      const blob = await loadChunk(chunk.id);
      if (!blob) continue;

      const img = await createImageBitmap(blob);
      ctx.drawImage(img, x, y);

    } else if (chunk.type === "raw") {
      const img = await base64ToImage(chunk.data);
      ctx.drawImage(img, x, y);
    }
  }

  return canvas;
}

// ----------------------------
// BASE64 → IMAGE
// ----------------------------
function base64ToImage(base64) {
  return new Promise((resolve) => {
    const img = new Image();
    img.src = "data:image/png;base64," + base64;
    img.onload = () => resolve(img);
  });
}

// ----------------------------
// FETCH + AUTO DECIDE
// ----------------------------
async function fetchNoizuImage(file) {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch("/process-image", {
    method: "POST",
    body: formData
  });

  if (res.headers.get("content-type").includes("application/json")) {
    const data = await res.json();

    if (data.mode === "noizu") {
      console.log("[Noizu] Using procedural reconstruction");

      const canvas = await reconstructImage(
        data.encoding,
        512,  // TODO: pass actual size
        512
      );

      return canvas;
    }
  }

  // fallback image
  const blob = await res.blob();
  const img = await createImageBitmap(blob);

  const canvas = document.createElement("canvas");
  canvas.width = img.width;
  canvas.height = img.height;

  const ctx = canvas.getContext("2d");
  ctx.drawImage(img, 0, 0);

  return canvas;
}
