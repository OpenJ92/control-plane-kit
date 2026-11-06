"""Current provider-policy reads after dedicated installer admission."""

from __future__ import annotations

from typing import Callable

from control_plane_kit_core.provider_policy import (
    ProviderPolicyErrorCode,
    ProviderPolicyFailure,
    ProviderPolicyGrantPage,
    ProviderPolicyGrantRead,
    ProviderPolicyIntentMode,
    ProviderPolicyListRequest,
    ProviderPolicyPort,
    ProviderPolicyReadRequest,
)
from control_plane_kit_operations.provider_policy_commands import (
    ProviderPolicyAdministrationDenied,
    ProviderPolicyAdministrationPolicy,
    ProviderPolicyBinding,
    ProviderPolicyCommandError,
    ProviderPolicyInvocationContext,
)


class ProviderPolicyCurrentReadService:
    """Read provider-owned current truth without a local transaction or mirror."""

    def __init__(
        self,
        *,
        administration_policy: ProviderPolicyAdministrationPolicy,
        port_factory: Callable[[ProviderPolicyBinding], ProviderPolicyPort],
    ) -> None:
        if type(administration_policy) is not ProviderPolicyAdministrationPolicy:
            raise ProviderPolicyCommandError(
                "provider-policy administration policy is malformed"
            )
        if not callable(port_factory):
            raise ProviderPolicyCommandError("provider-policy port factory is malformed")
        self._policy = administration_policy
        self._port_factory = port_factory

    def read(
        self,
        context: ProviderPolicyInvocationContext,
        request: ProviderPolicyReadRequest,
    ) -> ProviderPolicyGrantRead:
        if type(request) is not ProviderPolicyReadRequest:
            raise ProviderPolicyCommandError("provider-policy read request is malformed")
        self._require(context, request.logical_provider_id)
        result = self._invoke(context, lambda port: port.read(request))
        if (
            type(result) is not ProviderPolicyGrantRead
            or result.grant.provider_id != context.binding.provider_id
            or result.grant.grant_id != request.grant_id
        ):
            self._malformed_result("read")
        return result

    def list(
        self,
        context: ProviderPolicyInvocationContext,
        request: ProviderPolicyListRequest,
    ) -> ProviderPolicyGrantPage:
        if type(request) is not ProviderPolicyListRequest:
            raise ProviderPolicyCommandError("provider-policy list request is malformed")
        self._require(context, request.logical_provider_id)
        result = self._invoke(context, lambda port: port.list(request))
        if type(result) is not ProviderPolicyGrantPage or not self._page_matches(
            context, request, result
        ):
            self._malformed_result("list")
        return result

    @staticmethod
    def _page_matches(context, request, result) -> bool:
        grants = result.grants
        grant_ids = [grant.grant_id for grant in grants]
        if (
            len(grants) > request.limit
            or len(set(grant_ids)) != len(grants)
            or grant_ids != sorted(grant_ids)
            or any(grant.revoked_at is not None for grant in grants)
        ):
            return False
        if result.next_after is not None and (
            len(grants) != request.limit
            or result.next_after != grants[-1].grant_id
        ):
            return False
        if request.after_grant_id is not None and any(
            grant_id <= request.after_grant_id for grant_id in grant_ids
        ):
            return False
        for grant in grants:
            coordinate = grant.coordinate
            if grant.provider_id != context.binding.provider_id:
                return False
            if request.subject is not None and coordinate.subject != request.subject:
                return False
            if request.workspace_id is not None and coordinate.workspace_id != request.workspace_id:
                return False
            if request.action is not None and coordinate.action != request.action:
                return False
            if (
                request.intent_mode is ProviderPolicyIntentMode.NONE
                and coordinate.intent is not None
                or request.intent_mode is ProviderPolicyIntentMode.EXACT
                and coordinate.intent != request.intent
            ):
                return False
        return True

    @staticmethod
    def _malformed_result(operation: str) -> None:
        raise ProviderPolicyFailure(
            ProviderPolicyErrorCode.MALFORMED_RESULT,
            f"provider-policy {operation} result is malformed",
        )

    def _require(
        self, context: ProviderPolicyInvocationContext, logical_provider_id: str
    ) -> None:
        self._policy.require(context)
        if logical_provider_id != context.binding.logical_provider_id:
            raise ProviderPolicyAdministrationDenied(
                "provider-policy administration is denied"
            )

    def _invoke(self, context, operation):
        try:
            port = self._port_factory(context.binding)
            return operation(port)
        except ProviderPolicyFailure:
            raise
        except Exception:
            raise ProviderPolicyFailure(
                ProviderPolicyErrorCode.UNAVAILABLE,
                "provider-policy current state is unavailable",
            ) from None


__all__ = ["ProviderPolicyCurrentReadService"]
