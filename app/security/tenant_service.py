"""Service boundary: an operation never outlives or switches its tenant."""

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from app.security.tenant import TenantContextRequired, current_tenant, require_tenant


def tenant_service[T: type[Any]](cls: T) -> T:
    """Guard public async service operations, retaining signatures and seams.

    Shared startup services defer binding until the request. Request-created
    services retain that context and cannot be reused by a different tenant.
    """
    initializer = cls.__init__

    @wraps(initializer)
    def initialize(self: Any, *args: Any, **kwargs: Any) -> None:
        self._service_tenant = current_tenant()
        initializer(self, *args, **kwargs)

    cls.__init__ = initialize
    for name, method in tuple(vars(cls).items()):
        if name.startswith("_") or not inspect.iscoroutinefunction(method):
            continue

        def guarded(operation: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(operation)
            async def call(self: Any, *args: Any, **kwargs: Any) -> Any:
                context = require_tenant()
                bound = self._service_tenant
                if bound is not None and bound != context:
                    raise TenantContextRequired("service tenant does not match the caller")
                return await operation(self, *args, **kwargs)

            return call

        setattr(cls, name, guarded(method))
    return cls
