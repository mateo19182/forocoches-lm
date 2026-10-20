"""Quantize a run to MLC q4f16_1 so WebLLM can run it in the browser (WebGPU).

    .venv-train/bin/python scripts/export_webllm.py v0            # runs/v0/final -> data/web/mlc/v0
    .venv-train/bin/python scripts/export_webllm.py base --src ~/.cache/huggingface/hub/models--unsloth--gemma-3-1b-pt/snapshots/<hash>

The output is served by web/app.py at /mlc/<version>/resolve/main/ and loaded with WebLLM's prebuilt
gemma3-1b-it-q4f16_1 library (web/static/vendor/), which only fixes the architecture, not the weights.

Why not `mlc_llm convert_weight`: its TVM-compiled quantize kernel segfaults with the current nightly
wheels (CPU and CUDA alike), so this reimplements the same q4f16_1 group quantization in torch and
writes the same tensor-cache layout as tvm.contrib.tvmjs.dump_tensor_cache. Names and shapes match
mlc-ai/gemma3-1b-it-q4f16_1-MLC exactly.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import torch
from safetensors.torch import load_file

ROOT = Path(__file__).resolve().parent.parent
GROUP, MAX_INT, PER_STORAGE = 32, 7, 8  # q4f16_1: 32 weights per fp16 scale, int4 in [0, 14], 8 per uint32
SHARD_CAP = 32 << 20
NORMS = ["input_layernorm", "post_attention_layernorm", "post_feedforward_layernorm", "pre_feedforward_layernorm",
         "self_attn.k_norm", "self_attn.q_norm"]
LINEARS = ["mlp.down_proj", "mlp.gate_up_proj", "self_attn.k_proj", "self_attn.o_proj", "self_attn.q_proj",
           "self_attn.v_proj"]


def quantize(w: torch.Tensor):
    """[N, K] -> q_weight [N, K/8] uint32, q_scale [N, K/32] float16, as MLC's GroupQuantize._quantize."""
    w = w.to(torch.float16).float()  # MLC casts to the model dtype before quantizing
    n, k = w.shape
    g = w.view(n, k // GROUP, GROUP)
    scale = (g.abs().amax(-1).half() / MAX_INT).half()
    safe = torch.where(scale == 0, torch.ones_like(scale), scale).float()  # all-zero group: q = 7 = zero
    q = torch.round(g / safe[..., None] + MAX_INT).clamp(0, 2 * MAX_INT).to(torch.int64)
    shifts = torch.arange(PER_STORAGE, dtype=torch.int64) * 4
    packed = (q.view(n, k // PER_STORAGE, PER_STORAGE) << shifts).sum(-1)
    err = ((q.float() - MAX_INT).view(n, k // GROUP, GROUP) * safe[..., None] - g).pow(2).mean().sqrt() / g.pow(2).mean().sqrt()
    return packed.numpy().astype(np.uint32), scale.numpy(), float(err)


def mlc_params(hf: dict[str, torch.Tensor], n_layers: int):
    """Yield (name, array) in the order mlc-ai's export uses; HF Gemma 3 names -> MLC names."""
    errs = []

    def quant(name, w):
        qw, qs, err = quantize(w)
        errs.append(err)
        yield f"{name}.q_weight", qw
        yield f"{name}.q_scale", qs

    def norm(name, w):
        yield name, (w.float() + 1).half().numpy()  # Gemma stores RMSNorm weight as (1 + w)

    yield from quant("language_model.model.embed_tokens", hf["model.embed_tokens.weight"])
    for i in range(n_layers):
        p, m = f"model.layers.{i}.", f"language_model.model.layers.{i}."
        for sub in sorted(NORMS + LINEARS):
            if sub in NORMS:
                yield from norm(m + sub + ".weight", hf[p + sub + ".weight"])
            elif sub == "mlp.gate_up_proj":
                yield from quant(m + sub, torch.cat([hf[p + "mlp.gate_proj.weight"], hf[p + "mlp.up_proj.weight"]]))
            else:
                yield from quant(m + sub, hf[p + sub + ".weight"])
    yield from norm("language_model.model.norm.weight", hf["model.norm.weight"])
    print(f"quantization rel. RMS error: mean {np.mean(errs):.4f}, max {np.max(errs):.4f}")


def write_cache(params, out: Path, n_weights: int):
    """tensor-cache.json + params_shard_N.bin, the layout of tvm.contrib.tvmjs.dump_tensor_cache."""
    shards, pending, pending_recs, total = [], bytearray(), [], 0

    def commit(data, recs):
        path = out / f"params_shard_{len(shards)}.bin"
        path.write_bytes(data)
        shards.append({"dataPath": path.name, "format": "raw-shard", "nbytes": len(data), "records": recs,
                       "md5sum": hashlib.md5(data).hexdigest()})

    for name, arr in params:
        data = arr.tobytes()
        rec = {"name": name, "shape": list(arr.shape), "dtype": str(arr.dtype), "format": "f32-to-bf16", "nbytes": len(data)}
        total += len(data)
        if len(pending) + len(data) >= SHARD_CAP:
            if len(data) * 2 >= SHARD_CAP:  # big tensor: its own shard
                commit(data, [{**rec, "byteOffset": 0}])
                continue
            commit(bytes(pending), pending_recs)
            pending, pending_recs = bytearray(), []
        pending_recs.append({**rec, "byteOffset": len(pending)})
        pending += data
    if pending:
        commit(bytes(pending), pending_recs)
    n_params = sum(len(s["records"]) for s in shards)
    meta = {"ParamSize": n_params, "ParamBytes": float(total), "BitsPerParam": total * 8 / n_weights}
    (out / "tensor-cache.json").write_text(json.dumps({"metadata": meta, "records": shards}, indent=4))
    print(f"{n_params} params, {total / 1e6:.0f} MB in {len(shards)} shards, {meta['BitsPerParam']:.2f} bits/weight")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="name under data/web/mlc/ (and runs/<version>/final unless --src)")
    ap.add_argument("--src", type=Path, help="HF model dir (config.json, model.safetensors, tokenizer files)")
    a = ap.parse_args()
    src = a.src or ROOT / "runs" / a.version / "final"
    out = ROOT / "data/web/mlc" / a.version
    cfg = json.loads((src / "config.json").read_text())
    assert cfg["model_type"] == "gemma3_text" and cfg["hidden_size"] == 1152, "this exporter targets Gemma 3 1B only"

    hf = load_file(src / "model.safetensors")
    n_weights = sum(t.numel() for t in hf.values())
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("params_shard_*.bin"):
        f.unlink()
    # mlc-ai's order: by module name, q_weight before q_scale
    params = sorted(mlc_params(hf, cfg["num_hidden_layers"]), key=lambda kv: (kv[0].rsplit(".", 1)[0], kv[0].endswith("q_scale")))
    write_cache(params, out, n_weights)

    for name, dst in [(src / n, out / n) for n in ["tokenizer.json", "tokenizer.model", "tokenizer_config.json"]] + [
            (ROOT / "web/mlc_chat_config.json", out / "mlc-chat-config.json")]:
        dst.unlink(missing_ok=True)  # the run's tokenizer files are read-only
        shutil.copyfile(name, dst)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
