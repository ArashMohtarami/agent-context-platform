"""Bounded retries reread policy after concurrent shared-anchor read fences."""

from collections.abc import Awaitable, Callable
from functools import wraps

from pymongo.errors import PyMongoError

from app.security.policy.evaluation import PolicyConflict
from app.security.tenant_policy import current_policy


def retry_read[T, **P](operation: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(operation)
    async def run(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in range(3):
            policy = current_policy()
            if policy is not None and policy.refresh is not None:
                await policy.refresh()
            try:
                return await operation(*args, **kwargs)
            except PyMongoError as error:
                if attempt == 2 or not error.has_error_label("TransientTransactionError"):
                    raise PolicyConflict(
                        "monitoring read could not complete; refresh bounded filters"
                    ) from None
        raise PolicyConflict("monitoring read could not complete")

    return run
