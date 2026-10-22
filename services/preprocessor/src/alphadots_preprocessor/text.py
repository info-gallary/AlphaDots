"""Text cleanup for untrusted source text.

News and filings are hostile input. Cleaning here removes markup and invisible characters
so that later stages, and eventually an LLM, see plain text. It is not a substitute for
output escaping in the UI or for prompt-injection defences in the research agent.
"""

from __future__ import annotations

import html
import re
import unicodedata

from alphadots_common.ids import sha256_hex

_EXECUTABLE_BLOCK_RE = re.compile(
    r"<(script|style)\b[^<>]{0,500}>.{0,20000}?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^<>]{0,500}>")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^\w]+", re.UNICODE)


def clean_text(text: str) -> str:
    """Return ``text`` as NFKC plain text with markup, control characters and runs of
    whitespace removed.

    Entities are unescaped *before* tags are stripped, so ``&lt;script&gt;`` cannot
    survive as live markup. ``<script>`` and ``<style>`` blocks are removed together with
    their contents, since that content was never prose. Unicode category ``C*`` covers
    control and format characters, including zero-width joiners and bidirectional
    overrides used to disguise text.
    """
    normalized = unicodedata.normalize("NFKC", text)
    unescaped = html.unescape(normalized)
    without_blocks = _EXECUTABLE_BLOCK_RE.sub(" ", unescaped)
    without_tags = _TAG_RE.sub(" ", without_blocks)
    visible = "".join(
        ch if not unicodedata.category(ch).startswith("C") else " " for ch in without_tags
    )
    return _WHITESPACE_RE.sub(" ", visible).strip()


def content_fingerprint(headline: str, body: str | None) -> str:
    """Hash of the cleaned text with case and punctuation removed.

    Two items with the same fingerprint are treated as the same economic event. This is
    exact-duplicate detection after normalisation; near-duplicate and semantic clustering
    are not implemented yet.
    """

    def squash(value: str) -> str:
        return _NON_ALNUM_RE.sub(" ", value.casefold()).strip()

    return sha256_hex(squash(headline), squash(body or ""))
