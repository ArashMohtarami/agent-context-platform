"""Host-only stream declarations. No browser or MCP collector administration."""

from app.identity.repository import Row
from app.models.common import utc_now
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant import require_tenant
from app.security.tenant_policy import current_policy
from app.summaries.coverage import CHECKPOINT_ACTION
from app.summaries.models import Checkpoint
from app.summaries.service import Summaries


async def checkpoint(engine: PolicyEngine, org: str, command: Checkpoint) -> Row:
    if require_tenant().org_id != org or current_policy() is not None:
        raise PolicyDenied("an explicit installation operator context is required")
    service = Summaries(engine)
    await engine.enabled()
    async with engine.transaction() as tx:
        control = await engine.store.find(
            "_managed_agents_control", {"_id": "installation", "activated": True}, tx
        )
        if (
            not control
            or not control["created_at"] <= command.coverage_start <= utc_now()
            or command.retained_from > utc_now()
        ):
            raise PolicyDenied("verified Phase7 coverage bounds are required")
        previous = await service.checkpoint(org, command.source, tx)
        if previous and (
            previous["change"]["coverage_start"] != command.coverage_start
            or command.retained_from < previous["change"]["retained_from"]
        ):
            raise PolicyDenied("a stream cannot backdate collection or resurrect retained history")
        anchor = await engine.anchor(org, tx)
        # A new/narrower served-history boundary changes authorization, even
        # though an ordinary collector watermark does not change grant policy.
        retention_changed = (
            previous is None or command.retained_from != previous["change"]["retained_from"]
        )
        await engine.fence(anchor, tx, mutation=retention_changed)
        through = utc_now()
        gaps = [*previous["change"].get("gaps", [])] if previous else []
        gaps.extend(g.model_dump() for g in command.gaps)
        if previous and not previous["change"]["available"]:
            gaps.append({"start": previous["change"]["through"], "end": through})
        gaps = [g for g in gaps if g["end"] > command.retained_from]
        merged: list[Row] = []
        for gap in sorted(gaps, key=lambda g: g["start"]):
            if merged and gap["start"] <= merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], gap["end"])
            else:
                merged.append(dict(gap))
        gaps = merged
        if len(gaps) > 32 or any(g["end"] > through for g in gaps):
            raise PolicyDenied("bounded historical coverage gaps are required")
        declaration = command.model_dump(exclude={"source", "approval_reference"}) | {
            "through": through,
            "gaps": gaps,
        }
        await engine.audit(
            org,
            "installation_operator",
            CHECKPOINT_ACTION,
            command.source,
            tx,
            approval=command.approval_reference,
            change=declaration,
        )
        return {
            "org_id": org,
            "source": command.source,
            "through": through,
            "retained_from": command.retained_from,
            "storage_retention": command.storage_retention,
        }
