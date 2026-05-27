"""
Hybrid Edge-Cloud Facial Recognition — ArcFace Verification Server
Phase C1 (corrected): Image-based verification backend

Responsibilities:
  - Receive JPEG-compressed aligned face crop from edge device
  - Run ArcFace embedding extraction server-side (InsightFace)
  - Compare ONLY against ArcFace-enrolled gallery (512-d embeddings)
  - Return structured verification response with per-stage latency telemetry

NOT responsible for:
  - Face detection (done on edge — YuNet)
  - Liveness / PAD (done on edge)
  - Attendance logic (done on edge)

CRITICAL architectural invariant:
  MobileFaceNet embeddings from the edge are NEVER sent here.
  The edge sends a raw JPEG face crop.
  ArcFace extraction happens server-side only.
  Gallery contains ONLY ArcFace (512-d) embeddings.
  No cross-model embedding comparison ever occurs.
"""

import json
import time
import uuid
import logging
from contextlib import asynccontextmanager
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from arcface_verifier import ArcFaceVerifier
from gallery import FaceGallery

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("arcface_server")

# ── Global state (initialised in lifespan) ────────────────────────────────────
verifier: ArcFaceVerifier = None
gallery: FaceGallery = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load models once at startup; release on shutdown."""
    global verifier, gallery

    log.info("=== ArcFace Server startup ===")
    t0 = time.perf_counter()

    verifier = ArcFaceVerifier()
    gallery = FaceGallery(verifier=verifier)
    gallery.load_from_disk("gallery/")   # pre-enrolled embeddings

    elapsed = (time.perf_counter() - t0) * 1000
    log.info(f"Models loaded in {elapsed:.1f} ms — {len(gallery)} identities enrolled")

    yield  # ── server is running ──

    log.info("=== ArcFace Server shutdown ===")


app = FastAPI(
    title="ArcFace Verification Server",
    description="Hybrid edge–cloud facial recognition — cloud verification endpoint",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Response schema ───────────────────────────────────────────────────────────
#
# Metadata arrives as a JSON-encoded Form field alongside the UploadFile image.
# There is no request Pydantic model — FastAPI parses multipart directly.
#
# Edge metadata fields (Form["metadata"] JSON):
#   session_id, frame_id, track_id, edge_confidence,
#   edge_candidate, routing_strategy, timestamp_edge_ms

class VerificationResponse(BaseModel):
    """
    Standardised response contract for /verify/image.
    All latency fields are server-measured; roundtrip_ms is added by the edge client.
    """
    # Identity result
    verified: bool
    identity: Optional[str]                  # None if below threshold
    arcface_confidence: float

    # Agreement with edge
    edge_candidate: Optional[str]            # echoed from request metadata
    edge_cloud_agree: Optional[bool]         # None if edge had no candidate

    # Per-stage server latency (ms) — for latency breakdown analysis
    image_decode_ms: float
    arcface_extract_ms: float
    gallery_search_ms: float
    server_total_ms: float                   # sum of all server stages

    # Metadata
    route: str = "CLOUD_VERIFY"
    request_id: str
    timestamp_server_ms: int
    gallery_size: int


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "gallery_size": len(gallery) if gallery else 0,
        "model_loaded": verifier is not None,
    }


# ── Primary verification endpoint ─────────────────────────────────────────────

@app.post("/verify/image", response_model=VerificationResponse)
async def verify_image(
    image: UploadFile = File(..., description="JPEG-compressed aligned face crop from edge"),
    metadata: str = Form(..., description=(
        "JSON string with fields: "
        "session_id, frame_id, track_id, edge_confidence, "
        "edge_candidate, routing_strategy, timestamp_edge_ms"
    )),
):
    """
    Primary offload endpoint — IMAGE-BASED.

    Edge sends a JPEG-encoded aligned face crop when its MobileFaceNet
    confidence falls below the offload threshold. ArcFace extraction
    happens HERE, server-side only.

    CRITICAL invariant:
      No MobileFaceNet embeddings are accepted here.
      No cross-model embedding comparison ever occurs.
      Gallery contains ONLY ArcFace (512-d) embeddings.

    Research instrumentation:
      Per-stage latency (image_decode / arcface_extract / gallery_search)
      is returned explicitly to enable latency breakdown analysis.
    """
    t_total_start = time.perf_counter()
    request_id = str(uuid.uuid4())[:8]

    # ── Parse edge metadata ────────────────────────────────────────────────────
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"Invalid metadata JSON: {e}")

    session_id      = meta.get("session_id", "unknown")
    frame_id        = meta.get("frame_id", -1)
    edge_confidence = float(meta.get("edge_confidence", 0.0))
    edge_candidate  = meta.get("edge_candidate")   # may be None if edge had no candidate

    # ── Stage 1: Image decode ─────────────────────────────────────────────────
    t_decode_start = time.perf_counter()
    raw_bytes = await image.read()
    if not raw_bytes:
        raise HTTPException(status_code=422, detail="Empty image payload")

    img_array = np.frombuffer(raw_bytes, dtype=np.uint8)
    face_bgr = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    image_decode_ms = (time.perf_counter() - t_decode_start) * 1000

    if face_bgr is None:
        raise HTTPException(
            status_code=422,
            detail="cv2.imdecode failed — confirm edge sends valid JPEG bytes"
        )

    # ── Stage 2: ArcFace embedding extraction ─────────────────────────────────
    # Produces 512-d L2-normalised embedding via InsightFace.
    # MobileFaceNet (128-d) is NOT used here and never will be.
    t_extract_start = time.perf_counter()
    embedding = verifier.extract_embedding(face_bgr)
    arcface_extract_ms = (time.perf_counter() - t_extract_start) * 1000

    if embedding is None:
        # Degenerate case: ArcFace found no face in the crop.
        # Likely cause: severe crop quality issue from edge (blur, occlusion, tiny crop).
        # The edge pipeline should be logging face crop quality; surface this as a
        # researchable event rather than a hard error.
        log.warning(
            f"[{request_id}] ArcFace found no face in received crop "
            f"(session={session_id} frame={frame_id} "
            f"crop_size={face_bgr.shape[:2]}) → unverified"
        )
        return VerificationResponse(
            verified=False,
            identity=None,
            arcface_confidence=0.0,
            edge_candidate=edge_candidate,
            edge_cloud_agree=None,
            image_decode_ms=round(image_decode_ms, 2),
            arcface_extract_ms=round(arcface_extract_ms, 2),
            gallery_search_ms=0.0,
            server_total_ms=round((time.perf_counter() - t_total_start) * 1000, 2),
            route="CLOUD_VERIFY",
            request_id=request_id,
            timestamp_server_ms=int(time.time() * 1000),
            gallery_size=len(gallery),
        )

    # ── Stage 3: ArcFace gallery search ───────────────────────────────────────
    # embedding: 512-d, L2-normalised, from InsightFace ArcFace (r100 / buffalo_l)
    # gallery: contains only ArcFace (512-d) embeddings enrolled via enroll_gallery.py
    t_search_start = time.perf_counter()
    identity, arcface_score = gallery.search(embedding)
    gallery_search_ms = (time.perf_counter() - t_search_start) * 1000

    server_total_ms = (time.perf_counter() - t_total_start) * 1000

    # ── Verification threshold ─────────────────────────────────────────────────
    # ArcFace cosine similarity threshold. 0.35 is conservative; calibrate
    # per-dataset using ROC analysis across gallery / probe splits.
    VERIFICATION_THRESHOLD = 0.35
    verified = arcface_score >= VERIFICATION_THRESHOLD

    # ── Edge/cloud agreement ───────────────────────────────────────────────────
    edge_cloud_agree = None
    if edge_candidate is not None and identity is not None:
        edge_cloud_agree = (edge_candidate == identity)

    response = VerificationResponse(
        verified=verified,
        identity=identity if verified else None,
        arcface_confidence=round(float(arcface_score), 6),
        edge_candidate=edge_candidate,
        edge_cloud_agree=edge_cloud_agree,
        image_decode_ms=round(image_decode_ms, 2),
        arcface_extract_ms=round(arcface_extract_ms, 2),
        gallery_search_ms=round(gallery_search_ms, 2),
        server_total_ms=round(server_total_ms, 2),
        route="CLOUD_VERIFY",
        request_id=request_id,
        timestamp_server_ms=int(time.time() * 1000),
        gallery_size=len(gallery),
    )

    log.info(
        "[RECOGNITION] ArcFace verify: identity=%s conf=%.2f verified=%s agree=%s "
        "decode=%.0fms extract=%.0fms search=%.0fms total=%.0fms session=%s frame=%s",
        response.identity,
        arcface_score,
        verified,
        edge_cloud_agree,
        image_decode_ms,
        arcface_extract_ms,
        gallery_search_ms,
        server_total_ms,
        session_id,
        frame_id,
    )

    return response


# ── Enrollment endpoint (offline gallery building) ────────────────────────────
#
# Primary enrollment path is enroll_gallery.py (writes .npy files directly).
# This REST endpoint is a secondary path for programmatic enrollment.
# Accepts ArcFace (512-d) embeddings only — must be extracted server-side
# via enroll_gallery.py or equivalent before posting here.

class EnrollRequest(BaseModel):
    identity: str
    embedding: list[float] = Field(
        ...,
        description="512-d ArcFace L2-normalised embedding (from InsightFace extraction)"
    )
    source: str = Field("manual", description="Enrollment source tag for provenance")


@app.post("/enroll")
async def enroll_identity(req: EnrollRequest):
    """
    Add an ArcFace (512-d) identity embedding to the gallery.
    In research use: called offline during gallery construction.
    NOT called during live pipeline operation.
    """
    if len(req.embedding) != 512:
        raise HTTPException(
            status_code=422,
            detail=f"Expected 512-d ArcFace embedding, received {len(req.embedding)}-d. "
                   f"Embeddings must be extracted server-side via ArcFace — "
                   f"do NOT send MobileFaceNet (128-d) embeddings here."
        )

    vec = np.array(req.embedding, dtype=np.float32)
    vec = vec / (np.linalg.norm(vec) + 1e-8)

    gallery.enroll(req.identity, vec, source=req.source)
    log.info(f"Enrolled identity='{req.identity}' source='{req.source}' dim=512")

    return {"enrolled": req.identity, "gallery_size": len(gallery)}


@app.get("/gallery/stats")
async def gallery_stats():
    return {
        "total_identities": len(gallery),
        "identities": gallery.identity_list(),
    }


# ── Request telemetry middleware ───────────────────────────────────────────────

@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    """Log every request with timing — feeds into cloud RTT analysis."""
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    log.debug(f"{request.method} {request.url.path} → {response.status_code} [{elapsed_ms:.1f}ms]")
    return response