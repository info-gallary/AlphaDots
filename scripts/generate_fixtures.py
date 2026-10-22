"""Deterministically generate the bulk replay fixture and the fixture manifest.

    uv run python -m scripts.generate_fixtures           # rewrite replay.jsonl + MANIFEST.json
    uv run python -m scripts.generate_fixtures --check   # fail if committed files have drifted

Everything produced here is synthetic. Issuers come from the invented instrument master,
headlines come from the templates below, and "sentiment regimes" are a seeded Markov chain
whose only purpose is to give charts some structure. No statistical property of this data
says anything about real markets, and nothing measured on it is evidence of alpha.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from alphadots_common.reference import InstrumentMaster

SEED = 20260303
START = date(2026, 3, 3)
END = date(2026, 3, 13)
LICENSE = "CC0-1.0-synthetic"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"
REPLAY_PATH = Path("news/replay.jsonl")
MANIFEST_NAME = "MANIFEST.json"
SOURCES = ("synthetic_wire_a", "synthetic_wire_b")

# instrument_id -> (name used in headlines, business segment, product)
FLAVOUR: dict[str, tuple[str, str, str]] = {
    "SYN-0001": ("Northwind Semiconductor", "data-center", "its controller chip"),
    "SYN-0002": ("Halcyon Biotherapeutics", "oncology", "its lead therapy"),
    "SYN-0003": ("Meridian Freight Systems", "intermodal", "its routing platform"),
    "SYN-0004": ("Apex Robotics", "warehouse automation", "its picking robot"),
    "SYN-0005": ("Apex Energy Partners", "midstream", "its pipeline expansion"),
    "SYN-0006": ("Cobalt Harbor Bank", "commercial lending", "its digital banking app"),
    "SYN-0007": ("Lumen Grid Utilities", "transmission", "its grid storage project"),
    "SYN-0008": ("Vireo Cloud Software", "subscription", "its analytics suite"),
}
AMBIGUOUS_NAME = {"SYN-0004": "Apex", "SYN-0005": "Apex"}

TEMPLATES: dict[str, tuple[tuple[str, str], ...]] = {
    "positive": (
        ("{name} beats estimates as {segment} revenue grows",
         "Management pointed to strong {segment} demand during the quarter."),
        ("{name} tops estimates on strong {segment} demand",
         "Quarterly results came in ahead of the company's own forecast."),
        ("{name} posts record revenue for the quarter",
         "The company said {segment} growth accelerated."),
        ("{name} raises guidance after robust {segment} orders",
         "The updated outlook reflects an expanded order book."),
        ("{name} wins regulatory approval for {product}",
         "The approval clears the way for a commercial launch."),
        ("{name} launches {product} ahead of schedule",
         "Early customer interest was described as strong."),
        ("{name} secures strategic partnership to expand {segment} business",
         "Terms of the agreement were not disclosed."),
        ("{name} announces buyback as profit improves",
         "The board approved the programme after a strong year."),
    ),
    "negative": (
        ("{name} misses estimates as {segment} revenue declines",
         "Management cited weak {segment} demand during the quarter."),
        ("{name} reports quarterly loss on weak {segment} demand",
         "The shortfall was larger than the company had warned."),
        ("{name} cuts guidance citing {segment} slowdown",
         "The lowered outlook reflects delayed customer orders."),
        ("{name} lowers guidance and warns on costs",
         "Executives pointed to persistent headwinds in {segment}."),
        ("{name} faces regulatory probe over disclosure practices",
         "The company said it is cooperating with the investigation."),
        ("{name} delays {product} launch after testing setback",
         "No new date was given for the delayed release."),
        ("{name} recalls {product} after defect reports",
         "The recall is expected to weigh on quarterly results."),
        ("{name} chief executive resigns amid board investigation",
         "An interim chief executive was named while the board searches for a successor."),
    ),
    "neutral": (
        ("{name} schedules investor day for next month",
         "The event will be webcast for shareholders."),
        ("{name} to present at industry conference",
         "Executives are due to speak on the second day."),
        ("{name} files routine quarterly report",
         "The filing contained no change to previously issued figures."),
        ("{name} names new head of investor relations",
         "The appointment takes effect at the start of next month."),
    ),
    "mixed": (
        ("{name} beats estimates but warns on {segment} costs",
         "Revenue was ahead of forecast while margins came under pressure."),
        ("{name} raises guidance despite regulatory probe",
         "The company reaffirmed its plans while the review continues."),
    ),
}  # fmt: skip

# Probability of (positive, negative, neutral, mixed) given the issuer's daily tone.
POLARITY_WEIGHTS: dict[int, tuple[float, float, float, float]] = {
    1: (0.60, 0.12, 0.20, 0.08),
    0: (0.30, 0.30, 0.30, 0.10),
    -1: (0.12, 0.60, 0.20, 0.08),
}
POLARITIES = ("positive", "negative", "neutral", "mixed")
TONE_PERSISTENCE = 0.65
P_PROVIDER_SYMBOL = 0.75
P_AMBIGUOUS_NAME = 0.12
P_DUPLICATE = 0.10
P_LATE_ARRIVAL = 0.04


@dataclass(frozen=True, slots=True)
class Item:
    source: str
    published_at: datetime
    first_seen_at: datetime
    headline: str
    body: str
    provider_symbols: tuple[str, ...]


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_tone(tone: int, rng: random.Random) -> int:
    if rng.random() < TONE_PERSISTENCE:
        return tone
    return rng.choice([state for state in (-1, 0, 1) if state != tone])


def build_items(master: InstrumentMaster, seed: int = SEED) -> list[Item]:
    rng = random.Random(seed)
    tones = {instrument_id: rng.choice((-1, 0, 1)) for instrument_id in FLAVOUR}
    items: list[Item] = []
    sequence = 0
    day = START
    while day <= END:
        weekend = day.weekday() >= 5
        for instrument_id, (name, segment, product) in FLAVOUR.items():
            tones[instrument_id] = _next_tone(tones[instrument_id], rng)
            instrument = master.get(instrument_id)
            symbol = instrument.symbol_on(day) if instrument is not None else None
            if symbol is None:
                continue
            for _ in range(rng.choice((0, 0, 0, 1) if weekend else (0, 1, 1, 2, 2, 3))):
                sequence += 1
                polarity = rng.choices(POLARITIES, weights=POLARITY_WEIGHTS[tones[instrument_id]])[
                    0
                ]
                headline_template, body_template = rng.choice(TEMPLATES[polarity])
                ambiguous = instrument_id in AMBIGUOUS_NAME and rng.random() < P_AMBIGUOUS_NAME
                shown_name = AMBIGUOUS_NAME[instrument_id] if ambiguous else name
                published = datetime.combine(day, time(11, 0), tzinfo=UTC) + timedelta(
                    seconds=rng.randrange(0, int(10.5 * 3600))
                )
                delay = timedelta(seconds=rng.randint(2, 40))
                if rng.random() < P_LATE_ARRIVAL:
                    delay = timedelta(seconds=rng.randint(45 * 60, 5 * 3600))
                source = rng.choice(SOURCES)
                tagged = not ambiguous and rng.random() < P_PROVIDER_SYMBOL
                # The item number makes each body unique, so only deliberate copies collide.
                body = (
                    body_template.format(name=name, segment=segment, product=product)
                    + f" Synthetic item {sequence:04d}."
                )
                item = Item(
                    source=source,
                    published_at=published,
                    first_seen_at=published + delay,
                    headline=headline_template.format(
                        name=shown_name, segment=segment, product=product
                    ),
                    body=body,
                    provider_symbols=(symbol,) if tagged else (),
                )
                items.append(item)
                if rng.random() < P_DUPLICATE:
                    copy_published = published + timedelta(seconds=rng.randint(60, 480))
                    items.append(
                        Item(
                            source=next(s for s in SOURCES if s != source),
                            published_at=copy_published,
                            first_seen_at=max(item.first_seen_at, copy_published)
                            + timedelta(seconds=rng.randint(5, 60)),
                            headline=item.headline.upper(),
                            body=f"<p>{item.body}</p>",
                            provider_symbols=item.provider_symbols,
                        )
                    )
        day += timedelta(days=1)
    items.sort(key=lambda it: (it.first_seen_at, it.source, it.headline))
    return items


def build_replay_text(master: InstrumentMaster, seed: int = SEED) -> str:
    """The full replay fixture as LF-terminated text."""
    lines = [
        "# AlphaDots bulk replay fixture. SYNTHETIC: generated by scripts/generate_fixtures.py.",
        f"# seed={seed} window={START.isoformat()}..{END.isoformat()} license={LICENSE}",
        "# One JSON object per line, in arrival (first_seen_at) order. Do not edit by hand.",
    ]
    counters = dict.fromkeys(SOURCES, 0)
    for item in build_items(master, seed):
        counters[item.source] += 1
        source_event_id = f"rp-{item.source[-1]}-{counters[item.source]:04d}"
        record = {
            "source": item.source,
            "source_event_id": source_event_id,
            "published_at": _iso(item.published_at),
            "first_seen_at": _iso(item.first_seen_at),
            "headline": item.headline,
            "body": item.body,
            "language": "en",
            "provider_symbols": list(item.provider_symbols),
            "source_url": f"https://wire-{item.source[-1]}.example.invalid/news/{source_event_id}",
            "source_license": LICENSE,
        }
        lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def normalized_sha256(path: Path) -> str:
    """SHA-256 of the file with CRLF folded to LF, so a checkout cannot change the hash."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _record_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    return sum(1 for line in text.splitlines() if line.strip() and not line.startswith("#"))


