"""Serve Meta Muse Glimmer 30B (open weights, Apache 2.0) with vLLM on a Modal GPU.

  modal run glimmer_server.py::download   one-time: cache the weights in a Modal Volume
  modal deploy glimmer_server.py          endpoint that scales to zero when idle

The endpoint is OpenAI-compatible and requires GLIMMER_API_KEY (Modal secret `glimmer`).
Following Meta's vLLM guide: official image, bundled chat template, muse_glimmer parsers.
"""

from __future__ import annotations

import os
import subprocess

import modal

MODEL_ID = "meta-models/Muse-Glimmer-30B"
SERVED_NAME = "muse-glimmer"
PORT = 8000
GPU = "H100"  # ~60 GB bf16 weights; Meta's guide budgets 72 GB of VRAM

app = modal.App("decision-twin-glimmer")
weights = modal.Volume.from_name("glimmer-weights", create_if_missing=True)
CACHE = "/models"

image = (
    modal.Image.from_registry("vllm/vllm-openai:muse-glimmer", add_python=None)
    .entrypoint([])
    .dockerfile_commands(['RUN ln -sf "$(command -v python3)" /usr/local/bin/python'])
    .env({"HF_HOME": CACHE})
)


@app.function(image=image, volumes={CACHE: weights}, timeout=3 * 3600, cpu=8)
def download() -> str:
    from huggingface_hub import snapshot_download

    path = snapshot_download(MODEL_ID, cache_dir=f"{CACHE}/hub")
    weights.commit()
    return path


@app.function(
    image=image,
    gpu=GPU,
    volumes={CACHE: weights},
    secrets=[modal.Secret.from_name("glimmer")],
    scaledown_window=10 * 60,  # stop paying 10 minutes after the last request
    timeout=4 * 3600,
    max_containers=1,
)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=PORT, startup_timeout=20 * 60)
def serve() -> None:
    cmd = [
        "vllm",
        "serve",
        MODEL_ID,
        "--served-model-name",
        SERVED_NAME,
        "--host",
        "0.0.0.0",  # noqa: S104 - container port, fronted by Modal and an API key
        "--port",
        str(PORT),
        "--api-key",
        os.environ["GLIMMER_API_KEY"],
        "--download-dir",
        f"{CACHE}/hub",
        "--tensor-parallel-size",
        "1",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "muse_glimmer",
        "--reasoning-parser",
        "muse_glimmer",
        "--generation-config",
        "auto",
        "--max-model-len",
        "32768",
        "--gpu-memory-utilization",
        "0.92",
        "--max-logprobs",
        "20",
    ]
    subprocess.Popen(cmd)  # noqa: S603 - fixed argument list
