"""Translation backends (any language -> English), selectable via config.

Backends:
  - "argos": fully offline (Argos Translate / ctranslate2). One model per
    language pair; source language comes from cfg["source_lang"].
  - "nllb": fully offline (NLLB-200 distilled 600M via ctranslate2). Better
    quality than Argos; source language comes from cfg["source_lang"].
  - "deepl": DeepL API. Highest quality; needs internet + an auth key. The free
    tier uses api-free.deepl.com (keys end in ":fx"); pro uses api.deepl.com.
    Auto-detects the source language when cfg["source_lang"] is "auto".
  - "ollama": local LLM. Auto-detects the source language.

All expose translate(text) and translate_many(texts). The pipeline uses
translate_many so a whole snapshot is one network round-trip on DeepL.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


# --------------------------------------------------------------------------
# Argos (offline)
# --------------------------------------------------------------------------
class ArgosTranslator:
    def __init__(self, from_code: str = "ru", to_code: str = "en"):
        import argostranslate.translate as t

        langs = {l.code: l for l in t.get_installed_languages()}
        src, dst = langs.get(from_code), langs.get(to_code)
        self._translation = src.get_translation(dst) if src and dst else None
        if self._translation is None:
            raise RuntimeError(
                f"Argos {from_code}->{to_code} model not installed. "
                f"Run:  python setup_models.py"
            )

    @functools.lru_cache(maxsize=2048)
    def translate(self, text: str) -> str:
        if not text.strip():
            return text
        try:
            return self._translation.translate(text)
        except Exception as e:
            print(f"[argos] failed for {text!r}: {e}")
            return text

    def translate_many(self, texts: list[str]) -> list[str]:
        return [self.translate(t) for t in texts]


# --------------------------------------------------------------------------
# NLLB-200 (offline, ctranslate2)
# --------------------------------------------------------------------------
_NLLB_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "nllb-200-distilled-600M-ct2"
_NLLB_REPO = "entai2965/nllb-200-distilled-600M-ctranslate2"

# ISO 639-1 -> NLLB (FLORES-200) codes for common source languages.
_NLLB_CODES = {
    "ru": "rus_Cyrl", "uk": "ukr_Cyrl", "be": "bel_Cyrl", "bg": "bul_Cyrl",
    "sr": "srp_Cyrl", "ja": "jpn_Jpan", "zh": "zho_Hans", "zh-cn": "zho_Hans",
    "zh-tw": "zho_Hant", "ko": "kor_Hang", "de": "deu_Latn", "fr": "fra_Latn",
    "es": "spa_Latn", "it": "ita_Latn", "pt": "por_Latn", "pl": "pol_Latn",
    "nl": "nld_Latn", "cs": "ces_Latn", "sv": "swe_Latn", "fi": "fin_Latn",
    "hu": "hun_Latn", "ro": "ron_Latn", "tr": "tur_Latn", "ar": "arb_Arab",
    "he": "heb_Hebr", "el": "ell_Grek", "vi": "vie_Latn", "th": "tha_Thai",
    "id": "ind_Latn", "hi": "hin_Deva", "en": "eng_Latn",
}


_nllb_lock = threading.Lock()
_nllb_shared: tuple | None = None


def _nllb_load():
    """Load (or reuse) the shared NLLB model + tokenizer.

    Shared so the overlay pipeline (xx->en) and the compose window (en->xx)
    use one in-memory copy; ctranslate2 handles concurrent translate_batch.
    """
    global _nllb_shared
    with _nllb_lock:
        if _nllb_shared is None:
            import ctranslate2
            import sentencepiece

            if not (_NLLB_MODEL_DIR / "model.bin").exists():
                from huggingface_hub import snapshot_download

                snapshot_download(_NLLB_REPO, local_dir=str(_NLLB_MODEL_DIR))
            translator = ctranslate2.Translator(
                str(_NLLB_MODEL_DIR), device="cpu", compute_type="int8"
            )
            # Raw sentencepiece instead of transformers.AutoTokenizer:
            # identical pieces (NLLB source format is [lang] + pieces + </s>)
            # but ~10s less load time. CTranslate2 works on token strings,
            # so no id offsets.
            sp = sentencepiece.SentencePieceProcessor(
                str(_NLLB_MODEL_DIR / "sentencepiece.bpe.model")
            )
            _nllb_shared = (translator, sp)
        return _nllb_shared


def _nllb_code(lang: str, fallback: str) -> str:
    code = (lang or fallback).lower()
    if code == "auto":
        code = fallback
    flores = _NLLB_CODES.get(code)
    if flores is None:
        raise RuntimeError(
            f"NLLB: unsupported language {lang!r}. "
            f"Known: {', '.join(sorted(_NLLB_CODES))}"
        )
    return flores


class NllbTranslator:
    """NLLB-200 distilled 600M running locally via ctranslate2 (int8, CPU).

    Works in any direction. No auto-detect: "auto" as source falls back to
    Russian so the default config works out of the box.
    """

    def __init__(self, source: str = "auto", target: str = "en"):
        self._src = _nllb_code(source, "ru")
        self._tgt = _nllb_code(target, "en")
        self._translator, self._sp = _nllb_load()
        self._cache: dict[str, str] = {}

    # NLLB is trained on single sentences and tends to drop everything after
    # the first one, so multi-sentence lines are split and rejoined.
    _SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")

    def translate_many(self, texts: list[str]) -> list[str]:
        todo = [t for t in dict.fromkeys(texts) if t.strip() and t not in self._cache]
        if todo:
            pieces: list[str] = []
            spans: list[tuple[int, int]] = []  # (start, count) into pieces
            for t in todo:
                sents = [s for s in self._SENT_SPLIT.split(t) if s.strip()] or [t]
                spans.append((len(pieces), len(sents)))
                pieces.extend(sents)
            sources = [
                [self._src] + self._sp.encode(p, out_type=str) + ["</s>"]
                for p in pieces
            ]
            results = self._translator.translate_batch(
                sources, target_prefix=[[self._tgt]] * len(sources), beam_size=2
            )
            outs = []
            for res in results:
                tokens = [
                    t for t in res.hypotheses[0]
                    if t != self._tgt and t not in ("</s>", "<s>", "<pad>", "<unk>")
                ]
                outs.append(self._sp.decode(tokens).strip())
            for (start, n), t in zip(spans, todo):
                self._cache[t] = " ".join(outs[start:start + n])
        return [self._cache.get(t, t) for t in texts]

    def translate(self, text: str) -> str:
        return self.translate_many([text])[0]


# --------------------------------------------------------------------------
# DeepL (API)
# --------------------------------------------------------------------------
class DeepLTranslator:
    def __init__(self, api_key: str, target: str = "EN-US", source: str = "auto"):
        if not api_key:
            raise RuntimeError("DeepL API key missing.")
        self._key = api_key.strip()
        self._target = target
        self._source = (source or "auto").strip().lower()
        # Free keys end in ":fx" and use the free endpoint.
        host = "api-free.deepl.com" if self._key.endswith(":fx") else "api.deepl.com"
        self._url = f"https://{host}/v2/translate"
        self._cache: dict[str, str] = {}

    def _request(self, texts: list[str]) -> list[str]:
        params = [("text", t) for t in texts]
        # Omitting source_lang makes DeepL auto-detect per text.
        if self._source != "auto":
            params.append(("source_lang", self._source.upper()))
        params.append(("target_lang", self._target))
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=data,
            headers={
                "Authorization": f"DeepL-Auth-Key {self._key}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "RealTimeRussianTranslator/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.load(resp)
        return [tr["text"] for tr in payload["translations"]]

    def translate_many(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        # Serve cached lines; only send the rest to DeepL.
        todo = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if todo:
            try:
                for src, dst in zip(todo, self._request(todo)):
                    self._cache[src] = dst
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:200]
                raise RuntimeError(f"DeepL HTTP {e.code}: {body}") from e
            except urllib.error.URLError as e:
                raise RuntimeError(f"DeepL network error: {e.reason}") from e
        return [self._cache.get(t, t) for t in texts]

    def translate(self, text: str) -> str:
        return self.translate_many([text])[0]


# --------------------------------------------------------------------------
# Ollama (local LLM)
# --------------------------------------------------------------------------
class OllamaTranslator:
    def __init__(self, host: str, model: str, target_language: str = "English"):
        self._url = host.rstrip("/") + "/api/generate"
        self._model = model
        self._to = target_language
        self._cache: dict[str, str] = {}

    def _generate(self, text: str) -> str:
        prompt = (
            f"Translate the following text into {self._to} (detect the source "
            f"language yourself). Reply with ONLY the {self._to} translation, "
            "no notes or quotes.\n\n"
            f"Text: {text}\n{self._to}:"
        )
        body = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",            # keep model resident in VRAM
            "options": {"temperature": 0, "num_predict": 96},
        }).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
        return payload.get("response", "").strip()

    def translate_many(self, texts: list[str]) -> list[str]:
        out = []
        for t in texts:
            if t not in self._cache:
                try:
                    self._cache[t] = self._generate(t)
                except urllib.error.URLError as e:
                    raise RuntimeError(
                        f"Ollama not reachable at {self._url} ({e.reason}). "
                        f"Is 'ollama serve' running?"
                    ) from e
            out.append(self._cache.get(t, t))
        return out

    def translate(self, text: str) -> str:
        return self.translate_many([text])[0]


# --------------------------------------------------------------------------
# Factory / readiness helpers
# --------------------------------------------------------------------------
def _read_user_env_win(name: str) -> str:
    """Read a persisted user environment variable straight from the registry.

    `setx` only updates the env of *future* processes, so a freshly launched app
    can miss a just-set key if its parent (Explorer/shell) hasn't refreshed.
    Reading HKCU\\Environment avoids that inheritance timing problem.
    """
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val or ""
    except (OSError, FileNotFoundError):
        return ""


def _deepl_key(cfg: dict) -> str:
    key = os.environ.get("DEEPL_API_KEY") or cfg.get("deepl_api_key") or ""
    if not key:
        key = _read_user_env_win("DEEPL_API_KEY")
    return key.strip()


def _ollama_ready(cfg: dict) -> bool:
    host = cfg.get("ollama_host", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=3.0) as r:
            return r.status == 200
    except Exception:
        return False


def _source_lang(cfg: dict) -> str:
    return (cfg.get("source_lang", "auto") or "auto").strip().lower()


# Human-readable names for LLM prompts (Ollama) in the compose direction.
_LANG_NAMES = {
    "ru": "Russian", "uk": "Ukrainian", "be": "Belarusian", "bg": "Bulgarian",
    "sr": "Serbian", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
    "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "pl": "Polish", "nl": "Dutch", "cs": "Czech",
    "sv": "Swedish", "fi": "Finnish", "hu": "Hungarian", "ro": "Romanian",
    "tr": "Turkish", "ar": "Arabic", "he": "Hebrew", "el": "Greek",
    "vi": "Vietnamese", "th": "Thai", "id": "Indonesian", "hi": "Hindi",
    "en": "English",
}


def make_reverse_translator(cfg: dict, target: str):
    """English -> `target` language, for the compose (outgoing chat) feature.

    Reuses the configured backend. Argos needs the en->target model installed;
    NLLB handles any of its known codes with the same shared model.
    """
    backend = cfg.get("translator_backend", "argos").lower()
    target = (target or "ru").strip().lower()
    if backend == "deepl":
        return DeepLTranslator(_deepl_key(cfg), target=target.upper(), source="en")
    if backend == "ollama":
        return OllamaTranslator(
            cfg.get("ollama_host", "http://localhost:11434"),
            cfg.get("ollama_model", "translategemma:4b"),
            target_language=_LANG_NAMES.get(target, target),
        )
    if backend == "nllb":
        return NllbTranslator("en", target)
    return ArgosTranslator("en", target)


def make_translator(cfg: dict):
    backend = cfg.get("translator_backend", "argos").lower()
    source = _source_lang(cfg)
    if backend == "deepl":
        return DeepLTranslator(
            _deepl_key(cfg), cfg.get("deepl_target", "EN-US"), source
        )
    if backend == "ollama":
        return OllamaTranslator(
            cfg.get("ollama_host", "http://localhost:11434"),
            cfg.get("ollama_model", "translategemma:4b"),
        )
    if backend == "nllb":
        return NllbTranslator(source)
    return ArgosTranslator("ru" if source == "auto" else source, "en")


def backend_ready(cfg: dict) -> bool:
    backend = cfg.get("translator_backend", "argos").lower()
    if backend == "deepl":
        return bool(_deepl_key(cfg))
    if backend == "ollama":
        return _ollama_ready(cfg)
    if backend == "nllb":
        # find_spec keeps this cheap; the model itself downloads on first use.
        return bool(
            importlib.util.find_spec("ctranslate2")
            and importlib.util.find_spec("sentencepiece")
        )
    source = _source_lang(cfg)
    return is_model_installed("ru" if source == "auto" else source)


def backend_hint(cfg: dict) -> str:
    backend = cfg.get("translator_backend", "argos").lower()
    if backend == "deepl":
        return ("DeepL key missing. Put it in config.json 'deepl_api_key' "
                "or set the DEEPL_API_KEY environment variable.")
    if backend == "ollama":
        model = cfg.get("ollama_model", "translategemma:4b")
        return (f"Ollama not reachable. Run 'ollama serve' and "
                f"'ollama pull {model}'.")
    if backend == "nllb":
        return ("NLLB needs the 'sentencepiece' and 'huggingface_hub' packages "
                "(pip install sentencepiece huggingface_hub). The model "
                "downloads on first use.")
    return "Translation model not installed.\nRun:  python setup_models.py"


def is_model_installed(from_code: str = "ru", to_code: str = "en") -> bool:
    try:
        import argostranslate.translate as t
    except ImportError:
        return False
    langs = {l.code: l for l in t.get_installed_languages()}
    src, dst = langs.get(from_code), langs.get(to_code)
    return bool(src and dst and src.get_translation(dst))
