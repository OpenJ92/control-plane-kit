"""Workspace directory laws at the Operations public read boundary."""
from __future__ import annotations

import json
import os
import unittest
from dataclasses import dataclass, field

import psycopg
from control_plane_kit_core.algebra import BlockSockets, ProviderSocket, RequirementSocket
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.products import ContainerServerProduct, OciImageReference, ProductDescriptorCodec, ProductIdentity, ProductRuntimeContract
from control_plane_kit_core.secrets import SecretReference, SecretReferenceEnvironmentDelivery
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.operations.services import ControlPlaneServiceRole
from control_plane_kit_operations.cpk_server import CpkServerReadService, CpkServerApplicationError
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import ProductRegistrationService, ImportProductDescriptorCommand, InlineDescriptorSource


def document(name):
    return ProductDescriptorCodec().encode_document(ContainerServerProduct(
        ProductIdentity('directory-test',name,1), OciImageReference('example.invalid',name,'sha256:'+'a'*64),
        ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket('http',Protocol.HTTP),),requirements=(RequirementSocket('database',Protocol.POSTGRES,('DATABASE_URL',)),)),
            public_environment=(PublicStaticEnvironmentBinding('LABEL','DO_NOT_DISCLOSE_ENV'),),
            configuration_artifacts=(ConfigurationArtifact('config','/etc/app.conf',ConfigurationMediaType.TEXT,'DO_NOT_DISCLOSE_CONTENT'),),
            secret_deliveries=(SecretReferenceEnvironmentDelivery('HANDLE',SecretReference('secret://local/DO_NOT_DISCLOSE_HANDLE')),),
        ),display_name='Example '+name,description='Advisory purpose and intended connectivity.'))


def principal(allowed=True):
    return AuthenticatedPrincipal(PrincipalIdentity(issuer='urn:test:directory',subject_id='operator-a',kind=PrincipalKind.OPERATOR),
        (WorkspaceGrant('workspace-a',(PolicyScope.INSTANCE_WORKSPACE_READ,)),) if allowed else ())


@dataclass(frozen=True)
class Request:
    route_id: str = 'read.products'
    surface: str = 'http'
    service_role: ControlPlaneServiceRole = ControlPlaneServiceRole.READS
    path_parameters: dict = field(default_factory=lambda:{'workspace_id':'workspace-a'})
    payload: dict = field(default_factory=dict)
    principal: AuthenticatedPrincipal = field(default_factory=principal)


class ProductDirectoryBoundaryTests(unittest.TestCase):
    def test_unauthorized_before_hostile_cursor_or_uow(self):
        calls=[]
        def factory():
            calls.append('uow'); raise AssertionError('must not acquire')
        with self.assertRaises(CpkServerApplicationError) as caught:
            CpkServerReadService(factory).handle(Request(payload={'after':{'hostile':True}},principal=principal(False)))
        self.assertEqual(caught.exception.status,403)
        self.assertEqual(calls,[])

    def test_bad_digest_and_cursor_rejected_before_uow(self):
        for request in (Request(payload={'after':{'hostile':True}}),Request(route_id='read.product-detail',path_parameters={'workspace_id':'workspace-a','descriptor_sha256':'A'*64})):
            with self.subTest(route=request.route_id):
                calls=[]
                def factory():
                    calls.append('uow');raise AssertionError('must not acquire')
                with self.assertRaises(CpkServerApplicationError) as caught:CpkServerReadService(factory).handle(request)
                self.assertEqual(caught.exception.status,400);self.assertEqual(calls,[])

    def test_invalid_limits_and_noncanonical_cursor_rejected_before_uow(self):
        cursor = {'format_version':1,'collection':'registered-products',
                  'scope':{'workspace_id':'workspace-a'},'position':{'item_id':'A'*64}}
        for payload in ({'limit':0}, {'limit':101}, {'limit':True}, {'after':cursor}, {'unknown':1}):
            with self.subTest(payload=payload):
                def factory():
                    raise AssertionError('must not acquire')
                with self.assertRaises(CpkServerApplicationError) as caught:
                    CpkServerReadService(factory).handle(Request(payload=payload))
                self.assertEqual(caught.exception.status,400)


class ProductDirectoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.url=os.environ['CPK_OPERATIONS_TEST_DATABASE_URL']
        self.connection=psycopg.connect(self.url,autocommit=True);install_schema(self.connection)
        self.connection.execute('TRUNCATE TABLE cpk_workspaces CASCADE')
        self.connection.execute("INSERT INTO cpk_workspaces(workspace_id,name,lifecycle) VALUES ('workspace-a','A','created'),('workspace-b','B','created')")
        self.app=CpkServerReadService(self.uow)
        self.records=[]
        for name in ('zeta','alpha','beta'):
            self.records.append(ProductRegistrationService(self.uow).import_descriptor(ImportProductDescriptorCommand('workspace-a',document(name),InlineDescriptorSource(),'operator-a','2026-10-17T00:00:00Z')))

    def tearDown(self):self.connection.close()
    def uow(self):return PostgresUnitOfWork(lambda:psycopg.connect(self.url))
    def detail(self,digest,workspace='workspace-a'):
        return self.app.handle(Request(route_id='read.product-detail',path_parameters={'workspace_id':workspace,'descriptor_sha256':digest}))

    def test_bounded_pages_and_http_mcp_parity(self):
        first=self.app.handle(Request(payload={'limit':2}))
        mcp=self.app.handle(Request(surface='mcp',path_parameters={},payload={'workspace_id':'workspace-a','limit':2}))
        self.assertEqual(first,mcp);self.assertEqual(first['kind'],'registered-products')
        second=self.app.handle(Request(payload={'limit':2,'after':first['next_cursor']}))
        digests=[item['reference']['descriptor_sha256'] for item in first['items']+second['items']]
        self.assertEqual(digests,sorted(r.reference.descriptor_sha256.value for r in self.records));self.assertIsNone(second['next_cursor'])
        cursor=first['next_cursor'];cursor['scope']['workspace_id']='workspace-b'
        with self.assertRaises(CpkServerApplicationError) as caught:self.app.handle(Request(payload={'after':cursor}))
        self.assertEqual(caught.exception.status,400)

    def test_detail_preserves_reference_and_omits_values(self):
        record=self.records[0];body=self.detail(record.reference.descriptor_sha256.value)
        product=body['product'];self.assertEqual(product['reference'],record.reference.descriptor())
        self.assertEqual(body,self.app.handle(Request(route_id='read.product-detail',surface='mcp',path_parameters={},
            payload={'workspace_id':'workspace-a','descriptor_sha256':record.reference.descriptor_sha256.value})))
        self.assertEqual(product['projection_kind'],'noncanonical-product-display')
        self.assertEqual(product['contract']['providers'][0]['name'],'http')
        self.assertEqual(product['contract']['requirements'][0]['env_names'],['DATABASE_URL'])
        self.assertEqual(product['contract']['configuration'][0]['target_path'],'/etc/app.conf')
        text=json.dumps(body)
        for forbidden in ('DO_NOT_DISCLOSE_ENV','DO_NOT_DISCLOSE_CONTENT','DO_NOT_DISCLOSE_HANDLE','descriptor_document','descriptor_content'):
            self.assertNotIn(forbidden,text)
        self.assertLessEqual(len(text.encode()),1048576)

    def test_revoked_detail_and_live_active_membership(self):
        record=self.records[0];ProductRegistrationService(self.uow).revoke(workspace_id='workspace-a',reference=record.reference)
        self.assertEqual(self.detail(record.reference.descriptor_sha256.value)['product']['status'],'revoked')
        page=self.app.handle(Request())
        self.assertNotIn(record.registration_id,[p['registration_id'] for p in page['items']])
        repeated=ProductRegistrationService(self.uow).import_descriptor(ImportProductDescriptorCommand('workspace-a',record.descriptor_document,InlineDescriptorSource(),'operator-a','2026-10-17T00:00:00Z'))
        self.assertEqual(repeated.status.value,'revoked')

    def test_foreign_workspace_detail_cannot_leak(self):
        with self.assertRaises(CpkServerApplicationError) as caught:self.detail(self.records[0].reference.descriptor_sha256.value,'workspace-b')
        self.assertEqual(caught.exception.status,403)
        with self.assertRaises(CpkServerApplicationError) as caught:self.detail('0'*64)
        self.assertEqual(caught.exception.status,404)

    def test_authorized_empty_workspace_and_missing_admission(self):
        both = AuthenticatedPrincipal(principal().identity, (
            WorkspaceGrant('workspace-a',(PolicyScope.INSTANCE_WORKSPACE_READ,)),
            WorkspaceGrant('workspace-b',(PolicyScope.INSTANCE_WORKSPACE_READ,)),
        ))
        result = self.app.handle(Request(path_parameters={'workspace_id':'workspace-b'},principal=both))
        self.assertEqual(result['items'],[])
        self.assertIsNone(result['next_cursor'])
        with self.assertRaises(CpkServerApplicationError) as caught:
            self.app.handle(Request(route_id='read.product-detail',path_parameters={
                'workspace_id':'workspace-b','descriptor_sha256':self.records[0].reference.descriptor_sha256.value},principal=both))
        self.assertEqual(caught.exception.status,404)

    def test_summary_does_not_hydrate_contract_or_registration_metadata(self):
        self.connection.execute("UPDATE cpk_registered_products SET descriptor_document=jsonb_set(descriptor_document,'{product,runtime_contract}',to_jsonb(%s::text)), metadata=jsonb_build_object('private',%s::text)",('private-content'*100000,'private-metadata'*100000))
        result = self.app.handle(Request())
        self.assertEqual(len(result['items']),3)
        self.assertNotIn('private-',json.dumps(result))

    def test_summary_rejects_malformed_oversize_without_returning_payload(self):
        self.connection.execute("UPDATE cpk_registered_products SET descriptor_document=jsonb_set(descriptor_document,'{product,description}',to_jsonb(%s::text)) WHERE registration_id=%s",('x'*2000,self.records[0].registration_id))
        with self.assertRaises(CpkServerApplicationError) as caught:self.app.handle(Request())
        self.assertEqual(caught.exception.status,400)
        self.assertNotIn('x'*100,str(caught.exception))