def build_manifest(fixtures_dir: Path) -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for path in sorted(fixtures_dir.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        entry: dict[str, object] = {"sha256": normalized_sha256(path)}
        if path.suffix == ".jsonl":
            entry["lines"] = _record_count(path)
        files[path.relative_to(fixtures_dir).as_posix()] = entry
    return {
        "description": "Immutable fixture manifest. Hashes are over LF-normalised bytes.",
        "generated_by": "scripts/generate_fixtures.py",
        "replay_seed": SEED,
        "license": LICENSE,
        "synthetic": True,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args()

    master = InstrumentMaster.load(FIXTURES_DIR / "instruments.json")
    replay_file = FIXTURES_DIR / REPLAY_PATH
    manifest_file = FIXTURES_DIR / MANIFEST_NAME
    replay_text = build_replay_text(master)

    if args.check:
        problems = []
        if not replay_file.exists() or replay_file.read_text(encoding="utf-8") != replay_text:
            problems.append(f"{REPLAY_PATH} differs from generator output")
        committed = (
            json.loads(manifest_file.read_text(encoding="utf-8"))
            if manifest_file.exists()
            else None
        )
        if committed != build_manifest(FIXTURES_DIR):
            problems.append(f"{MANIFEST_NAME} does not match the fixture files")
        for problem in problems:
            print(f"DRIFT: {problem}", file=sys.stderr)
        return 1 if problems else 0

    replay_file.parent.mkdir(parents=True, exist_ok=True)
    replay_file.write_text(replay_text, encoding="utf-8", newline="\n")
    manifest_file.write_text(
        json.dumps(build_manifest(FIXTURES_DIR), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {replay_file} ({_record_count(replay_file)} records) and {manifest_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
