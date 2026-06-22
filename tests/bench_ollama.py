"""Benchmark Ollama models for Russian->English speed and quality.

    python tests/bench_ollama.py [model1 model2 ...]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from translate import OllamaTranslator

PHRASES = [
    "Привет, как дела?",
    "В игре всего 9 онлайна",
    "иди на мид, я помогу",
    "почему ты не пушишь?",
    "хорошая игра, всем спасибо",
]


def bench(model: str):
    t = OllamaTranslator("http://localhost:11434", model)
    # Warm up (loads model into VRAM, not counted).
    t.translate("привет")
    times = []
    print(f"\n=== {model} ===")
    for p in PHRASES:
        start = time.perf_counter()
        out = t.translate(p)
        dt = time.perf_counter() - start
        times.append(dt)
        print(f"  [{dt:5.2f}s] {p!r} -> {out!r}")
    print(f"  avg: {sum(times)/len(times):.2f}s/line")


def main():
    models = sys.argv[1:] or ["translategemma:4b", "qwen2.5:3b"]
    for m in models:
        try:
            bench(m)
        except Exception as e:
            print(f"\n=== {m} === FAILED: {e}")


if __name__ == "__main__":
    main()
