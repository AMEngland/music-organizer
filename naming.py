#!/usr/bin/env python3
"""Filename parsing, artist canonicalization, and safe output naming.

Three jobs live here:

* :func:`parse_filename` -- turn a messy filename stem into track/artist/album/
  title fields using an ordered set of patterns, after stripping scene junk.
* :class:`ArtistRegistry` -- fold ``The Beatles`` / ``Beatles`` / ``beatles``
  into one canonical display name so a single artist gets a single folder.
* :func:`safe_component` / :func:`build_filename` -- produce output names that
  are valid on Windows and consistent across the library.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

__all__ = [
    "ParsedName",
    "ArtistRegistry",
    "parse_filename",
    "canonical_artist_key",
    "looks_like_artist",
    "safe_component",
    "build_filename",
    "strip_junk",
    "normalize_title",
    "smart_titlecase",
    "DEFAULT_NAME_EXCEPTIONS",
]

# Names whose casing must survive a title-case pass. Split into the two failure
# modes: names that are legitimately shouted, and names that are legitimately
# quiet. Acronyms without vowels (MGMT, TLC, XTC) are detected automatically and
# do not need listing here.
DEFAULT_NAME_EXCEPTIONS = (
    "ABBA", "AFI", "ATB", "BTS", "CHVRCHES", "EPMD", "HAIM", "INXS", "KISS",
    "KMFDM", "NOFX", "OMD", "SZA", "UB40", "UNKLE", "YACHT", "AC/DC",
    "N.W.A", "R.E.M.", "MF DOOM", "JAY-Z", "OK Go", "LCD Soundsystem",
    "deadmau5", "iamamiwhoami", "will.i.am", "k.d. lang", "fIREHOSE",
    "ZZ Top", "3OH!3", "M83", "P!nk", "Ke$ha", "DJ Shadow", "Sigur Rós",
    "USA", "UFO", "NASA", "AKA", "OK", "EDM", "IDM", "UNI", "OMC", "ELO",
)

# Lowercased inside a title unless they are the first or last word.
_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "onto", "or", "over", "the", "to", "via", "vs", "vs.",
    "with",
}

# Windows-reserved device names; a bare "CON" or "NUL" cannot be a path component.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Words that are never an artist on their own.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "they", "these", "this", "that", "i", "you", "we", "my",
    "your", "his", "her", "it", "is", "as", "be", "no", "so", "if", "up",
    "track", "audio", "untitled", "unknown", "various", "va", "misc",
}

# Trailing parentheticals that are part of the *title*, not junk to discard.
_TITLE_QUALIFIERS = re.compile(
    r"^(live|remix|remaster(ed)?|acoustic|radio edit|edit|demo|instrumental|"
    r"extended|extended mix|version|album version|single version|mix|cover|"
    r"reprise|bonus track|unplugged|feat\.?.*|ft\.?.*|with .*)$",
    re.IGNORECASE,
)

# Bracketed groups containing any of these are removed wholesale.
_JUNK_INSIDE_BRACKETS = re.compile(
    r"(kbps|kbit|\bhq\b|\bhd\b|\b\d{3,4}p\b|www\.|\.com|\.net|\.org|\.ru|\.info|"
    r"official\s*(music\s*)?(video|audio)|lyrics?\s*video|youtube|"
    r"free\s*download|mp3|torrent|rip by|uploaded by)",
    re.IGNORECASE,
)

_BARE_JUNK = re.compile(
    r"(?:\b(?:www\.[^\s\]\)]+|[a-z0-9-]+\.(?:com|net|org|ru|info)(?:/\S*)?)\b"
    r"|\b\d{2,4}\s?kbps\b"
    r"|\bofficial\s*(?:music\s*)?(?:video|audio)\b"
    r"|\blyrics?\s*video\b"
    r"|\byoutube\b"
    r"|\bfree\s*download\b)",
    re.IGNORECASE,
)

_DASH = r"[-‐‑‒–—―]"

# Ordered parse patterns. First match wins, so the more specific shapes lead.
_PATTERNS = [
    (
        "track_artist_album_title",
        re.compile(
            rf"^(?P<track>\d{{1,3}})\s*[.\-_)]?\s+"
            rf"(?P<artist>.+?)\s+{_DASH}\s+(?P<album>.+?)\s+{_DASH}\s+(?P<title>.+)$"
        ),
    ),
    (
        "track_artist_title",
        re.compile(
            rf"^(?P<track>\d{{1,3}})\s*[.\-_)]?\s+(?P<artist>.+?)\s+{_DASH}\s+(?P<title>.+)$"
        ),
    ),
    (
        "artist_album_title",
        re.compile(
            rf"^(?P<artist>.+?)\s+{_DASH}\s+(?P<album>.+?)\s+{_DASH}\s+(?P<title>.+)$"
        ),
    ),
    (
        "artist_title",
        re.compile(rf"^(?P<artist>.+?)\s+{_DASH}\s+(?P<title>.+)$"),
    ),
    (
        "artist_comma_title",
        re.compile(r"^(?P<artist>[^,]{2,60}),\s+(?P<title>.{2,})$"),
    ),
    (
        "track_title",
        re.compile(r"^(?P<track>\d{1,3})\s*[.\-_)]?\s+(?P<title>.+)$"),
    ),
    (
        "artist_dash_title",  # unspaced, single dash: "Nirvana-Lithium"
        re.compile(rf"^(?P<artist>[^{_DASH[1:-1]}]{{2,60}}){_DASH}(?P<title>[^{_DASH[1:-1]}]{{2,}})$"),
    ),
]

_UNDERSCORE_PAIR = re.compile(r"^(?P<artist>[^_]{2,60})_(?P<title>[^_]{2,})$")

_DISC_DIR = re.compile(r"^(cd|disc|disk)\s*[-_]?\s*(?P<number>\d{1,2})$", re.IGNORECASE)


@dataclass
class ParsedName:
    """Result of parsing a filename stem."""

    title: str
    artist: Optional[str] = None
    album: Optional[str] = None
    track: Optional[int] = None
    #: Name of the pattern that matched, for reporting and confidence scoring.
    pattern: str = "title_only"

    @property
    def has_artist(self) -> bool:
        return bool(self.artist)


def strip_junk(text: str) -> str:
    """Remove scene tags, bitrate markers and site URLs from a filename stem."""
    if not text:
        return ""

    # Drop bracketed groups that are clearly junk, keeping "(Live)"-style ones.
    def _drop_bracket(match: "re.Match[str]") -> str:
        return "" if _JUNK_INSIDE_BRACKETS.search(match.group(1)) else match.group(0)

    text = re.sub(r"[\[(]([^\[\]()]*)[\])]", _drop_bracket, text)
    text = _BARE_JUNK.sub(" ", text)

    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .-_–—")
    return text


def normalize_title(text: str) -> str:
    """Tidy a title: collapse whitespace, normalize Unicode, trim separators."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = re.sub(r"\s+", " ", text.replace("_", " "))
    return text.strip(" .-_–—")


