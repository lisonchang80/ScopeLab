"""ScopeLab — 裝置偵測 / 依 VRAM 調參 / GPU 健檢（純程式、無需 agent）。

- pick_device()：torch.cuda 偵測 GPU/CPU 與 VRAM
- batch_for()  ：依 VRAM × 影像大小 × 架構，查表給安全 batch size（8GB 卡保守）
- gpu_stats()  ：訓練中健檢，優先 nvidia-smi（本機無 pynvml）→ 退回 torch.cuda 記憶體
"""
from __future__ import annotations

import shutil
import subprocess

import torch


def pick_device() -> dict:
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        return {"device": "cuda", "name": p.name, "vram_gb": round(p.total_memory / 1e9, 1)}
    return {"device": "cpu", "name": "CPU", "vram_gb": 0.0}


def batch_for(vram_gb: float, img: int, arch: str) -> int:
    """保守 batch size：寧小勿 OOM（消費級 8GB 卡尤其）。"""
    if vram_gb <= 0:                      # CPU
        return 8
    heavy = arch in ("resnet50", "resnet101", "resnet152")
    base = 16 if heavy else 32
    if img >= 224:
        base = base // 2                 # 224 比 160 吃約 2x
    if vram_gb < 6:
        base = max(4, base // 2)
    elif vram_gb >= 12:
        base *= 2
    return max(4, base)


def device_summary(d: dict) -> str:
    if d["device"] == "cuda":
        return f"🟢 GPU · {d['name']} · {d['vram_gb']}GB VRAM"
    return "🟡 CPU（無可用 GPU，訓練會明顯較慢）"


def gpu_stats() -> dict | None:
    """訓練中 GPU 健檢。優先 nvidia-smi（util/temp/mem 都有），退回 torch.cuda 記憶體。
    回 None = 沒有 GPU 或查不到。"""
    if not torch.cuda.is_available():
        return None
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=4)
            row = out.stdout.strip().splitlines()[0]
            u, t, mu, mt = [x.strip() for x in row.split(",")]
            return {"util": int(u), "temp": int(t),
                    "mem_used_gb": round(int(mu) / 1024, 1), "mem_total_gb": round(int(mt) / 1024, 1)}
        except Exception:
            pass
    # 退回：torch 只拿得到本進程已配置的記憶體
    try:
        used = torch.cuda.memory_reserved(0) / 1e9
        total = torch.cuda.get_device_properties(0).total_memory / 1e9
        return {"util": None, "temp": None,
                "mem_used_gb": round(used, 1), "mem_total_gb": round(total, 1)}
    except Exception:
        return None


def health_warnings(stats: dict | None, training: bool = True) -> list[str]:
    """把健檢數據翻成人話告警（純查表）。"""
    if not stats:
        return []
    w = []
    if training and stats.get("util") is not None and stats["util"] == 0:
        w.append("GPU 使用率 0%（可能未真正用到 GPU 或卡住）")
    if stats.get("temp") is not None and stats["temp"] >= 85:
        w.append(f"GPU 溫度偏高 {stats['temp']}°C（可能降頻）")
    mt, mu = stats.get("mem_total_gb"), stats.get("mem_used_gb")
    if mt and mu and mu >= mt * 0.95:
        w.append(f"VRAM 逼近上限 {mu}/{mt}GB（OOM 風險）")
    return w
