from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Callable


PROFILE_ID = "multilingual-e5-small-qint8"
DIMENSIONS = 384
BASE_URL = "https://huggingface.co/intfloat/multilingual-e5-small/resolve/main/onnx"
PROFILE_FILES = {
    "model.onnx": ("model_qint8_avx512_vnni.onnx", "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"),
    "tokenizer.json": ("tokenizer.json", "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
    "config.json": ("config.json", "bbb7c1333fc4b3e27fbc9cd5d2070aabcc1d4dfb99917c3633e772f97545a6b6"),
    "special_tokens_map.json": ("special_tokens_map.json", "d05497f1da52c5e09554c0cd874037a083e1dc1b9cfd48034d1c717f1afc07a7"),
    "tokenizer_config.json": ("tokenizer_config.json", "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b"),
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class LocalEmbeddingProfile:
    def __init__(self, model_root: Path):
        self.directory = model_root / "multilingual-e5-small"
        self.manifest_path = self.directory / "manifest.json"
        self._session = None
        self._tokenizer = None
        self._verified = False

    @property
    def ready(self) -> bool:
        if self._verified:
            return True
        if not self.manifest_path.exists():
            return False
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        self._verified = manifest.get("profile") == PROFILE_ID and all(
            (self.directory / local_name).exists() and file_sha256(self.directory / local_name) == expected
            for local_name, (_, expected) in PROFILE_FILES.items()
        )
        return self._verified

    def download(self, progress: Callable[[dict], None] | None = None) -> dict:
        self.directory.mkdir(parents=True, exist_ok=True)
        for index, (local_name, (remote_name, expected)) in enumerate(PROFILE_FILES.items(), start=1):
            target = self.directory / local_name
            if target.exists() and file_sha256(target) == expected:
                continue
            partial = target.with_suffix(target.suffix + ".part")
            request = urllib.request.Request(
                f"{BASE_URL}/{remote_name}", headers={"User-Agent": "EAI-Desktop/1.0 embedding-profile"}
            )
            digest = hashlib.sha256()
            total = 0
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > 160 * 1024 * 1024:
                        raise ValueError("embedding model file exceeds the 160 MB limit")
                    digest.update(block)
                    output.write(block)
                    if progress:
                        progress({"file": local_name, "index": index, "total_files": len(PROFILE_FILES), "bytes": total})
            if digest.hexdigest() != expected:
                partial.unlink(missing_ok=True)
                raise ValueError(f"embedding model checksum mismatch: {local_name}")
            partial.replace(target)
        manifest = {
            "profile": PROFILE_ID, "dimensions": DIMENSIONS, "source": "intfloat/multilingual-e5-small",
            "files": {name: expected for name, (_, expected) in PROFILE_FILES.items()},
        }
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._verified = True
        return manifest

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not self.ready:
            raise RuntimeError("embedding profile is not ready")
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError("local embedding dependencies are unavailable") from exc
        if self._tokenizer is None:
            tokenizer = Tokenizer.from_file(str(self.directory / "tokenizer.json"))
            tokenizer.enable_truncation(max_length=512)
            tokenizer.enable_padding(length=None, pad_id=1, pad_token="<pad>")
            self._tokenizer = tokenizer
        if self._session is None:
            self._session = ort.InferenceSession(str(self.directory / "model.onnx"), providers=["CPUExecutionProvider"])
        prefix = "query: " if query else "passage: "
        encodings = self._tokenizer.encode_batch([prefix + text[:12000] for text in texts])
        input_ids = np.asarray([item.ids for item in encodings], dtype=np.int64)
        attention_mask = np.asarray([item.attention_mask for item in encodings], dtype=np.int64)
        available = {item.name for item in self._session.get_inputs()}
        inputs = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in available:
            inputs["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self._session.run(None, {key: value for key, value in inputs.items() if key in available})[0]
        mask = attention_mask[..., None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        return pooled.astype(np.float32).tolist()
