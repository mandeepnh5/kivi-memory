"""Contextual judge for ambiguous or conflicting matches.

The model is consulted, never trusted: it returns per-span decisions as JSON
and never produces text, so it cannot rewrite anything outside the approved
spans. The decision shape is validated in the pipeline, not assumed. Any
failure (missing key, refusal, timeout, bad payload) degrades that request to
deterministic-only.

One backend, spoken to over plain HTTP: an OpenAI-compatible chat endpoint.

  GROQ_API_KEY [+ LLM_BASE_URL, LLM_MODEL]

Defaults are Groq's free tier, which is what this project was built and
measured against:

  LLM_BASE_URL = https://api.groq.com/openai/v1
  LLM_MODEL    = openai/gpt-oss-120b

Anything else speaking the same protocol (a local Ollama, OpenAI itself)
works by setting those two variables. `LLM_API_KEY` is accepted in place of
`GROQ_API_KEY`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import (JUDGE_ATTEMPTS, JUDGE_BACKOFF_S, JUDGE_TIMEOUT_S,
                     PRICING_PER_MTOK)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"

_SYSTEM = """You judge whether possibly-misheard words in a dictated sentence \
refer to the user's personal vocabulary. For each numbered span you receive \
the text as transcribed and the personal term(s) it phonetically matches, \
with a note about what each term is. Decide per span:
- apply=true with the chosen preferred if the sentence context indicates the \
personal term (a person, product, or organisation the user knows);
- apply=false if the word is used in its ordinary meaning (e.g. the fruit \
"kiwi"), or the context is genuinely unclear. When unsure, do not apply.
Judge each span by its own role in its clause, not by the sentence's \
overall topic. A span noted as coexisting with the term's exact spelling \
elsewhere in the sentence is usually meant as a different word; apply=false \
there unless its own clause clearly means the personal term.
A span that sits inside a longer proper name - followed or preceded by \
another capitalised word, or by a number, as in "Aditya Sharma", \
"Aditya Birla Group", "Aditya 369" - is almost always a different person, \
organisation, place or title that merely shares a first name. Answer \
apply=false for those, even though the first name matches the user's term \
exactly. Rewriting a stranger's name is the worst error you can make here.
Give a one-sentence reason. Decide every span you are given, nothing else.
Respond with JSON only: {"decisions": [{"index": ..., "apply": ..., \
"preferred": ..., "reason": ...}, ...]}"""


@dataclass
class JudgeResult:
    decisions: dict[int, dict]     # index -> {apply, preferred, reason}
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class LLMError(Exception):
    pass


def _prompt(sentence: str, items: list[dict]) -> str:
    lines = [f"Sentence: {json.dumps(sentence, ensure_ascii=False)}", "", "Spans:"]
    for it in items:
        opts = "; ".join(
            f"{o['preferred']} ({o.get('kind') or 'term'}"
            + (f": {o['note']}" if o.get("note") else "") + ")"
            for o in it["options"]
        )
        line = f'{it["index"]}. transcribed "{it["text"]}" - matches: {opts}'
        if it.get("hint"):
            line += f' - note: {it["hint"]}'
        lines.append(line)
    return "\n".join(lines)


def _parse_decisions(text: str) -> dict[int, dict]:
    # One general extractor for every wrapper a model might add (fences,
    # prose preamble): take the outermost JSON object unconditionally.
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    payload = json.loads(text[start:end + 1])
    # json_object mode guarantees JSON, not our shape - coerce the index so
    # '"index": "0"' does not silently orphan a span.
    return {int(d["index"]): d for d in payload["decisions"]}


class Judge:
    """The judge. Without credentials it reports itself unavailable, and the
    pipeline leaves every ambiguous span alone."""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled

    @staticmethod
    def _key() -> str | None:
        # A host that injects an unset variable gives us "" or "   ", and a
        # blank key is no key: treating it as one turns every ambiguous span
        # into two real HTTP round trips that 401, instead of the documented
        # "no credentials, leave it alone".
        for var in ("GROQ_API_KEY", "LLM_API_KEY"):
            value = (os.environ.get(var) or "").strip()
            if value:
                return value
        return None

    @property
    def available(self) -> bool:
        return self._enabled and self._key() is not None

    def judge(self, sentence: str, items: list[dict]) -> JudgeResult:
        """items: [{index, text, options: [{preferred, kind, note}], hint?}]"""
        if not self.available:
            raise LLMError("no LLM credentials configured")

        base = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        body = {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _prompt(sentence, items)},
            ],
        }
        try:
            request = urllib.request.Request(
                f"{base}/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self._key()}",
                         # some gateways (Cloudflare) reject urllib's default UA
                         "User-Agent": "kivi-memory/1.0"},
            )
        except ValueError as exc:   # LLM_BASE_URL is user-set and may be junk
            raise LLMError(f"bad endpoint URL: {exc}") from exc

        # Free-tier endpoints rate-limit (429) under bursts like an eval run,
        # so one short retry is worth it. Both the timeout and the retry count
        # are bounded on purpose: a dictation cannot wait, and a call that
        # does not answer in time simply leaves the span alone.
        data = None
        for attempt in range(JUDGE_ATTEMPTS):
            try:
                with urllib.request.urlopen(request, timeout=JUDGE_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503) and attempt < JUDGE_ATTEMPTS - 1:
                    time.sleep(JUDGE_BACKOFF_S)
                    continue
                raise LLMError(str(exc)) from exc
            except Exception as exc:
                raise LLMError(str(exc)) from exc

        try:
            decisions = _parse_decisions(data["choices"][0]["message"]["content"])
            usage = data.get("usage", {})
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMError(f"unparseable response: {exc}") from exc

        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
        # priced only when the model is in the table; the free tier is 0, and
        # an unknown endpoint reports 0 rather than inventing a number
        in_price, out_price = PRICING_PER_MTOK.get(model, (0.0, 0.0))
        cost = round((in_tok * in_price + out_tok * out_price) / 1e6, 6)
        return JudgeResult(decisions, model, in_tok, out_tok, cost)
