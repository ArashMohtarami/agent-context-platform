"""Sealed organization registry; this phase creates no membership or owner login."""

from typing import Literal

from pydantic import Field, model_validator

from app.models.common import Identifier, MongoDocumentModel, ShortText


class OrganizationDocument(MongoDocumentModel):
    """Installation registry entry, sealed until all tenant enforcement gates pass."""

    org_id: Identifier = Field(frozen=True)
    name: ShortText
    slug: Identifier
    status: Literal["sealed", "active", "suspended"] = "sealed"
    auth_policy: Literal["local_only", "sso_preferred", "sso_required"] = "sso_preferred"
    policy_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def no_legacy_organization(self) -> "OrganizationDocument":
        if self.identity_version != 2:
            raise ValueError("organizations are new version 2 registry records")
        return self
