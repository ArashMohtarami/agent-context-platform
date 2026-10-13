"""Standard MongoDB indexes for generic client profiles."""

from typing import Final

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.client_profile_schema import CLIENT_PROFILE_COLLECTION

CLIENT_PROFILE_STANDARD_INDEX_DEFINITIONS: Final[dict[str, tuple[IndexModel, ...]]] = {
    CLIENT_PROFILE_COLLECTION: (
        IndexModel([("profile_id", ASCENDING)], unique=True, name="client_profile_id_uq"),
        IndexModel([("updated_at", DESCENDING)], name="client_profile_updated"),
    ),
}
