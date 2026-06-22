"""Translation backends (Russian -> English), selectable via config.

Backends:
  - "argos": fully offline (Argos Translate / ctranslate2). No internet.
  - "deepl": DeepL API. Higher quality; needs internet + an auth key. The free
    tier uses api-free.deepl.com (keys end in ":fx"); pro uses api.deepl.com.

Both expose translate(text) and translate_many(texts). The pipeline uses
translate_many so a whole snapshot is one network round-trip on DeepL.
"""
from __future__ import annotations

import functools
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


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
# DeepL (API)
# --------------------------------------------------------------------------
class DeepLTranslator:
    def __init__(self, api_key: str, target: str = "EN-US"):
        if not api_key:
            raise RuntimeError("DeepL API key missing.")
        self._key = api_key.strip()
        self._target = target
        # Free keys end in ":fx" and use the free endpoint.
        host = "api-free.deepl.com" if self._key.endswith(":fx") else "api.deepl.com"
        self._url = f"https://{host}/v2/translate"
        self._cache: dict[str, str] = {}

    def _request(self, texts: list[str]) -> list[str]:
        params = [("text", t) for t in texts]
        params += [("source_lang", "RU"), ("target_lang", self._target)]
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
    def __init__(self, host: str, model: str):
        self._url = host.rstrip("/") + "/api/generate"
        self._model = model
        self._cache: dict[str, str] = {}

    def _generate(self, text: str) -> str:
        prompt = (
            "Translate the following Russian text to English. "
            "Reply with ONLY the English translation, no notes or quotes.\n\n"
            f"Russian: {text}\nEnglish:"
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


def make_translator(cfg: dict):
    backend = cfg.get("translator_backend", "argos").lower()
    if backend == "deepl":
        return DeepLTranslator(_deepl_key(cfg), cfg.get("deepl_target", "EN-US"))
    if backend == "ollama":
        return OllamaTranslator(
            cfg.get("ollama_host", "http://localhost:11434"),
            cfg.get("ollama_model", "translategemma:4b"),
        )
    return ArgosTranslator("ru", "en")


def backend_ready(cfg: dict) -> bool:
    backend = cfg.get("translator_backend", "argos").lower()
    if backend == "deepl":
        return bool(_deepl_key(cfg))
    if backend == "ollama":
        return _ollama_ready(cfg)
    return is_model_installed()


def backend_hint(cfg: dict) -> str:
    backend = cfg.get("translator_backend", "argos").lower()
    if backend == "deepl":
        return ("DeepL key missing. Put it in config.json 'deepl_api_key' "
                "or set the DEEPL_API_KEY environment variable.")
    if backend == "ollama":
        model = cfg.get("ollama_model", "translategemma:4b")
        return (f"Ollama not reachable. Run 'ollama serve' and "
                f"'ollama pull {model}'.")
    return "Translation model not installed.\nRun:  python setup_models.py"


def is_model_installed(from_code: str = "ru", to_code: str = "en") -> bool:
    try:
        import argostranslate.translate as t
    except ImportError:
        return False
    langs = {l.code: l for l in t.get_installed_languages()}
    src, dst = langs.get(from_code), langs.get(to_code)
    return bool(src and dst and src.get_translation(dst))
