"""A controlled capability port; Operations alone owns admission and history."""

from control_plane_kit_core.secrets import SecretCustodyReceipt


class StagedIngressPort:
    def __init__(self, test):
        self.test = test
        self.calls = []
        self.create_allocation_names = []
        self.create_active_counts = []
        self.after_token = self.after_provider = self.after_custody = None
        self.before_provider_result = self.before_custody_result = None
        self.port = None
        self.permits = {}
        self.results = {}

    def _unit(self, invocations, kind, result=None, hook=None, *, authorization_id=None):
        permit = invocations.admit(kind, authorization_id=authorization_id)
        self.permits[kind] = permit
        # Every fake capability occurs after the admitting transaction has closed.
        self.test.assertEqual(self.test.tracker.active, 0)
        self.calls.append(kind)
        if hook is not None:
            hook()
        if result is None:
            invocations.record_outcome(permit, "returned")
        else:
            invocations.record_result(permit, "returned", result)
            self.results[kind] = result
        return permit

    def create(self, ingress, *, authority, allocation_name, origin_service_url,
            secret_resolution_grant, secret_custody_grant, invocations):
        self.port = invocations
        self.create_allocation_names.append(allocation_name)
        self.create_active_counts.append(self.test.tracker.active)
        self._unit(invocations, "secret-resolution", authorization_id=secret_resolution_grant.authorization_id,
            hook=self.after_token)
        self._unit(invocations, "ingress-provider-create", dict(version=1, tag="provider-created",
            tunnel_id="tunnel-001", dns_record_id="dns-001"), hook=self.before_provider_result)
        if self.after_provider is not None:
            self.after_provider()
        receipt = SecretCustodyReceipt(secret_custody_grant.custody_id,
            secret_custody_grant.provider_registration_id, secret_custody_grant.reference, "version-tunnel-token", 1)
        self._unit(invocations, "ingress-custody-write", dict(version=1, tag="custody-write-returned",
            receipt=receipt.descriptor()), hook=self.after_custody or self.before_custody_result)

    def teardown(self, *, authority, resources, secret_resolution_grant, secret_custody_grant, invocations):
        self.port = invocations
        self._unit(invocations, "secret-resolution", authorization_id=secret_resolution_grant.authorization_id,
            hook=self.after_token)
        self._unit(invocations, "ingress-custody-revoke", dict(version=1, tag="custody-revoke-returned"),
            hook=self.after_custody)
        self._unit(invocations, "ingress-provider-remove", dict(version=1, tag="provider-removal-returned"),
            hook=self.after_provider)