def looks_like_artist(candidate: Optional[str]) -> bool:
    """Cheap plausibility test for an artist name parsed out of a filename."""
    if not candidate:
        return False
    candidate = candidate.strip()
    if not 2 <= len(candidate) <= 100:
        return False
    if candidate.isdigit():
        return False
    if candidate.lower() in _STOPWORDS:
        return False
    if candidate.lower().startswith(("http", "www.")):
        return False
    # Must contain at least one letter.
    return any(ch.isalpha() for ch in candidate)


def _looks_like_qualifier(text: str) -> bool:
    """True for trailing fragments like "Live" that belong to the title."""
    return bool(_TITLE_QUALIFIERS.match(text.strip(" ()[]")))


def parse_filename(stem: str) -> ParsedName:
    """Parse a filename stem into track / artist / album / title.

    Patterns are tried most-specific first. An implausible artist candidate
    demotes the result rather than producing a bogus folder.
    """
    original = stem or ""
    working = original

    # Underscore-delimited names carry the separator in an underscore; remember
    # a bare "Artist_Title" pair, then treat underscores as spaces so the dash
    # patterns and junk stripping (which are whitespace-aware) can do their job.
    underscore_match = None
    if "_" in working:
        stripped = strip_junk(working)
        if " " not in stripped:
            underscore_match = _UNDERSCORE_PAIR.match(stripped)
        if working.count("_") >= working.count(" "):
            working = working.replace("_", " ")

    cleaned = re.sub(r"\s+", " ", strip_junk(working)).strip()

    for name, pattern in _PATTERNS:
        match = pattern.match(cleaned)
        if not match:
            continue

        groups = match.groupdict()
        artist = normalize_title(groups.get("artist") or "")
        album = normalize_title(groups.get("album") or "")
        title = normalize_title(groups.get("title") or "")
        track = int(groups["track"]) if groups.get("track") else None

        # "Artist - Song - Live" is a two-part name with a qualifier, not a
        # three-part Artist/Album/Title name.
        if name == "artist_album_title" and _looks_like_qualifier(title):
            album, title = "", f"{album} ({title})"
            name = "artist_title"

        if artist and not looks_like_artist(artist):
            if track is None and re.fullmatch(r"\d{1,3}", artist):
                # "1 - First" is a track number, not an artist named "1".
                track, artist, name = int(artist), "", "track_title"
                if album:
                    artist, album, name = album, "", "track_artist_title"
                    if not looks_like_artist(artist):
                        artist, name = "", "track_title"
            else:
                # Bad artist guess: keep the whole thing as a title.
                title = normalize_title(cleaned)
                artist = album = ""
                name = "title_only"

        if not title:
            continue

        return ParsedName(
            title=title,
            artist=artist or None,
            album=album or None,
            track=track,
            pattern=name,
        )

    if underscore_match:
        artist = normalize_title(underscore_match.group("artist"))
        title = normalize_title(underscore_match.group("title"))
        if looks_like_artist(artist) and title:
            return ParsedName(title=title, artist=artist, pattern="artist_underscore_title")

    return ParsedName(title=normalize_title(cleaned) or normalize_title(original) or "Unknown")


