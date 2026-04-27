import os
import io
import json
import base64
import zipfile
import tempfile
import numpy as np
from PIL import Image
import faiss
import numpy as np


from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.responses import FileResponse
app = FastAPI()

# ----------------------------
# CONFIG
# ----------------------------
CHUNK_SIZE = (64, 64)
SIMILARITY_THRESHOLD = 0.01   # lower = stricter
DATASET_PATH = "chunk_dataset.json"

# ----------------------------
# LOAD DATASET
# ----------------------------
if os.path.exists(DATASET_PATH):
    with open(DATASET_PATH, "r") as f:
        CHUNK_DATASET = json.load(f)
else:
    CHUNK_DATASET = []

# ----------------------------
# UTIL
# ----------------------------
def mse(a, b):
    return np.mean((a.astype("float32") - b.astype("float32")) ** 2)

def similarity(a, b):
    return 1.0 - np.mean((a - b) ** 2)

def update_stats(top_k):
    best, best_sim = top_k[0]

    best["use_count"] += 1
    best["match_score"] += best_sim

    for entry, sim in top_k:
        entry["nearby_score"] += sim
        entry["avg_similarity"] = (
            entry["avg_similarity"] * 0.9 + sim * 0.1
        )

    return best


def faiss_search(chunk, k=10):
    vec = embed(chunk).reshape(1, -1)

    distances, indices = index.search(vec, k)

    results = []
    for i, dist in zip(indices[0], distances[0]):
        results.append((CHUNK_DATASET[i], 1 / (1 + dist)))
    return results


def detect_missing_regions(top_k, threshold=0.7):
    missing = []

    for entry, sim in top_k:
        if sim < threshold:
            missing.append(entry)

    return missing





def embed(chunk):
    # simple baseline (replace later with CNN/CLIP)
    return chunk.flatten().astype("float32")

def top_k_matches(chunk, k=5):
    scored = []

    for entry in CHUNK_DATASET:
        sim = similarity(chunk, entry["image"])
        scored.append((entry, sim))
        entry.update({
    "use_count": 0,
    "match_score": 0.0,
    "nearby_score": 0.0,
    "avg_similarity": 0.0
})
        entry["embedding"] = embed(entry["image"])

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]

def compute_rank(e):
    return (
        e["use_count"] +
        e["match_score"] * 2 +
        e["nearby_score"] * 0.5 +
        e["avg_similarity"] * 3
    )

def encode_chunk(chunk):
    top_k = top_k_matches(chunk)

    best = update_stats(top_k)
    best_sim = top_k[0][1]

    if best_sim > 0.85:
        return {"type": "ref", "id": best["id"]}
    else:
        return {"type": "raw"}


def chunk_image(img):
    w, h = img.size
    cw, ch = CHUNK_SIZE
    chunks = []

    for y in range(0, h, ch):
        for x in range(0, w, cw):
            chunk = img.crop((x, y, x + cw, y + ch))
            chunks.append((x, y, chunk))

    return chunks

def save_dataset():
    with open("chunks.json", "w") as f:
        json.dump(CHUNK_DATASET, f)



def load_dataset():
    global CHUNK_DATASET
    CHUNK_DATASET = json.load(open("chunks.json"))

def encode_chunk_to_base64(img):
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()

# ----------------------------
# MATCHING
# ----------------------------
def find_best_match(chunk_np):
    best = None
    best_score = float("inf")

    for entry in CHUNK_DATASET:
        dataset_chunk = np.array(Image.open(entry["path"]))
        score = mse(chunk_np, dataset_chunk)

        if score < best_score:
            best_score = score
            best = entry

    if best_score < SIMILARITY_THRESHOLD:
        return best, best_score

    return None, best_score

# ----------------------------
# DECISION ENGINE
# ----------------------------
def estimate_sizes(chunks, encoding):
    raw_size = sum(len(encode_chunk_to_base64(c[2])) for c in chunks)
    encoding_size = len(json.dumps(encoding))
    return raw_size, encoding_size


def assign_tier(e):
    r = compute_rank(e)

    if r > 1000:
        return 0
    elif r > 200:
        return 1
    else:
        return 2

def build_chunk_pack():
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    zip_path = temp_file.name

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:

        index = []

        for entry in CHUNK_DATASET:
            chunk_path = entry["path"]
            chunk_id = entry["id"]

            arcname = f"chunks/{chunk_id}.png"
            z.write(chunk_path, arcname)

            index.append({
                "id": chunk_id,
                "path": arcname
            })

        # Write index.json
        z.writestr("index.json", json.dumps(index, indent=2))

    return zip_path


@app.get("/chunks")
def get_chunks(top: int = 50):
    return sorted(
        CHUNK_DATASET,
        key=lambda x: compute_rank(x),
        reverse=True
    )[:top]

@app.get("/download-chunk-pack")
def download_chunk_pack():
    zip_path = build_chunk_pack()

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="noizunet_pack.zip"
    )

# ----------------------------
# ROUTES
# ----------------------------

@app.get("/patch/{patch_id}")
def get_patch(patch_id: str):
    patch = load_patch(patch_id)

    return FileResponse(
        patch["path"],
        media_type="image/webp"
    )




@app.post("/process-image")
async def process_image(file: UploadFile = File(...)):
    img = Image.open(file.file).convert("RGB")

    chunks = chunk_image(img)

    encoding = []
    matched_count = 0
dim = CHUNK_DATASET[0]["embedding"].shape[0]

index = faiss.IndexFlatL2(dim)

vectors = np.array([e["embedding"] for e in CHUNK_DATASET])
index.add(vectors)
    for x, y, chunk in chunks:
        chunk_np = np.array(chunk)

        match, score = find_best_match(chunk_np)
		top_k = faiss_search(chunk)
        if match:
            encoding.append({
                "type": "ref",
                "id": match["id"],
                "x": x,
                "y": y
            })
            matched_count += 1
        else:
            encoding.append({
                "type": "raw",
                "data": encode_chunk_to_base64(chunk),
                "x": x,
                "y": y
            })

    raw_size, encoding_size = estimate_sizes(chunks, encoding)

    # 🔥 Decision logic
    if encoding_size < raw_size:
        mode = "noizu"
    else:
        mode = "image"



    
    # ----------------------------
    # RESPONSE
    # ----------------------------
    if mode == "noizu":
        
return {
    "mode": "hybrid",
    "encoding": [
        {"type": "ref", "id": "chunk_001"},
        {"type": "ref", "id": "chunk_042"},
        {"type": "missing", "patch_id": "p12"}
    ],
    "patches": [
        {"id": "p12", "url": "/patch/p12"}
    ]
}

    else:
        buffer = io.BytesIO()
        img.save(buffer, format="WEBP")
        buffer.seek(0)

        return StreamingResponse(buffer, media_type="image/webp")
