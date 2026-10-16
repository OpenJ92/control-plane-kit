"""Original-installation capacity from supported workflows and real wire sizes.

Metadata and installation assertions are simulated observation ports. Product,
provider/reference registration, version pinning, starts, folds and publication
use their actual Operations owners. No provider or secret value is accessed.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import unittest
from unittest import mock

import psycopg
import rfc8785

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.secret_installation import secret_installation_receipt_for_result
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery, SecretProviderEndpointReference, SecretProviderId, SecretReference, SecretUseIntent,
)
from control_plane_kit_operations import graph_authoring, configuration_preparation as capacity_values
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint as Footprint
from control_plane_kit_operations.effect_attempt_fold import ExistingFold
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _Capacity
from control_plane_kit_operations.receiver_execution_scopes import ExecutionReceiverScope
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand, RegisterSecretReferenceCommand, SecretProviderKind, SecretProviderRegistrationService,
)
from tests import test_postgres_configuration_acceptance_membership as membership_fixture
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection, _PhaseRows
from tests.configuration_transfer_fixture import profiled_configuration_result
from tests.test_postgres_configuration_installation_proof import proof_sql, difference
from tests.test_postgres_configuration_publication_bounds import (
    _publication_budget_observation, _assert_publication_and_cold_fit,
)
from tests.test_secret_installation_admission import receipt_result
from tests.test_secret_selection_preparation import _MetadataReader
from tests.test_runtime_effect_translation import _configuration_product
from tests.test_postgres_secret_installation_admission import ProofReadObserver


def measurement_footprint(value):
    return dict(records=value.records, value_octets=value.value_octets,
        scalar_markers=value.scalar_markers, statements=value.statements,
        accounted_bytes=value.accounted_bytes)


def measurement_wire_report(label, observed, used):
    """Only scalar telemetry from the existing observer; never SQL, IDs or values."""
    fields = ("records", "value_octets", "scalar_markers", "statements")
    peaks = [Footprint(*item["peak"]) for item in observed["queries"]]
    report = dict(label=label, settled=measurement_footprint(used),
        peak={name: max((getattr(value, name) for value in peaks), default=0)
              for name in (*fields, "accounted_bytes")},
        queries=[dict(position=index, rows=len(item["widths"]),
            widths=item["widths"], reserved=measurement_footprint(Footprint(*item["peak"])))
            for index, item in enumerate(observed["queries"])])
    print("source-input-measurement " + json.dumps(report, separators=(",", ":")))
    return report


def measure_selected_scope_inputs(case, database_url, label, *, passes):
    """Independent read-only rehearsal, not a new advancement admission path."""
    with _configuration_accounting(("1987-measurement", label)) as accounting:
        observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
            accounting=accounting, role_label=lambda _sql, _params: label)
        with PostgresUnitOfWork(lambda: _PhaseConnection(psycopg.connect(database_url), observed)) as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            for _ in range(passes):
                evidence = uow.stores.execution.receiver_scope_evidence("workspace-a",
                    (ExecutionReceiverScope("runtime-a", "api"),), guard)
                case.assertEqual(evidence.state, "complete")
            case.assertEqual(accounting.used.statements, observed["statements"])
            measurement_wire_report(label, observed, accounting.used)


def installation_widths(connection, preimage):
    """Independent PostgreSQL text-width oracle; never a production bound."""
    return connection.execute("WITH value AS (SELECT convert_from(%s,'UTF8')::jsonb AS doc) "
        "SELECT octet_length(doc->'products'->0->>'runtime_id'), "
        "octet_length(doc->'products'->0->>'node_id'), "
        "octet_length((doc->'products'->0->'product'->'runtime_contract'->'secret_deliveries')::text), "
        "octet_length((doc->'products'->0->'secret_selections')::text) FROM value", (preimage,)).fetchone()


def assert_installation_shapes(case, bounds, originals, connection):
    """Compare issued finite identities with actual independently loaded originals."""
    shapes = getattr(bounds, "installation_shapes", None)
    case.assertIsNotNone(shapes, "owner bounds omit finite original-installation shapes")
    case.assertIs(type(shapes), tuple)
    expected = {value.identity: value for value in originals}
    case.assertEqual(len(expected), len(originals))
    case.assertEqual({value.identity for value in shapes}, set(expected))
    case.assertEqual(len(shapes), len(expected))
    for shape in shapes:
        original = expected[shape.identity]
        case.assertEqual((shape.request_fingerprint, shape.original_event_id, shape.original_event_ordinal),
            (original.request_fingerprint, original.original_start_event.event_id, original.original_start_event.ordinal))
        case.assertEqual(shape.widths, installation_widths(connection, rfc8785.dumps(original.intent.descriptor())))
        material, = original.intent.products
        case.assertIs(shape.empty, not material.product.runtime_contract.secret_deliveries
            and not material.secret_selections)
    return shapes


@contextmanager
def trace_installation_queries():
    """Test-local spy: actual bind values and scalar types, unchanged SQL/rows."""
    execute, record = _PhaseConnection.execute, _PhaseRows._record
    def traced_execute(connection, *args, **kwargs):
        offset = len(connection.observations["queries"])
        result = execute(connection, *args, **kwargs)
        entry = connection.observations["queries"][offset]
        if proof_sql(entry["sql"]):
            entry["installation_params"] = tuple(args[1] if len(args) > 1 else kwargs.get("params", ()))
            entry["installation_rows"] = []
        return result
    def traced_record(cursor, row):
        if row is not None and "installation_rows" in cursor.entry:
            cursor.entry["installation_rows"].append(
                (row[0] if type(row[0]) is int else None, tuple(type(value) for value in row)))
        return record(cursor, row)
    with mock.patch.object(_PhaseConnection, "execute", traced_execute), \
            mock.patch.object(_PhaseRows, "_record", traced_record):
        yield


def assert_cold_installation_reads(case, queries, originals):
    """Real wire rows: one probe per exact original, fetch only for material."""
    material = [entry for entry in queries if proof_sql(entry["sql"])]
    expected = sum(1 if not original.intent.products[0].product.runtime_contract.secret_deliveries
        and not original.intent.products[0].secret_selections else 2 for original in originals)
    case.assertEqual(len(material), expected)
    case.assertTrue(all(len(entry["widths"]) == 1 and len(entry["widths"][0]) == 5 for entry in material))
    matched = {original.identity: [] for original in originals}
    case.assertEqual(len(matched), len(originals))
    for entry in material:
        case.assertIn("installation_params", entry, "proof query escaped exact-original telemetry")
        params = entry["installation_params"]
        # Both unchanged SQL queries bind these six scalar guard coordinates.
        # Parameter order is not prescribed. Ambiguous matches also fail.
        candidates = [original for original in originals if all(value in params for value in (
            original.identity.run_id.value, original.identity.activity_id, original.identity.attempt,
            original.request_fingerprint, original.original_start_event.event_id,
            original.original_start_event.ordinal))]
        case.assertEqual(len(candidates), 1, "query did not guard one exact expected original")
        matched[candidates[0].identity].extend(entry["installation_rows"])
    for original in originals:
        value, = original.intent.products
        empty = not value.product.runtime_contract.secret_deliveries and not value.secret_selections
        expected_rows = [(2 if empty else 1, (int,) * 5)]
        if not empty:
            expected_rows.append((1, (int, str, str, str, str)))
        case.assertEqual(matched[original.identity], expected_rows,
            "duplicate or missing per-original probe/fetch")
    case.assertEqual(sum(entry["sql"].startswith("SAVEPOINT ") for entry in queries),
        sum(entry["sql"].startswith("RELEASE SAVEPOINT ") for entry in queries))


class PostgresConfigurationInstallationCapacityTests(unittest.TestCase):
    def installation_witness(self):
        registered = _configuration_product()
        product = registered.descriptor_document.product
        stem = "secret://local/" + "/".join("a" * 128 for _ in range(15))
        references = tuple(SecretReference(stem + f"/r{index:02d}") for index in range(24))
        self.assertEqual({len(value.reference_id.encode()) for value in references}, {1953})
        self.assertEqual(2 * sum(len(value.reference_id.encode()) for value in references), 93744)
        self.assertEqual(len(set(references)), 24)
        deliveries = tuple(SecretEnvironmentDelivery(f"SECRET_{index:02d}", reference,
            SecretUseIntent.APPLICATION_CONTROL_TOKEN) for index, reference in enumerate(references))
        selections = tuple(SecretVersionSelection(value.reference, value.intent, f"version-{index:02d}")
            for index, value in enumerate(deliveries))
        product = replace(product, runtime_contract=replace(product.runtime_contract, secret_deliveries=deliveries))
        self.installation_product = RegisteredProduct.from_document(workspace_id="workspace-a",
            descriptor_document=ProductDescriptorCodec().encode_document(product), source=registered.source,
            imported_by=registered.imported_by, imported_at=registered.imported_at)
        self.installation_selections = selections
        self.installation_metadata_calls, self.installation_requests = [], []
        self.installation_open_units = 0
        member = membership_fixture.PostgresConfigurationAcceptanceMembershipTests()
        member.registered_product = self.installation_product
        member.configuration_graph_publisher = self.publish_installation_graph
        member.configuration_extra_scopes = (PolicyScope.SECRET_PROVIDER_USE,)
        member.configuration_result_for_request = self.simulated_installation
        self.addCleanup(lambda: self.assertTrue(member.doCleanups(), "installation witness cleanup failed"))
        return member

    def publish_installation_graph(self, base, registered, graph):
        with base.unit_of_work() as uow:
            uow.stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=registered.descriptor_document, source=registered.source,
                imported_by=registered.imported_by, imported_at=registered.imported_at)
            workspace = uow.stores.workspaces.get("workspace-a")
            uow.commit()
        service = SecretProviderRegistrationService(base.unit_of_work)
        use = SecretUseIntent.APPLICATION_CONTROL_TOKEN
        provider = service.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=SecretProviderId("local"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
            display_name="Simulated installation capacity provider",
            endpoint_reference=SecretProviderEndpointReference("installation-capacity"),
            credential_reference=SecretReference("secret://bootstrap/installation-capacity/token"),
            allowed_reference_prefixes=(SecretReference("secret://local/" + "/".join("a" * 128 for _ in range(15))),),
            allowed_intents=(use,), admitted_by="operator-a", admitted_at="2026-07-22T12:00:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        reference_ids = {}
        for selection in self.installation_selections:
            registered_ref = service.register_reference(RegisterSecretReferenceCommand(
                workspace_id="workspace-a", reference=selection.reference,
                provider_registration_id=provider.registration_id, allowed_intents=(use,),
                admitted_by="operator-a", admitted_at="2026-07-22T12:00:01Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
            reference_ids[selection.reference] = registered_ref.registration_id

        @contextmanager
        def authoring_unit():
            with base.unit_of_work() as uow:
                self.installation_open_units += 1
                try:
                    yield uow
                finally:
                    self.installation_open_units -= 1

        def metadata(request):
            self.assertEqual(self.installation_open_units, 0)
            self.assertEqual(request.provider_registration_id, provider.registration_id)
            self.assertEqual(request.reference_registration_id, reference_ids[request.selection.reference])
            self.assertIn(request.selection, self.installation_selections)
            self.installation_metadata_calls.append(request.selection)
            return graph_authoring.SecretVersionMetadata(correlation_id=request.correlation_id,
                provider_registration_id=request.provider_registration_id,
                reference_registration_id=request.reference_registration_id,
                selection=request.selection, available=True)

        result = graph_authoring.GraphAuthoringService(authoring_unit,
            graph_id_factory=lambda: "graph-configured", clock=lambda: "2026-07-22T12:00:30Z",
            secret_metadata_reader=_MetadataReader(metadata)).set_desired_graph(
                graph_authoring.SetDesiredGraphCommand(workspace_id="workspace-a", actor_id="operator-a", graph=graph,
                    expected_desired_graph_id=workspace.desired_graph_id,
                    expected_desired_realized_projection_id=workspace.desired_realized_projection_id,
                    expected_desired_graph_revision=workspace.desired_graph_revision,
                    actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,),
                    explicit_secret_choices={"api": self.installation_selections}))
        self.assertEqual(set(self.installation_metadata_calls), set(self.installation_selections))
        self.assertEqual(len(self.installation_metadata_calls), 24)
        return result.workspace

    def simulated_installation(self, request):
        self.assertEqual(request.products[0].secret_selections, self.installation_selections)
        self.assertEqual(tuple(value.environment_name for value in request.products[0].product.runtime_contract.secret_deliveries),
            tuple(f"SECRET_{index:02d}" for index in range(24)))
        ordinary = profiled_configuration_result(request)
        secret = receipt_result(request)
        configuration = {**secret.evidence["secret_installation_receipt"],
            "profile": "configuration-installation-receipt.v1",
            "selection_fingerprint": ordinary.evidence["configuration_invocation_completion"]["selection_fingerprint"]}
        result = replace(ordinary, evidence={**ordinary.evidence, **secret.evidence,
            "configuration_installation_receipt": configuration})
        self.assertIsNotNone(secret_installation_receipt_for_result(request, result))
        self.installation_requests.append(request)
        return result

    @contextmanager
    def trace_installation_start(self, member):
        execute, prepare = EffectAttemptStartService._execute_once, ConfigurationPreparationStore._prepare
        capacity = capacity_values.configuration_preparation_capacity
        traces, active = [], {}
        def measured_capacity(**kwargs):
            if active and _ACCOUNTING.get() is active["wire"]["accounting"]:
                active.setdefault("forecasts", []).append(kwargs["reserved_future"])
                if "prior" not in active:
                    active.update(prior=kwargs["current"], offset=len(active["wire"]["queries"]))
            return capacity(**kwargs)
        def measured_prepare(store, stores, command, request, run, plan, guard, event_kind):
            prepared = prepare(store, stores, command, request, run, plan, guard, event_kind)
            if request.identity.request_id == "request-config":
                issued = store._ordinary_owner._issued
                shapes = getattr(issued, "installation_shapes", None)
                self.assertIsNotNone(shapes, "ordinary start omitted prospective installation proof widths")
                self.assertEqual(len(shapes), 1)
                shape, = shapes
                self.assertEqual((shape.identity, shape.request_fingerprint),
                    (command.transition.identity, runtime_effect_intent_fingerprint(prepared.intent)))
                self.assertIsNone(shape.original_event_id)
                self.assertIsNone(shape.original_event_ordinal)
                self.assertEqual(shape.widths, installation_widths(member.connection,
                    rfc8785.dumps(prepared.intent.descriptor())))
                self.assertFalse(shape.empty)
                self.assertGreater(sum(shape.widths), 93744)
                self.assertEqual(self.installation_requests, [], "measurement followed provider dispatch")
                self.assertEqual(active["ids"], [], "prospective measurement followed ID allocation")
                self.assertEqual(member.connection.execute("SELECT COUNT(*) FROM cpk_effect_attempt_intents "
                    "WHERE run_id='run-config'").fetchone(), (0,))
                active["shape"] = shape
            return prepared
        def measured_execute(service, command, health):
            if command.request_id != "request-config":
                return execute(service, command, health)
            observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
                accounting=_ACCOUNTING.get(), role_label=lambda sql, params: "selected-start")
            active.clear()
            active["wire"] = observed
            active["ids"] = []
            original_factory = service._unit_of_work_factory
            original_ids = service._id_factory
            def allocated():
                value = original_ids()
                active["ids"].append(value)
                return value
            service._id_factory = allocated
            service._unit_of_work_factory = lambda: PostgresUnitOfWork(lambda: _PhaseConnection(
                psycopg.connect(member.fixture.database_url), observed))
            try:
                # Start joins the coordinator's ledger after its earlier work.
                # Keep an immutable value for this observer's interval; never
                # reset or refund the actual command accounting.
                active["entry"] = observed["accounting"].used
                result = execute(service, command, health)
                active["used"] = observed["accounting"].used
                traces.append(dict(active))
                return result
            finally:
                service._unit_of_work_factory = original_factory
                service._id_factory = original_ids
        with mock.patch.object(ConfigurationPreparationStore, "_prepare", measured_prepare), \
                mock.patch.object(EffectAttemptStartService, "_execute_once", measured_execute), \
                mock.patch.object(capacity_values, "configuration_preparation_capacity", measured_capacity):
            yield traces

    def test_fixed_24_reference_workflow_measures_start_replay_publication_and_independent_readback(self):
        member = self.installation_witness()
        execute = EffectAttemptFoldService.execute
        replayed = []
        def fold_and_replay(service, command):
            result = execute(service, command)
            if command.request_id == "request-config" and command.outcome is not None:
                with member.fixture.unit_of_work() as uow:
                    original = uow.stores.effect_attempt_intents.get(command.outcome.identity)
                    completion_owner = type(uow.stores.configuration_completions)
                self.assertEqual(len(original.intent.products[0].secret_selections), 24)
                shortcut = getattr(completion_owner, "_get_for_original", None)
                self.assertTrue(callable(shortcut), "selected replay lacks full-original forwarding")
                observer = ProofReadObserver(psycopg.connect(member.fixture.database_url))
                forwarded = []
                def reuse(store, actual_original):
                    self.assertEqual(actual_original, original)
                    observer.enabled = True
                    try:
                        return shortcut(store, actual_original)
                    finally:
                        observer.enabled = False
                        forwarded.append(actual_original)
                original_ids, original_factory = service._id_factory, service._unit_of_work_factory
                service._id_factory = lambda: self.fail("selected full-original replay allocated an ID")
                service._unit_of_work_factory = lambda: PostgresUnitOfWork(lambda: observer)
                try:
                    with mock.patch.object(completion_owner, "_get_for_original", reuse):
                        repeated = execute(service, command)
                finally:
                    service._id_factory = original_ids
                    service._unit_of_work_factory = original_factory
                self.assertIsInstance(repeated, ExistingFold)
                self.assertEqual(repeated.outcome_record, result.outcome_record)
                self.assertEqual(forwarded, [original])
                self.assertFalse(any(proof_sql(sql) for sql in observer.queries))
                replayed.append(original)
            return result
        with self.trace_installation_start(member) as starts, \
                mock.patch.object(EffectAttemptFoldService, "execute", fold_and_replay):
            member.setUp()
        self.assertEqual(len(starts), 1)
        self.assertEqual(replayed, [member.original])
        self.assertEqual(len(self.installation_requests), 1)
        trace, = starts
        self.assertTrue(any("secret_deliveries" in entry["sql"] and "octet_length" in entry["sql"]
            for entry in trace["wire"]["queries"]), "prospective widths lack actual PostgreSQL measurement")
        measured = difference(trace["used"], trace["entry"])
        wire_rows = [row for entry in trace["wire"]["queries"] for row in entry["widths"]]
        wire = Footprint(trace["wire"]["rows"], sum(map(sum, wire_rows)), sum(map(len, wire_rows)),
            trace["wire"]["statements"])
        fields = ("records", "value_octets", "scalar_markers", "statements")
        print("installation-start accounting interval", {name: tuple(getattr(value, field) for field in fields)
            for name, value in (("entry", trace["entry"]), ("end", trace["used"]),
                ("delta", measured), ("wire", wire))})
        self.assertLessEqual(trace["used"].accounted_bytes, 16 * 1024 * 1024)
        self.assertLessEqual(trace["used"].records, 4096)
        self.assertEqual(measured.statements, wire.statements)
        self.assertGreaterEqual(measured.records, wire.records)
        self.assertEqual(measured.value_octets, wire.value_octets)
        self.assertEqual(measured.scalar_markers, wire.scalar_markers)
        self.assertEqual(len(trace["forecasts"]), 2 * len(member.refs))
        settled, peak = trace["forecasts"][:2]
        self.assertEqual(trace["forecasts"], [settled, peak] * len(member.refs))
        tail = difference(trace["used"], trace["prior"])
        for field in ("records", "value_octets", "scalar_markers", "statements"):
            self.assertLessEqual(getattr(tail, field), getattr(settled, field))
            for entry in trace["wire"]["queries"][trace["offset"]:]:
                self.assertLessEqual(getattr(difference(Footprint(*entry["peak"]), trace["prior"]), field),
                    getattr(peak, field), "selected start exceeded its complete suffix reservation")
        for entry in trace["wire"]["queries"]:
            self.assertLessEqual(Footprint(*entry["peak"]).accounted_bytes, 16 * 1024 * 1024)
            self.assertLessEqual(Footprint(*entry["peak"]).records, 4096)
        measure_selected_scope_inputs(self, member.fixture.database_url, "24-reference-before", passes=1)
        with _publication_budget_observation(self, member.fixture.database_url) as (publication, factory):
            member.advance(factory=factory)
        assert_installation_shapes(self, publication["prepared"].read_bounds,
            (member.original,), member.connection)
        _assert_publication_and_cold_fit(self, publication, member.fixture.database_url)
        print("source-input-publication-phases " + json.dumps({phase: {
            "prior": measurement_footprint(value["prior"]),
            "snapshot": measurement_footprint(value["snapshot"]),
            "future_settled": measurement_footprint(value["future"].settled),
            "future_peak": measurement_footprint(value["future"].peak),
            "publication_settled": measurement_footprint(value["publication"].settled),
            "publication_peak": measurement_footprint(value["publication"].peak),
        } for phase, value in publication["phases"].items()}, separators=(",", ":")))
        measure_selected_scope_inputs(self, member.fixture.database_url, "24-reference-after", passes=2)
        print("installation-capacity witness", dict(references=24, repeated_reference_octets=93744,
            fragment_octets=sum(trace["shape"].widths), start_records=trace["used"].records,
            start_accounted_bytes=trace["used"].accounted_bytes))

    def test_selected_publication_exact_record_and_byte_edges_include_real_shapes_and_prior_work(self):
        member = self.installation_witness()
        member.setUp()
        original = ConfigurationAcceptanceStore._preflight
        checked = []
        def preflight(store, prepared):
            assert_installation_shapes(self, prepared.read_bounds, (member.original,), member.connection)
            prior = _ACCOUNTING.get().used
            snapshot, future, publication = store._publication_budgets(prepared)
            for field, limit in (("records", 4096), ("value_octets", 16 * 1024 * 1024)):
                total = prior.plus(publication.peak)
                spare = limit - (total.records if field == "records" else total.accounted_bytes)
                self.assertGreaterEqual(spare, 0, "fixed witness exceeds actual publication forecast")
                exact = replace(prior, **{field: getattr(prior, field) + spare})
                try:
                    _ACCOUNTING.get().used = exact
                    original(store, prepared)
                    self.assertEqual(_ACCOUNTING.get().used, exact)
                    _ACCOUNTING.get().used = replace(exact, **{field: getattr(exact, field) + 1})
                    with self.assertRaises(_Capacity):
                        original(store, prepared)
                    self.assertEqual(_ACCOUNTING.get().used, replace(exact, **{field: getattr(exact, field) + 1}))
                finally:
                    # Explicit test-only prior usage; never an execution refund.
                    _ACCOUNTING.get().used = prior
            self.assertLessEqual(snapshot.accounted_bytes, 3 * 1024 * 1024)
            self.assertLessEqual(future.peak.accounted_bytes, 16 * 1024 * 1024)
            checked.append("pre-id" if prepared.event is None else "bound")
            return original(store, prepared)
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            member.advance()
        self.assertEqual(checked, ["pre-id", "bound"])
