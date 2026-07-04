# =============================================================================
# Dockerfile — NeMo Multitalker Parakeet Agent (GPU)
# =============================================================================
#
# This container runs:
#   1. NeMo multitalker Parakeet (Sortformer diarization + multi-speaker ASR)
#   2. FastAPI WebSocket server for real-time audio transcription (M2+)
#   3. Strands role inference agent (uses Bedrock, NOT local GPU) (M3+)
#
# REQUIRES: NVIDIA Container Toolkit + GPU-capable Docker runtime
#
# GPU SYNTAX (docker-compose.yml):
#   deploy:
#     resources:
#       reservations:
#         devices:
#           - driver: nvidia
#             count: 1
#             capabilities: [gpu]
#
# VRAM BUDGET (RTX 5080, 16GB):
#   Sortformer:     ~2GB
#   Parakeet x2:    ~4-6GB each
#   Total:          ~10-14GB
#   Headroom:       ~2-6GB
#
# IMPORTANT: NeMo owns the GPU exclusively. The Strands role inference
# agent uses Bedrock or CPU-only Ollama. Do NOT co-locate a GPU LLM.
#
# BUILD:
#   docker build -t ambient-scribe-nemo ./docker/nemo
#
# RUN (interactive, for API discovery):
#   docker run --rm -it --gpus all \
#     -v $(pwd)/scripts:/app/scripts \
#     -v $(pwd)/tests/fixtures/audio:/app/audio \
#     ambient-scribe-nemo bash
#
# RUN (server mode, for M2+):
#   docker run --rm --gpus all \
#     -v $(pwd)/strands_agents:/app \
#     -p 8000:8000 \
#     ambient-scribe-nemo
# =============================================================================

FROM nvcr.io/nvidia/nemo:26.02

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Pin NeMo to the released multitalker line instead of rebuilding from main.
# The browser still uses the same overlap-aware checkpoints; this only makes
# the GPU container reproducible for live transcription sessions.
RUN pip install --no-cache-dir "nemo_toolkit[asr]==2.7.3"

WORKDIR /app

# Install the same agent dependencies used by local tests and the dev server.
# A user reaching the browser WebSocket should therefore hit the same FastAPI,
# Strands, and Mercure client floors that CI just verified.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Download NeMo models at build time (avoids re-download on restart)
# These are multi-GB files — cached in the Docker layer
#
# IMPORTANT: The multitalker Parakeet requires the STREAMING Sortformer (v2.1),
# NOT the offline v1. The offline v1 is for standalone diarization only.
# The streaming variant provides frame-level speaker activity that drives
# the per-speaker kernel injection in the ASR model.
RUN python -c "from huggingface_hub import hf_hub_download; hf_hub_download('nvidia/diar_streaming_sortformer_4spk-v2.1', filename='diar_streaming_sortformer_4spk-v2.1.nemo'); hf_hub_download('nvidia/multitalker-parakeet-streaming-0.6b-v1', filename='multitalker-parakeet-streaming-0.6b-v1.nemo')"

# Copy application code (build context is ./strands_agents)
# Dev override: docker-compose.yml mounts ./strands_agents:/app for hot reload
COPY . /app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default: start the FastAPI server (override with 'bash' for interactive use)
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "websockets-sansio"]
