"""MongoDB Search index definition, query shape, reconciliation, and health.

Lexical retrieval indexes text twice: once with a standard language analyzer for
ordinary prose, and once with a whitespace analyzer that keeps technical
identifiers such as `wallet:read` or `/api/v1/payments` intact.

The analyzer, field mapping, and query shape here are shared by every lexically
searchable collection, so one vocabulary governs them all. Each collection's own
index definition lives with that collection.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.db.search_indexes import (
    SKILL_CHUNK_COLLECTION,
    SearchIndexAction,
    describe_search_index,
    reconcile_search_index,
    text_value,
)
from app.skills.types import SkillVersionKey

SKILL_CHUNK_TEXT_INDEX: Final = "skill_chunk_text_index"
TEXT_INDEX_TYPE: Final = "search"
CONTENT_PATH: Final = "content"
SECTION_PATH: Final = "section"
FILTER_PATHS: Final[tuple[str, ...]] = ("org_id", "_id", "skill_id", "skill_version")
STANDARD_ANALYZER: Final = "lucene.standard"
IDENTIFIER_ANALYZER: Final = "identifier_exact"
EXACT_MULTI: Final = "exact"

# Starting relevance weights. An exact identifier match is the strongest signal
# lexical search has, then an exact phrase. These are deliberate starting points
# for retrieval evaluation, not optimal values.
IDENTIFIER_BOOST: Final = 3.0
PHRASE_BOOST: Final = 2.0
CONTENT_BOOST: Final = 1.0
SECTION_BOOST: Final = 0.5

TextIndexAction = SearchIndexAction


def identifier_analyzer_definition() -> MongoDocument:
    """Build the analyzer that keeps punctuated identifiers whole."""
    return {
        "name": IDENTIFIER_ANALYZER,
        "charFilters": [],
        "tokenizer": {"type": "whitespace"},
        "tokenFilters": [{"type": "lowercase"}],
    }


def searchable_text_field(*, exact: bool = False) -> MongoDocument:
    """Build one mapped string field, optionally with the exact multi-field."""
    field: MongoDocument = {"type": "string", "analyzer": STANDARD_ANALYZER}
    if exact:
        field["multi"] = {EXACT_MULTI: {"type": "string", "analyzer": IDENTIFIER_ANALYZER}}
    return field


def skill_chunk_text_definition() -> MongoDocument:
    """Build the skill chunk Search index definition.

    `dynamic: false` keeps vectors and internal fields out of the text index;
    only the fields lexical retrieval actually queries or filters are mapped.
    """
    return {
        "analyzers": [identifier_analyzer_definition()],
        "mappings": {
            "dynamic": False,
            "fields": {
                CONTENT_PATH: searchable_text_field(exact=True),
                SECTION_PATH: searchable_text_field(),
                "org_id": {"type": "token"},
                "_id": {"type": "objectId"},
                "skill_id": {"type": "token"},
                "skill_version": {"type": "token"},
            },
        },
    }


def compound_text_query(
    query: str,
    *,
    content_path: str = CONTENT_PATH,
    secondary: Sequence[tuple[str, float]] = (),
    phrase: bool = False,
) -> MongoDocument:
    """Build the compound operator for one lexical query.

    The main content field is matched three ways — as prose, as exact
    identifiers, and as a phrase — while each secondary field contributes a
    plain match at its own weight. The query text reaches the analyzers as
    written apart from whitespace normalization, so casing and punctuation
    inside identifiers survive.
    """
    normalized = " ".join(query.split())
    if not normalized:
        raise ValueError("search query must not be empty")
    phrase_clause: MongoDocument = {
        "phrase": {
            "query": normalized,
            "path": content_path,
            "score": {"boost": {"value": PHRASE_BOOST}},
        }
    }
    should: list[MongoDocument] = [
        {
            "text": {
                "query": normalized,
                "path": content_path,
                "score": {"boost": {"value": CONTENT_BOOST}},
            }
        },
        {
            "text": {
                "query": normalized,
                "path": {"value": content_path, "multi": EXACT_MULTI},
                "score": {"boost": {"value": IDENTIFIER_BOOST}},
            }
        },
    ]
    should.extend(
        {
            "text": {
                "query": normalized,
                "path": path,
                "score": {"boost": {"value": boost}},
            }
        }
        for path, boost in secondary
    )
    compound: MongoDocument = {"should": should, "minimumShouldMatch": 1}
    if phrase:
        compound["must"] = [phrase_clause]
    else:
        should.append(phrase_clause)
    return compound


def skill_chunk_text_query(query: str, *, phrase: bool = False) -> MongoDocument:
    """Build the compound operator for one lexical skill chunk query."""
    return compound_text_query(
        query,
        content_path=CONTENT_PATH,
        secondary=((SECTION_PATH, SECTION_BOOST),),
        phrase=phrase,
    )


def version_filter_clauses(skill_versions: Sequence[SkillVersionKey]) -> list[MongoDocument]:
    """Build non-scoring filter clauses that scope a query to exact versions."""
    if not skill_versions:
        return []
    clauses: list[MongoDocument] = [
        {
            "compound": {
                "must": [
                    {"equals": {"path": "skill_id", "value": key.skill_id}},
                    {"equals": {"path": "skill_version", "value": key.version}},
                ]
            }
        }
        for key in skill_versions
    ]
    if len(clauses) == 1:
        return clauses
    return [{"compound": {"should": clauses, "minimumShouldMatch": 1}}]


@dataclass(frozen=True, slots=True)
class TextIndexStatus:
    """Credential-free state of the Search index, safe to return to clients."""

    name: str
    exists: bool
    queryable: bool
    status: str | None = None
    analyzer: str | None = None

    @property
    def ready(self) -> bool:
        """Report whether the index can currently serve a lexical query."""
        return self.exists and self.queryable


@dataclass(frozen=True, slots=True)
class TextSearchHealth:
    """Lexical search readiness without leaking driver internals."""

    available: bool
    index: TextIndexStatus | None = None
    detail: str | None = None

    @property
    def ready(self) -> bool:
        """Report whether lexical search can serve a query."""
        return self.available and self.index is not None and self.index.ready


async def describe_text_index(
    database: AsyncDatabase[MongoDocument],
    name: str = SKILL_CHUNK_TEXT_INDEX,
) -> TextIndexStatus:
    """Read the Search index's state through `mongod`."""
    state = await describe_search_index(database, SKILL_CHUNK_COLLECTION, name)
    content = _content_field(state.definition)
    return TextIndexStatus(
        name=state.name,
        exists=state.exists,
        queryable=state.queryable,
        status=state.status,
        analyzer=text_value(content.get("analyzer")) if content else None,
    )


