"""Bounded noncanonical product display; never material or admission authority."""
from __future__ import annotations

import json

from control_plane_kit_core.products import ProductReferenceCodec
from control_plane_kit_operations.products import ProductRegistrationNotFound, RegisteredProductStatus, registration_id_for
from control_plane_kit_operations.read_pages import ReadCollection, ReadPageRequest, WorkspaceReadScope
from ._redaction import _redact_descriptor_value
from .errors import ReadModelError


class _DirectoryRead:
    def __init__(self, value):
        try:
            encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
            if len(encoded) > 1048576:
                raise ValueError
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise ReadModelError("product directory response is malformed or oversized") from None
        self._encoded = encoded

    def descriptor(self):
        return json.loads(self._encoded)


def _summary(value, workspace_id):
    try:
        if type(value) is not dict:
            raise ValueError
        reference = ProductReferenceCodec().decode(value['reference'])
        if value['registration_id'] != registration_id_for(workspace_id, reference):
            raise ValueError
        for key, limit in (('display_name',128),('description',1024)):
            text = value[key]
            if text is not None and (type(text) is not str or len(text) > limit):
                raise ValueError
        if value['product_family'] not in {'server','data-service'}:
            raise ValueError
        status = RegisteredProductStatus(value['status']).value
        return {'registration_id':value['registration_id'], 'reference':reference.descriptor(),
                'status':status, 'display_name':value['display_name'], 'description':value['description'],
                'product_family':value['product_family']}
    except (KeyError, ValueError, TypeError):
        raise ReadModelError("product directory record is malformed or oversized") from None


def _material_shape(delivery):
    raw = delivery.descriptor()
    # Never include reference_id, credential, actual value, or provider handle.
    result = {key: raw[key] for key in ('kind','environment_name','target_path','file_mode','intent') if key in raw}
    if raw.get('path_binding') is not None:
        result['path_binding'] = {'environment_name':raw['path_binding']['environment_name']}
    return result


def _contract(product):
    c = product.runtime_contract
    return {
        'providers':[{'name':s.name,'protocol':s.protocol.descriptor()} for s in c.sockets.providers],
        'requirements':[{'name':s.name,'protocol':s.protocol.descriptor(),'env_names':list(s.env_bindings),
                         'required':s.required,'binding':s.binding.value,
                         'material_requirements':[_material_shape(d) for d in s.secret_deliveries]}
                        for s in c.sockets.requirements],
        'provider_ports':[p.descriptor() for p in c.provider_ports],
        'public_environment_names':[v.name for v in c.public_environment],
        'configuration':[{'artifact_id':a.artifact_id,'target_path':a.target_path,
                          'media_type':a.media_type.value,'file_mode':a.file_mode.value} for a in c.configuration_artifacts],
        'material_requirements':[_material_shape(d) for d in c.secret_deliveries],
        'retained_data_mounts':[{'resource_id':m.resource_id,'target_path':m.target_path} for m in c.retained_data_mounts],
        'capabilities':[v.value for v in c.capabilities],
        'lifecycle':c.lifecycle.descriptor(),
        'verification':[{'kind':v.descriptor()['kind'],'check_id':v.check_id,'provider_socket':v.provider_socket} for v in c.verification.checks],
        'control_surfaces':[{'provider_socket_name':s.provider_socket_name.value,
                             'variable_names':[v.variable_name.value for v in s.variables],
                             'health_reads':[v.value for v in s.health_reads]} for s in c.control_surfaces],
    }


class _ProductDirectoryReadProjection:
    def __init__(self, require_workspace, store):
        self._require_workspace = require_workspace
        self._store = store

    def _require(self, workspace_id):
        self._require_workspace(workspace_id)
        if self._store is None:
            raise ReadModelError('product directory store is not configured')

    def page(self, request: ReadPageRequest):
        if request.collection is not ReadCollection.REGISTERED_PRODUCTS:
            raise ReadModelError('product directory page is malformed')
        workspace_id = request.scope.workspace_id
        self._require(workspace_id)
        page = self._store.directory_page(request).map(lambda value:_summary(value,workspace_id))
        return _DirectoryRead(page.descriptor())

    def detail(self, workspace_id, digest):
        WorkspaceReadScope(workspace_id)
        self._require(workspace_id)
        try:
            record = self._store.directory_detail(workspace_id,digest)
        except ProductRegistrationNotFound:
            raise ReadModelError('missing registered product') from None
        product = record.descriptor_document.product
        summary = _summary({'registration_id':record.registration_id,'reference':record.reference.descriptor(),
            'status':record.status.value,'display_name':product.display_name,'description':product.description,
            'product_family':product.product_family.value},workspace_id)
        image = product.image
        projection = {'image':{'registry':image.registry,'repository':image.repository,'digest':image.digest,
                               'tag':image.tag,'platforms':[p.descriptor() for p in image.platforms]},
                      'source':record.source.descriptor(), 'imported_at':record.imported_at,
                      'contract':_contract(product)}
        safe = _redact_descriptor_value('product_projection', projection)
        return _DirectoryRead({'workspace_id':workspace_id,'kind':'registered-product-detail',
                               'product':{**summary, 'projection_kind':'noncanonical-product-display',**safe}})
