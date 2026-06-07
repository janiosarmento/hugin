"""Leitura de posts, filtragem e priorização."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import frontmatter

from hugin.state import get_last_processed


@dataclass
class Post:
    path: Path
    metadata: dict
    content: str
    has_tags: bool
    tags: list[str] = field(default_factory=list)
    date: datetime | None = None
    lastmod: datetime | None = None

    @property
    def filename(self) -> str:
        return self.path.name


def _parse_date(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _is_toml_frontmatter(path: Path) -> bool:
    with open(path) as f:
        first_line = f.readline().strip()
    return first_line == "+++"


AGENT_FILES = frozenset({"CLAUDE.md", "AGENTS.md"})


def _maybe_repair_yaml(text: str) -> str | None:
    """Attempt to fix common YAML frontmatter issues (unquoted multi-line values).
    Returns repaired full text or None if not fixable."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm_text = text[3:end]
    body = text[end:]

    def _quote_val(val: str) -> str:
        val = val.strip()
        if not val:
            return ""
        val = val.replace('"', '\\"').replace("'", '"')
        return f'"{val}"\n'

    lines = fm_text.splitlines(keepends=True)
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        matched = None
        for field in ("title", "description"):
            prefix = f"{field}:"
            stripped = line.lstrip()
            if stripped.startswith(prefix):
                # Check char right after key
                rest = stripped[len(prefix):]
                if rest and rest[0] not in (" ", "\t"):
                    matched = None
                else:
                    matched = field
                    key = prefix
                    indent = line[: len(line) - len(line.lstrip())]
                    break
        if matched is None:
            out.append(line)
            i += 1
            continue

        # Collect full value with continuation lines
        parts = [rest.lstrip("\n")]
        while i + 1 < len(lines) and lines[i + 1][0] in (" ", "\t"):
            i += 1
            parts.append(lines[i])
        full = "".join(parts)
        out.append(f"{indent}{key} {_quote_val(full)}")
        i += 1

    return f"---{''.join(out)}{body}"


def load_posts(directory: Path) -> list[Post]:
    posts = []
    for path in sorted(directory.glob("*.md")):
        if path.name in AGENT_FILES:
            continue
        if _is_toml_frontmatter(path):
            print(f"Aviso: {path.name} usa TOML frontmatter, ignorado.")
            continue

        try:
            post = frontmatter.load(str(path))
        except Exception as e:
            # Auto-repair: try quoting title/description and re-parse
            raw = path.read_text()
            repaired = _maybe_repair_yaml(raw)
            if repaired is not None:
                try:
                    post = frontmatter.loads(repaired)
                except Exception:
                    print(f"Aviso: erro ao ler {path.name}: {e}")
                    continue
            else:
                print(f"Aviso: erro ao ler {path.name}: {e}")
                continue

        tags = post.metadata.get("tags", []) or []

        posts.append(Post(
            path=path,
            metadata=post.metadata,
            content=post.content,
            has_tags=bool(tags),
            tags=list(tags),
            date=_parse_date(post.metadata.get("date")),
            lastmod=_parse_date(post.metadata.get("lastmod")),
        ))

    return posts


def prioritize(posts: list[Post], state: dict, include_all: bool = False) -> list[Post]:
    no_tags = []
    edited = []
    up_to_date = []

    for post in posts:
        last_processed = get_last_processed(state, post.filename)

        if not post.has_tags:
            no_tags.append(post)
        elif last_processed is None:
            # Tem tags mas nunca foi processado pelo app
            no_tags.append(post)
        elif post.lastmod and post.lastmod > last_processed:
            edited.append(post)
        else:
            up_to_date.append(post)

    # Sem tags: mais recentes primeiro
    no_tags.sort(key=lambda p: p.date or datetime.min, reverse=True)
    edited.sort(key=lambda p: p.lastmod or datetime.min, reverse=True)

    if include_all:
        return no_tags + edited + up_to_date

    result = no_tags + edited
    if not result:
        return []
    return result


def collect_tag_pool(posts: list[Post]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for post in posts:
        for tag in post.tags:
            counter[tag] += 1
    return dict(counter.most_common())


def format_pool_for_prompt(pool: dict[str, int], limit: int = 100) -> str:
    items = list(pool.items())[:limit]
    return ", ".join(f"{tag} ({count})" for tag, count in items)


def find_duplicate_tags(pool: dict[str, int]) -> list[tuple[str, str, str]]:
    tags = list(pool.keys())
    duplicates = []

    for i, tag_a in enumerate(tags):
        for tag_b in tags[i + 1:]:
            # Levenshtein via SequenceMatcher
            ratio = SequenceMatcher(None, tag_a, tag_b).ratio()
            if ratio >= 0.8 and tag_a != tag_b:
                duplicates.append((tag_a, tag_b, f"similaridade: {ratio:.0%}"))
                continue

            # Prefixo comum
            shorter = min(tag_a, tag_b, key=len)
            longer = max(tag_a, tag_b, key=len)
            if len(shorter) >= 3 and longer.startswith(shorter):
                duplicates.append((tag_a, tag_b, "prefixo comum"))

    return duplicates