async def ensure_text_indexes(database: AsyncDatabase[MongoDocument]) -> TextIndexAction:
    """Create or reconcile the skill chunk Search index.

    Safe to repeat on every startup: an identical index is left untouched and a
    changed analyzer or field mapping is applied in place.
    """
    definition = skill_chunk_text_definition()
    existing = await describe_search_index(
        database,
        SKILL_CHUNK_COLLECTION,
        SKILL_CHUNK_TEXT_INDEX,
    )
    return await reconcile_search_index(
        database,
        SKILL_CHUNK_COLLECTION,
        name=SKILL_CHUNK_TEXT_INDEX,
        index_type=TEXT_INDEX_TYPE,
        definition=definition,
        matches=text_definition_matches(existing.definition, definition),
        existing=existing,
    )


async def check_text_search_health(
    database: AsyncDatabase[MongoDocument],
) -> TextSearchHealth:
    """Evaluate lexical search readiness without raising or leaking internals."""
    try:
        index = await describe_text_index(database)
    except SearchUnavailableError as error:
        return TextSearchHealth(available=False, detail=type(error).__name__)
    return TextSearchHealth(available=True, index=index)


def _mapped_fields(definition: Mapping[str, object] | None) -> Mapping[str, object]:
    if definition is None:
        return {}
    mappings = definition.get("mappings")
    if not isinstance(mappings, Mapping):
        return {}
    fields = mappings.get("fields")
    return fields if isinstance(fields, Mapping) else {}


def _content_field(definition: Mapping[str, object] | None) -> Mapping[str, object] | None:
    content = _mapped_fields(definition).get(CONTENT_PATH)
    return content if isinstance(content, Mapping) else None


def content_analyzer(definition: Mapping[str, object] | None) -> str | None:
    """Return the analyzer applied to the main content field, if any."""
    content = _content_field(definition)
    return text_value(content.get("analyzer")) if content else None


def text_definition_matches(
    current: Mapping[str, object] | None,
    expected: MongoDocument,
) -> bool:
    """Compare only the parts of a definition this application controls."""
    if current is None:
        return False
    return _comparable(current) == _comparable(expected)


def _comparable(definition: Mapping[str, object]) -> tuple[object, ...]:
    mappings = definition.get("mappings")
    dynamic = mappings.get("dynamic") if isinstance(mappings, Mapping) else None
    fields = tuple(
        (
            path,
            text_value(field.get("type")),
            text_value(field.get("analyzer")),
            _multi_analyzers(field),
        )
        for path, field in sorted(_mapped_fields(definition).items())
        if isinstance(field, Mapping)
    )
    analyzers = definition.get("analyzers")
    analyzer_names = tuple(
        sorted(
            str(analyzer.get("name"))
            for analyzer in (analyzers if isinstance(analyzers, list) else [])
            if isinstance(analyzer, Mapping)
        )
    )
    return (bool(dynamic), fields, analyzer_names)


def _multi_analyzers(field: Mapping[str, object]) -> tuple[tuple[str, str | None], ...]:
    multi = field.get("multi")
    if not isinstance(multi, Mapping):
        return ()
    return tuple(
        sorted(
            (name, text_value(value.get("analyzer")) if isinstance(value, Mapping) else None)
            for name, value in multi.items()
        )
    )