def _looks_like_acronym(word: str) -> bool:
    """True for shouted names that should stay shouted (``MGMT``, ``BTS``)."""
    core = re.sub(r"[^A-Za-z]", "", word)
    if not core or not word.isupper():
        return False
    # "THE", "OF" and friends are shouted grammar, not acronyms.
    if word.casefold().strip(".'") in _SMALL_WORDS:
        return False
    # Vowel-free runs are acronyms (MGMT, TLC, BBC, AC, DC). Length alone is not
    # a safe test: it would freeze ordinary short words such as ME, IT and US.
    # Vowel-bearing acronyms (ABBA, AFI, USA) are handled by the exception list.
    return not re.search(r"[AEIOUY]", core)


def _capitalize_word(word: str) -> str:
    """Capitalize one word, respecting hyphens, apostrophes and ``Mc`` names."""

    def fix(match: "re.Match[str]") -> str:
        part = match.group(0)
        if _looks_like_acronym(part):
            return part
        lowered = part.casefold()
        result = lowered[:1].upper() + lowered[1:]
        result = re.sub(r"^(Mc)([a-z])", lambda m: m.group(1) + m.group(2).upper(), result)
        result = re.sub(r"^(O')([a-z])", lambda m: m.group(1) + m.group(2).upper(), result)
        return result

    return re.sub(r"[A-Za-z0-9']+", fix, word)


def smart_titlecase(name: Optional[str], exceptions: Optional[Iterable[str]] = None) -> str:
    """Re-case a shouted or whispered name without mangling deliberate styling.

    Only strings that are entirely upper or entirely lower case are rewritten --
    a name that already carries mixed case was styled on purpose and is left
    alone. Exceptions are matched whole-string and per word, so ``ABBA GOLD``
    becomes ``ABBA Gold`` rather than ``Abba Gold``.
    """
    if not name:
        return name or ""

    lookup = {value.casefold(): value for value in DEFAULT_NAME_EXCEPTIONS}
    if exceptions:
        lookup.update({value.casefold(): value for value in exceptions if value})

    text = name.strip()
    whole = lookup.get(text.casefold())
    if whole:
        return whole

    if not (text.isupper() or text.islower()):
        return name

    tokens = re.split(r"(\s+)", text)
    words = [index for index, token in enumerate(tokens) if token and not token.isspace()]
    if not words:
        return name

    first, last = words[0], words[-1]
    rebuilt: List[str] = []

    for index, token in enumerate(tokens):
        if not token or token.isspace():
            rebuilt.append(token)
            continue

        override = lookup.get(token.casefold())
        if override:
            rebuilt.append(override)
        elif _looks_like_acronym(token):
            rebuilt.append(token)
        elif token.casefold() in _SMALL_WORDS and index not in (first, last):
            rebuilt.append(token.casefold())
        else:
            rebuilt.append(_capitalize_word(token))

    return "".join(rebuilt)


