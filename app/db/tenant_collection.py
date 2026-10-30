"""One fail-closed driver boundary for every served domain collection.

Maintenance/catalog repositories deliberately use the raw database. Domain
repositories get this adapter, including auxiliary version/chunk collections;
no direct-driver query can omit its immutable tenant/resource envelope.
"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any, cast

from pymongo.asynchronous.collection import AsyncCollection

from app.db.client_types import MongoDocument
from app.security.policy.hooks import fenced
from app.security.tenant import TenantContext, TenantContextRequired, current_tenant, require_tenant
from app.security.tenant_policy import current_action, current_policy, policy_allows, policy_filter

MAX_POLICY_CANDIDATE_IDS = 10_000
_IMMUTABLE = {
    "org_id",
    "identity_version",
    "legacy_adoption_id",
    "_id",
    "resource_project_ids",
    "initiating_authority",
    "policy_hash",
}


class UnsupportedSearchPredicate(ValueError):
    """A resource predicate requires an authorized-ID preselection."""


def lexical_predicate(query: Mapping[str, Any]) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = []
    for field, value in query.items():
        if field in {"$and", "$or"}:
            clauses.append(
                {
                    "compound": {
                        "filter" if field == "$and" else "should": [
                            lexical_predicate(v) for v in value
                        ],
                        **({"minimumShouldMatch": 1} if field == "$or" else {}),
                    }
                }
            )
        elif field.startswith("$"):
            raise UnsupportedSearchPredicate("unsupported policy query operator")
        elif isinstance(value, Mapping):
            if value == {"$gt": ""}:
                clauses.append({"range": {"path": field, "gt": ""}})
            elif set(value) != {"$in"}:
                raise UnsupportedSearchPredicate("complex policy requires ID preselection")
            elif not value["$in"]:
                clauses.append({"compound": {"mustNot": [{"exists": {"path": "org_id"}}]}})
            else:
                clauses.append({"in": {"path": field, "value": list(value["$in"])}})
        elif value is None:
            clauses.append(
                {
                    "compound": {
                        "should": [
                            {"equals": {"path": field, "value": None}},
                            {"compound": {"mustNot": [{"exists": {"path": field}}]}},
                        ],
                        "minimumShouldMatch": 1,
                    }
                }
            )
        else:
            clauses.append({"equals": {"path": field, "value": value}})
    return clauses[0] if len(clauses) == 1 else {"compound": {"filter": clauses}}


def _search_identifier_types(value: Any) -> Any:
    # field_filter uses $type:string for ALL resource IDs. Strict served
    # Identifier fields are nonempty strings: this equivalent string range is
    # supported inside both mongot filters, without enumerating corpus IDs.
    if isinstance(value, Mapping):
        if value == {"$type": "string"}:
            return {"$gt": ""}
        return {key: _search_identifier_types(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_search_identifier_types(item) for item in value]
    return value


def _without_empty_in(
    query: Mapping[str, Any], non_null_strings: frozenset[str] = frozenset()
) -> dict[str, Any] | None:
    """Preserve AND/OR truth while removing mongot's forbidden empty INs.

    None means false, {} means true. A denied private-memory branch must not
    force an otherwise expressible shared-memory policy into ID preselection.
    """
    clauses: list[dict[str, Any]] = []
    for field, value in query.items():
        if field in {"$and", "$or"}:
            branches = [_without_empty_in(item, non_null_strings) for item in value]
            if field == "$and" and any(item is None for item in branches):
                return None
            live = [item for item in branches if item is not None]
            if field == "$or":
                if not live:
                    return None
                if any(not item for item in live):
                    continue
            else:
                live = [item for item in live if item]
                if not live:
                    continue
            clauses.append(live[0] if len(live) == 1 else {field: live})
        elif isinstance(value, Mapping) and value.get("$in") == []:
            return None
        elif value is None and field in non_null_strings:
            # Strict served models cannot contain null in these fields. Old
            # generic legacy predicates sometimes include an impossible null
            # project-context branch alongside an empty project selector.
            return None
        else:
            clauses.append({field: value})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses} if clauses else {}


class TenantCursor:
    """A lazy cursor retaining the guard through iteration and projection."""

    def __init__(self, factory: Any, owner: Any, projection: Any = None) -> None:
        self.factory, self.owner, self.projection = factory, owner, projection
        self.operations: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.cursor: Any = None

    def sort(self, *args: Any, **kwargs: Any) -> "TenantCursor":
        self.operations.append(("sort", args, kwargs))
        return self

    def limit(self, *args: Any, **kwargs: Any) -> "TenantCursor":
        self.operations.append(("limit", args, kwargs))
        return self

    async def _open(self) -> Any:
        if self.cursor is None:
            self.cursor = await self.factory()
            for method, args, kwargs in self.operations:
                self.cursor = getattr(self.cursor, method)(*args, **kwargs)
        return self.cursor

    async def _row(self, row: Any) -> Any:
        row = await self.owner._verify(row)
        projection = self.projection
        if row is None or not projection:
            return row
        if not isinstance(projection, Mapping):
            projection = dict.fromkeys(projection, 1)
        included = {key for key, value in projection.items() if value and key != "_id"}
        if included:
            return {
                key: value
                for key, value in row.items()
                if key in included or (key == "_id" and projection.get("_id", 1))
            }
        return {key: value for key, value in row.items() if projection.get(key, 1)}

    async def to_list(self, length: int | None = None) -> list[Any]:
        rows = await (await self._open()).to_list(length=length)
        return [await self._row(row) for row in rows]

    def __aiter__(self) -> "TenantCursor":
        return self

    async def __anext__(self) -> Any:
        return await self._row(await (await self._open()).__anext__())


class TenantCollection:
    def __init__(
        self,
        raw: AsyncCollection[MongoDocument],
        collection: str,
        context: Callable[[], TenantContext],
    ) -> None:
        from app.db.tenant_schema import TENANT_COLLECTIONS

        if collection not in TENANT_COLLECTIONS:
            raise TenantContextRequired("unclassified domain collection is refused")
        self.raw = raw
        self.collection = collection
        self.context = context

    def _context(self) -> TenantContext:
        selected = self.context()
        ambient = current_tenant()
        if ambient is None or ambient.org_id != selected.org_id:
            raise TenantContextRequired("repository requires matching trusted tenant context")
        return selected

    def query(self, filters: Mapping[str, object] | None = None) -> MongoDocument:
        self._context()
        envelope = policy_filter(self.collection)
        supplied = dict(filters or {})
        if supplied.get("org_id", self._context().org_id) != self._context().org_id:
            raise TenantContextRequired("caller cannot override repository ownership")
        if set(envelope) == {"org_id"}:
            return {**supplied, **envelope}
        return {"$and": [supplied, envelope]} if supplied else envelope

    async def _verify(self, row: MongoDocument | None) -> MongoDocument | None:
        self._context()
        if row is None:
            return None
        if row.get("org_id") != require_tenant().org_id:
            raise TenantContextRequired("retrieved ownership mismatch")
        candidate = row
        if self.collection.startswith("content_"):
            from app.db.tenant_resources import content_envelope

            projects, clients = await content_envelope(self.raw.database, self.collection, row)
            candidate = {**row, "resource_project_ids": projects, "resource_client_ids": clients}
        if not policy_allows(candidate, self.collection):
            raise TenantContextRequired("retrieved ownership or resource policy mismatch")

        # Lookup results retain a defensive tenant check independent of mongot.
        def check_nested(value: Any) -> None:
            if isinstance(value, Mapping):
                if "org_id" in value and value["org_id"] != require_tenant().org_id:
                    raise TenantContextRequired("joined ownership mismatch")
                for child in value.values():
                    check_nested(child)
            elif isinstance(value, list):
                for child in value:
                    check_nested(child)

        check_nested(row)
        return row

    async def _prepared_query(self, filters: Mapping[str, object] | None = None) -> MongoDocument:
        query = self.query(filters)
        policy = current_policy()
        if not self.collection.startswith("content_") or policy is None or policy.operator:
            return query
        # Content may inherit live ACLs through several source/version links.
        # Compute IDs before ANY count, sort, limit or search candidate selection.
        # The bound refuses work instead of silently weakening the filter.
        from app.db.tenant_resources import REFERENCE_PROJECTION, content_envelope

        rows = (
            await self.raw.find({"org_id": require_tenant().org_id}, REFERENCE_PROJECTION)
            .limit(MAX_POLICY_CANDIDATE_IDS + 1)
            .to_list(length=MAX_POLICY_CANDIDATE_IDS + 1)
        )
        if len(rows) > MAX_POLICY_CANDIDATE_IDS:
            raise TenantContextRequired("content envelope exceeds bounded preselection")
        allowed = []
        for row in rows:
            projects, clients = await content_envelope(self.raw.database, self.collection, row)
            if policy_allows(
                {**row, "resource_project_ids": projects, "resource_client_ids": clients},
                self.collection,
            ):
                allowed.append(row["_id"])
        return {"$and": [query, {"_id": {"$in": allowed}}]}

    def find(self, filters: Mapping[str, object] | None = None, *args: Any, **kwargs: Any) -> Any:
        self._context()
        projection = args[0] if args else kwargs.pop("projection", None)
        if len(args) > 1:
            raise ValueError("unsupported positional find options")

        async def factory() -> Any:
            return self.raw.find(await self._prepared_query(filters), **kwargs)

        return TenantCursor(factory, self, projection)

    async def find_one(
        self, filters: Mapping[str, object] | None = None, *args: Any, **kwargs: Any
    ) -> Any:
        if args or "projection" in kwargs:
            raise ValueError("find_one projection is not admitted")
        return await self._verify(
            await self.raw.find_one(await self._prepared_query(filters), **kwargs)
        )

    async def count_documents(
        self, filters: Mapping[str, object] | None = None, **kwargs: Any
    ) -> int:
        return await self.raw.count_documents(await self._prepared_query(filters), **kwargs)

    async def distinct(
        self, key: str, filter: Mapping[str, object] | None = None, **kwargs: Any
    ) -> Any:
        return await self.raw.distinct(key, await self._prepared_query(filter), **kwargs)

    async def _references(self, document: MongoDocument, session: Any = None) -> None:
        from app.db.tenant_inventory import references
        from app.db.tenant_schema import TENANT_COLLECTIONS

        for reference in references(self.collection, document):
            if reference.target not in TENANT_COLLECTIONS:
                raise ValueError("unclassified reference is refused")
            scope = policy_filter(reference.target)
            policy = current_policy()
            if (
                reference.target == "agent_clients"
                and policy is not None
                and policy.managed
                and policy.subject_kind == "machine"
                and policy.client_id is not None
                and reference.query == {"client_id": policy.client_id}
                and document.get("client_id") == policy.client_id
                and document.get("org_id") == policy.org_id
                and (
                    self.collection == "gateway_idempotency"
                    or (
                        self.collection == "memories"
                        and reference.path == "client_id"
                        and document.get("scope") in {"agent", "project_agent"}
                        and current_action() == "memory:write"
                        and policy_allows(document, "memories")
                    )
                )
            ):
                # Only the verified calling client's active identity is linked.
                # Private memory still needs its complete action/resource and
                # ownership envelope; this grants no client administration.
                scope = {"org_id": policy.org_id, "status": "active"}
            query = {"$and": [reference.query, scope]}
            rows = (
                await self.raw.database[reference.target]
                .find(query, {"_id": 1}, session=session)
                .limit(2)
                .to_list(length=2)
            )
            if len(rows) != 1:
                raise ValueError("reference is unavailable in the tenant/resource envelope")

    async def _insert(self, document: MongoDocument, session: Any = None) -> None:
        from app.security.job_authority import recheck_job_authority

        await recheck_job_authority()
        self._context()
        if (
            document.get("identity_version") != 2
            or document.get("org_id") != require_tenant().org_id
        ):
            raise TenantContextRequired("new domain records require owned version 2 identity")
        if self.collection.startswith("content_"):
            from app.db.tenant_resources import content_projects

            document["resource_project_ids"] = await content_projects(
                self.raw.database, self.collection, document, session
            )
        if not policy_allows(document, self.collection):
            raise TenantContextRequired("record lies outside the action/resource envelope")
        await self._references(document, session)
        from app.security.policy.hooks import record_authorship

        policy = current_policy()
        if policy is not None and (policy.managed or policy.engine is not None):
            from app.security.policy.evaluation import PolicyDenied
            from app.security.policy.hooks import is_decision

            if is_decision(self.collection, dict(document)):
                raise PolicyDenied("new authored records cannot start with an approved review")
            if policy.managed:
                await record_authorship(
                    self.raw.database, self.collection, document, session, new=True
                )

    @fenced
    async def insert_one(self, document: MongoDocument, **kwargs: Any) -> Any:
        await self._insert(document, kwargs.get("session"))
        return await self.raw.insert_one(document, **kwargs)

    @fenced
    async def insert_many(self, documents: Sequence[MongoDocument], **kwargs: Any) -> Any:
        for document in documents:
            await self._insert(document, kwargs.get("session"))
        return await self.raw.insert_many(list(documents), **kwargs)

    async def _update(
        self, filters: Mapping[str, object], update: MongoDocument, session: Any = None
    ) -> MongoDocument:
        from app.security.job_authority import recheck_job_authority

        await recheck_job_authority()
        query = await self._prepared_query(filters)
        if any(operator not in {"$set", "$unset", "$inc", "$max"} for operator in update):
            raise ValueError("unsupported tenant mutation")
        for fields in update.values():
            if not isinstance(fields, Mapping) or any(
                str(k).split(".", 1)[0] in _IMMUTABLE for k in fields
            ):
                raise ValueError("ownership and identity are immutable")
        policy = current_policy()
        if (
            policy is not None
            and (policy.managed or policy.engine is not None)
            and self.collection
            in {
                "content_items",
                "content_ideas",
                "knowledge_candidates",
                "skill_update_proposals",
                "research_claims",
                "research_notes",
                "research_briefs",
            }
        ):
            if any(
                "status" in cast(Mapping[str, object], fields)
                for operation, fields in update.items()
                if operation != "$set"
            ):
                raise ValueError("review state requires an explicit operation")
        existing = await self.raw.find_one(query, session=session)
        if existing is not None:
            from app.security.policy.hooks import record_authorship, review_gate

            policy = current_policy()
            decision = False
            if policy is not None and (policy.managed or policy.engine is not None):
                decision = await review_gate(
                    self.raw.database,
                    self.collection,
                    existing,
                    dict(cast(Mapping[str, Any], update.get("$set", {}))),
                    session,
                )
            candidate = deepcopy(existing)
            for key, value in cast(Mapping[str, object], update.get("$set", {})).items():
                target = candidate
                components = key.split(".")
                for component in components[:-1]:
                    target = cast(MongoDocument, target.setdefault(component, {}))
                target[components[-1]] = value
            for key, value in cast(Mapping[str, Any], update.get("$max", {})).items():
                if key not in candidate or candidate[key] < value:
                    candidate[key] = value
            for key in cast(Mapping[str, object], update.get("$unset", {})):
                candidate.pop(key, None)
            if self.collection.startswith("content_"):
                from app.db.tenant_resources import content_projects

                derived = await content_projects(
                    self.raw.database, self.collection, candidate, session
                )
                candidate["resource_project_ids"] = derived
                cast(dict[str, Any], update).setdefault("$set", {})["resource_project_ids"] = (
                    derived
                )
            if not policy_allows(candidate, self.collection):
                raise TenantContextRequired("mutation leaves the resource envelope")
            await self._references(candidate, session)
            if policy is not None and policy.managed and not decision:
                await record_authorship(self.raw.database, self.collection, candidate, session)
        return query

    @fenced
    async def update_one(
        self, filters: Mapping[str, object], update: MongoDocument, **kwargs: Any
    ) -> Any:
        if kwargs.get("upsert"):
            raise ValueError("tenant upserts are not admitted")
        return await self.raw.update_one(
            await self._update(filters, update, kwargs.get("session")), update, **kwargs
        )

    @fenced
    async def find_one_and_update(
        self, filters: Mapping[str, object], update: MongoDocument, **kwargs: Any
    ) -> Any:
        if kwargs.get("upsert"):
            raise ValueError("tenant upserts are not admitted")
        query = await self._update(filters, update, kwargs.get("session"))
        return await self._verify(await self.raw.find_one_and_update(query, update, **kwargs))

    @fenced
    async def delete_one(self, filters: Mapping[str, object], **kwargs: Any) -> Any:
        return await self.raw.delete_one(await self._prepared_query(filters), **kwargs)

    @fenced
    async def delete_many(self, filters: Mapping[str, object], **kwargs: Any) -> Any:
        return await self.raw.delete_many(await self._prepared_query(filters), **kwargs)

    async def _search_envelope(self, *, lexical: bool) -> dict[str, Any]:
        envelope = _search_identifier_types(await self._prepared_query())
        try:
            converted = lexical_predicate(envelope)
            if not lexical:
                from app.db.tenant_inventory import MODEL_BY_COLLECTION

                non_null_strings = frozenset(
                    name
                    for name, field in MODEL_BY_COLLECTION[self.collection].model_fields.items()
                    if field.annotation is str
                )
                simplified = _without_empty_in(envelope, non_null_strings)
                envelope = (
                    simplified
                    if simplified is not None
                    else {
                        "$and": [
                            {"org_id": require_tenant().org_id},
                            {"org_id": {"$ne": require_tenant().org_id}},
                        ]
                    }
                )
        except UnsupportedSearchPredicate:
            rows = (
                await self.raw.find(envelope, {"_id": 1})
                .limit(MAX_POLICY_CANDIDATE_IDS + 1)
                .to_list(length=MAX_POLICY_CANDIDATE_IDS + 1)
            )
            if len(rows) > MAX_POLICY_CANDIDATE_IDS:
                raise TenantContextRequired(
                    "resource envelope exceeds bounded search preselection"
                ) from None
            ids = [row["_id"] for row in rows]
            if not lexical and not ids:
                org_id = require_tenant().org_id
                # A valid, contradictory filter avoids mongot's forbidden empty
                # IN syntax while retaining the explicit tenant predicate.
                return {"$and": [{"org_id": org_id}, {"org_id": {"$ne": org_id}}]}
            envelope = {"org_id": require_tenant().org_id, "_id": {"$in": ids}}
            converted = lexical_predicate(envelope)
        return converted if lexical else envelope

    async def pipeline(self, pipeline: Sequence[MongoDocument]) -> list[MongoDocument]:
        self._context()
        stages = deepcopy(list(pipeline))
        if stages and "$search" in stages[0]:
            search = cast(dict[str, Any], stages[0]["$search"])
            compound = search.setdefault("compound", {})
            compound.setdefault("filter", []).append(await self._search_envelope(lexical=True))
        elif stages and "$vectorSearch" in stages[0]:
            vector = cast(dict[str, Any], stages[0]["$vectorSearch"])
            vector["filter"] = {
                "$and": [vector.get("filter", {}), await self._search_envelope(lexical=False)]
            }
        else:
            stages.insert(0, {"$match": await self._prepared_query()})
        for stage in stages:
            if any(
                name in stage
                for name in (
                    "$unionWith",
                    "$out",
                    "$merge",
                    "$graphLookup",
                    "$searchMeta",
                    "$facet",
                )
            ):
                raise TenantContextRequired("unsupported cross-collection pipeline")
            if "$lookup" in stage:
                lookup = cast(dict[str, Any], stage["$lookup"])
                from app.db.tenant_schema import TENANT_COLLECTIONS

                target = lookup.get("from")
                if target not in TENANT_COLLECTIONS or "pipeline" not in lookup:
                    raise TenantContextRequired("lookup must use an explicit served pipeline")
                adapter = TenantCollection(self.raw.database[target], target, self.context)
                lookup["pipeline"] = await adapter.pipeline(lookup["pipeline"])
        return stages

    async def aggregate(self, pipeline: Sequence[MongoDocument], **kwargs: Any) -> Any:
        async def factory() -> Any:
            return await self.raw.aggregate(await self.pipeline(pipeline), **kwargs)

        return TenantCursor(factory, self)


def scoped_collection(
    database: Any, collection: str, context: Callable[[], TenantContext]
) -> AsyncCollection[MongoDocument]:
    return cast(
        AsyncCollection[MongoDocument], TenantCollection(database[collection], collection, context)
    )
