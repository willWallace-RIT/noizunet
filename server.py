import os
import io
import json
import base64
import zipfile
import tempfile
import numpy as np
from PIL import Image
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

def chunk_image(img):
    w, h = img.size
    cw, ch = CHUNK_SIZE
    chunks = []

    for y in range(0, h, ch):
        for x in range(0, w, cw):
            chunk = img.crop((x, y, x + cw, y + ch))
            chunks.append((x, y, chunk))

    return chunks

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
@app.post("/process-image")
async def process_image(file: UploadFile = File(...)):
    img = Image.open(file.file).convert("RGB")

    chunks = chunk_image(img)

    encoding = []
    matched_count = 0

    for x, y, chunk in chunks:
        chunk_np = np.array(chunk)

        match, score = find_best_match(chunk_np)

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
        return JSONResponse({
            "mode": "noizu",
            "encoding": encoding,
            "stats": {
                "matched_chunks": matched_count,
                "total_chunks": len(chunks),
                "raw_size_est": raw_size,
                "encoding_size_est": encoding_size
            }
        })

    else:
        buffer = io.BytesIO()
        img.save(buffer, format="WEBP")
        buffer.seek(0)

        return StreamingResponse(buffer, media_type="image/webp")
