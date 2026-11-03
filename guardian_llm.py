"""The guardian's local judge: Meta Llama 3.2 3B Instruct (4-bit) on CPU with llama.cpp. No GPU, no API.

modal run guardian_llm.py::download   one-time: cache the model file (~2 GB) in a Modal Volume
modal deploy guardian_llm.py          OpenAI-compatible endpoint on CPU that scales to zero

Requires the Modal secret `guardian-llm` with GUARDIAN_LLM_KEY.
"""

from __future__ import annotations

import os
import subprocess

import modal

REPO = "bartowski/Llama-3.2-3B-Instruct-GGUF"
FILE = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
SERVED_NAME = "llama-3.2-3b"
CORES = 8
MODELS = "/models"

app = modal.App("guardian-llm")
weights = modal.Volume.from_name("guardian-llm-weights", create_if_missing=True)

image = modal.Image.from_registry("ghcr.io/ggml-org/llama.cpp:server", add_python="3.12").entrypoint([])
dl_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub==0.36.0")


@app.function(image=dl_image, volumes={MODELS: weights}, timeout=1800)
def download() -> str:
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(REPO, FILE, local_dir=MODELS)
    weights.commit()
    return f"{path} {os.path.getsize(path) / 1e9:.2f} GB"


@app.function(
    image=image,
    volumes={MODELS: weights},
    cpu=CORES,
    memory=8192,
    secrets=[modal.Secret.from_name("guardian-llm")],
    scaledown_window=600,
    timeout=3600,
    max_containers=2,
)
@modal.concurrent(max_inputs=4)
@modal.web_server(port=8080, startup_timeout=300)
def serve():
    cmd = [
        "/app/llama-server",
        "--model", f"{MODELS}/{FILE}",
        "--alias", SERVED_NAME,
        "--host", "0.0.0.0", "--port", "8080",  # noqa: S104 (inside the container)
        "--api-key", os.environ["GUARDIAN_LLM_KEY"],
        "--threads", str(CORES * 2),
        "--ctx-size", "16384",
        "--parallel", "2",
        "--cache-reuse", "256",
        "--jinja",
    ]  # fmt: skip
    subprocess.Popen(cmd)  # noqa: S603 (fixed command)


@app.local_entrypoint()
def main():
    print(download.remote())
