"""How every search box matches: forgiving about spacing, case, accents and small typos.

A query is split into words, and each word has to turn up in one of the card's fields -- name,
number, set, set code, finish, rarity, rules text -- in any order. Inside a field spaces and
punctuation do not count, so "Smugalana", "Smug Alana" and "SmugAlana PL9" all find "Smug Alana"
(PL9), and "SmugAlana Fractured Paradox 1st Ed" also narrows it by set and finish. A word of four
letters or more (without digits) may be one letter off (two from eight letters on) from a word in
the field or its start: "Godess" finds "Goddess".

A query with the syntax only a regular expression uses -- | ^ $ \\ [ ] * + ? { } -- is matched as
before instead: as typed, or read as that expression ("PL9|PL10", "^Ember", "pl(8|9)"), in any
one field. Brackets and dots alone do not count, so "Ember (PL8)" and "Mr. Mime" are words.

Matches are scored -- the name counts for more than the rules text, the whole name for most -- so
searches that show a few results list the best ones first.
"""

import re
import unicodedata
from functools import lru_cache

WORD = re.compile(r"\w+")
REGEX_SYNTAX = re.compile(r"[|^$\\\[\]*+?{}]")
QUERY_MAX = 200

# The weight a field's hit adds to the score.
NAME, NUMBER, SET, DETAIL, TEXT = 4.0, 3.0, 2.0, 1.0, 0.4
FUZZY = 0.6


@lru_cache(maxsize=200_000)
def fold(text):
    """Lower case without accents: "Pokémon" -> "pokemon"."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


@lru_cache(maxsize=200_000)
def field_words(text):
    """The field's words and the field without spaces/punctuation."""
    words = tuple(WORD.findall(fold(text)))
    return words, "".join(words)


def within(word, other, limit):
    """Whether two words are at most `limit` edits apart (Levenshtein, cut off early)."""
    if abs(len(word) - len(other)) > limit:
        return False
    previous = list(range(len(other) + 1))
    for i, char in enumerate(word, 1):
        current = [i]
        for j, other_char in enumerate(other, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char != other_char)))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


@lru_cache(maxsize=400_000)
def near_miss(token, text):
    """Whether the field has a word (or two run together) one letter off the token, two from eight
    letters on -- whole, or as much of its start as was typed. Not for numbers: PL8 is no typo of
    PL9."""
    if len(token) < 4 or any(char.isdigit() for char in token):
        return False
    words = field_words(text)[0]
    limit = 1 if len(token) < 8 else 2
    for word in list(words) + [a + b for a, b in zip(words, words[1:])]:
        if within(token, word, limit) or any(within(token, word[:size], limit) for size in (len(token) - 1, len(token), len(token) + 1) if 0 < size < len(word)):
            return True
    return False


def token_score(token, fields):
    """The weight of the best field the token is in; a near miss counts 0.6 of it. Rules text is
    only searched for the word itself -- long texts have a near miss for almost anything."""
    best = max((weight for text, weight in fields if token in field_words(text)[1]), default=0.0)
    for text, weight in fields:
        if weight > TEXT and weight * FUZZY > best and near_miss(token, text):
            best = weight * FUZZY
    return best


class Query:
    def __init__(self, text):
        self.text = text.strip()
        self.tokens = tuple(dict.fromkeys(WORD.findall(fold(self.text))))
        self.compact = "".join(self.tokens)
        self.pattern = None
        if REGEX_SYNTAX.search(self.text):
            literal = re.escape(self.text)
            try:
                self.pattern = re.compile(f"{literal}|(?:{self.text})" if len(self.text) <= QUERY_MAX else literal, re.I)
            except re.error:
                self.pattern = re.compile(literal, re.I)

    def __bool__(self):
        return bool(self.tokens or self.pattern)

    def score(self, fields):
        """fields: (text, weight) pairs; empty ones are skipped. 0 means no match."""
        fields = [(str(text), weight) for text, weight in fields if text not in (None, "")]
        if self.pattern:
            return max((weight for text, weight in fields if self.pattern.search(text)), default=0.0)
        total = 0.0
        for token in self.tokens:
            best = token_score(token, fields)
            if not best:
                return 0.0
            total += best
        # The whole query as (the start of) the name puts that card first.
        names = [field_words(text)[1] for text, weight in fields if weight == NAME]
        if self.compact in names:
            total += 10
        elif any(name.startswith(self.compact) for name in names):
            total += 5
        return total


@lru_cache(maxsize=256)
def query(text):
    return Query(text)


def score(text, *fields):
    """SQL: search_score(?, text1, weight1, text2, weight2, ...)."""
    return query(text).score(zip(fields[::2], fields[1::2]))


def matches(text, fields):
    return query(text).score(fields) > 0


def card_fields(row, set_name=None, set_code=None, attrs=None):
    """A card row's searchable fields; `attrs` is the printing's attributes (localized name/text)."""
    attrs = attrs or {}
    return (
        (row.get("canonical_name"), NAME), (attrs.get("localizedName"), NAME),
        (row.get("collector_number"), NUMBER),
        (set_name or row.get("set_name"), SET), (set_code or row.get("set_code"), SET),
        (row.get("finish"), DETAIL), (row.get("rarity"), DETAIL), (row.get("language"), DETAIL),
        (row.get("rules_text"), TEXT), (attrs.get("localizedRulesText"), TEXT),
    )


def card_score_sql(parameter="?"):
    """card_fields in SQL, for queries over card_identities i, printings p, variants v, sets s;
    `parameter` is the query's placeholder."""
    return (
        f"search_score({parameter},i.canonical_name,{NAME},json_extract(p.attributes,'$.localizedName'),{NAME},p.collector_number,{NUMBER},"
        f"s.name,{SET},s.code,{SET},v.finish,{DETAIL},p.rarity,{DETAIL},p.language,{DETAIL},"
        f"i.rules_text,{TEXT},json_extract(p.attributes,'$.localizedRulesText'),{TEXT})"
    )


def title_score(title, card):
    """How well a long listing title ("Smug Alana PL9 Fractured Paradox 1st Edition Holo VCard
    NM") describes a card -- the other way round from a search: the card's words are looked for
    in the title. Every word of the name has to be there (a letter off allowed); its number, set,
    set code, finish and rarity add to the score. 0 when the name is not in the title."""
    words, compact = field_words(title)
    present = set(words)
    name_words = field_words(card.get("canonical_name") or "")[0]
    if not name_words:
        return 0.0
    total = 0.0
    for word in name_words:
        if word in compact:
            total += NAME
        elif near_miss(word, title):
            total += NAME * FUZZY
        else:
            return 0.0
    number = field_words(card.get("collector_number") or "")[1]
    if number and (number in present or (len(number) >= 3 and number in compact)):
        total += NUMBER
    set_words = field_words(card.get("set_name") or "")[0]
    if set_words:
        total += SET * sum(word in present for word in set_words) / len(set_words)
    set_code = field_words(card.get("set_code") or "")[1]
    if len(set_code) >= 2 and set_code in present:
        total += SET
    for field in ("finish", "rarity"):
        total += DETAIL * sum(word in present for word in field_words(card.get(field) or "")[0] if word not in ("normal", "standard"))
    return round(total, 2)