def canonical_artist_key(name: str) -> str:
    """Fold an artist name to a comparison key.

    ``The Beatles``, ``beatles``, ``Beatles, The`` and ``Béatles`` all collapse
    to the same key, so they share one folder.
    """
    if not name:
        return ""

    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().strip()

    # "Beatles, The" -> "the beatles"
    trailing = re.match(r"^(?P<body>.+),\s*(the|a|an)$", text)
    if trailing:
        text = trailing.group("body")

    text = re.sub(r"^(the|a|an)\s+", "", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


class ArtistRegistry:
    """Collects artist name variants and elects one display form per artist.

    Election is ordered: a spelling seen in embedded tags beats one inferred
    from a path or filename; among equal sources a properly cased spelling
    beats ALL CAPS or all lowercase; only then does frequency decide. Frequency
    is deliberately last, so one well-tagged file outvotes a pile of sloppily
    named ones.
    """

    _SOURCE_WEIGHT = {"tags": 3, "path": 2, "filename": 1}

    def __init__(self, titlecase: bool = False,
                 exceptions: Optional[Iterable[str]] = None) -> None:
        self._counts: Dict[str, Counter] = defaultdict(Counter)
        self._best_source: Dict[str, Dict[str, int]] = defaultdict(dict)
        self._resolved: Dict[str, str] = {}
        self._titlecase = titlecase
        self._exceptions = tuple(exceptions or ())

    def register(self, name: Optional[str], source: str = "filename") -> Optional[str]:
        """Record a spelling of an artist name. Returns its canonical key."""
        if not name:
            return None
        key = canonical_artist_key(name)
        if not key:
            return None
        display = normalize_title(name)
        if display:
            weight = self._SOURCE_WEIGHT.get(source, 1)
            self._counts[key][display] += 1
            previous = self._best_source[key].get(display, 0)
            self._best_source[key][display] = max(previous, weight)
            self._resolved.pop(key, None)
        return key

    def resolve(self, name: Optional[str]) -> Optional[str]:
        """Return the elected display spelling for *name*."""
        if not name:
            return None
        key = canonical_artist_key(name)
        if not key:
            return None

        if key not in self._resolved:
            variants = self._counts.get(key)
            if not variants:
                return self._present(normalize_title(name))
            sources = self._best_source[key]
            elected = max(
                variants,
                key=lambda display: (
                    sources.get(display, 0),
                    self._case_score(display),
                    variants[display],
                    display,
                ),
            )
            self._resolved[key] = self._present(elected)
        return self._resolved[key]

    def _present(self, display: str) -> str:
        """Apply the optional title-case pass to an elected spelling."""
        if not self._titlecase:
            return display
        return smart_titlecase(display, self._exceptions)

    @staticmethod
    def _case_score(text: str) -> int:
        """Prefer Mixed Case over ALL CAPS and all lowercase."""
        return 0 if (text.isupper() or text.islower()) else 1

    def merged_groups(self) -> Dict[str, Counter]:
        """Artist keys that saw more than one spelling, for reporting."""
        return {key: counts for key, counts in self._counts.items() if len(counts) > 1}


def safe_component(name: Optional[str], max_length: int = 100) -> str:
    """Make *name* usable as a single path component on Windows and POSIX."""
    if not name:
        return "Unknown"

    text = unicodedata.normalize("NFC", str(name))
    text = _ILLEGAL_CHARS.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(". ")

    if len(text) > max_length:
        cut = text[:max_length]
        # Prefer breaking on a word boundary when one is reasonably close.
        space = cut.rfind(" ")
        if space > max_length * 0.6:
            cut = cut[:space]
        text = cut.rstrip(". ")

    if text.upper() in _RESERVED_NAMES:
        text = f"{text}_"

    return text or "Unknown"


def build_filename(
    title: str,
    extension: str,
    track: Optional[int] = None,
    disc: Optional[int] = None,
    artist: Optional[str] = None,
    template: str = "{track} - {title}",
    template_without_track: str = "{title}",
    max_length: int = 100,
) -> str:
    """Render a normalized output filename.

    Track numbers are zero-padded so players sort 1, 2, ... 10 correctly, and a
    disc prefix is added only for multi-disc releases.
    """
    title = normalize_title(title) or "Unknown"
    fields = {
        "title": title,
        "artist": normalize_title(artist or ""),
        "track": f"{track:02d}" if track else "",
        "disc": str(disc) if disc else "",
    }

    chosen = template if track else template_without_track
    try:
        stem = chosen.format(**fields)
    except (KeyError, IndexError, ValueError):
        stem = fields["title"]

    if disc and disc > 1:
        stem = f"{disc}-{stem}"

    stem = re.sub(r"\s+", " ", stem).strip(" -_")
    extension = extension if extension.startswith(".") else f".{extension}"
    return safe_component(stem, max_length) + extension.lower()


def disc_number_from_dirname(name: str) -> Optional[int]:
    """Return the disc number if *name* is a ``CD1``/``Disc 2`` style folder."""
    match = _DISC_DIR.match(name.strip())
    return int(match.group("number")) if match else None
