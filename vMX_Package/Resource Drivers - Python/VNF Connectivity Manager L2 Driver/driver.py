import json
import threading

import jsonpickle
import os
from cloudshell.api.cloudshell_api import CloudShellAPISession, InputNameValue
from cloudshell.core.logger.qs_logger import get_qs_logger
from cloudshell.shell.core.context_utils import get_attribute_by_name
from cloudshell.shell.core.context import ResourceCommandContext
from cloudshell.networking.networking_resource_driver_interface import NetworkingResourceDriverInterface
from cloudshell.shell.core.resource_driver_interface import ResourceDriverInterface

from cloudshell.networking.apply_connectivity.apply_connectivity_operation import apply_connectivity_changes
from cloudshell.networking.apply_connectivity.models.connectivity_request import ConnectivityActionRequest, \
    AttributeNameValue
from cloudshell.networking.apply_connectivity.models.connectivity_result import ConnectivitySuccessResponse

class VnfConnectivityManagerL2Driver(ResourceDriverInterface, NetworkingResourceDriverInterface):
    def __init__(self):
        pass

    def initialize(self, context):
        pass

    def cleanup(self):
        pass

    def ApplyConnectivityChanges(self, context, request):
        """
        Configures VLANs on multiple ports or port-channels
        :param ResourceCommandContext context: The context object for the command with resource and reservation info
        :param str request: A JSON object with the list of requested connectivity changes
        :return: a json object with the list of connectivity changes which were carried out by the switch
        :rtype: str
        """
        logger = get_qs_logger(log_group=context.reservation.reservation_id, log_file_prefix='vMX')

        logger.info('ApplyConnectivityChanges called with json %s' % request)

        api = CloudShellAPISession(host=context.connectivity.server_address,
                                   token_id=context.connectivity.admin_auth_token,
                                   domain=context.reservation.domain)

        vmuid2portno_req_tuples = {}
        vmuid2cpname = {}
        vmuid2resourcename = {}
        o = json.loads(request)
        for action in o['driverRequest']['actions']:
            targetrd = api.GetResourceDetails(action['actionTarget']['fullName'])

            vmname = [a.Value for a in targetrd.ResourceAttributes if a.Name == 'VM Name'][0]
            nicno = [a.Value for a in targetrd.ResourceAttributes if a.Name == 'VM Port vNIC Name'][0]

            action['actionTarget']['fullName'] = vmname
            action['actionTarget']['fullAddress'] = vmname

            vmrd = api.GetResourceDetails(vmname)
            cpname = vmrd.VmDetails.CloudProviderFullName
            cpdetails = api.GetResourceDetails(cpname)
            vmuid = vmrd.VmDetails.UID
            vmuid2cpname[vmuid] = cpname
            vmuid2resourcename[vmuid] = vmrd.Name

            if 'customActionAttributes' not in action:
                action['customActionAttributes'] = []

            action['customActionAttributes'].append({
                'attributeName': 'VM_UUID',
                'attributeValue': vmuid,
            })
            # Vnic Name is supported on vSphere only (OpenStack relies on requests being sorted by NIC number)
            action['customActionAttributes'].append({
                'attributeName': 'Vnic Name',
                'attributeValue': nicno,
            })

            req = json.dumps({
                'driverRequest': {
                    'actions': [
                        action
                    ]
                }
            })
            if vmuid not in vmuid2portno_req_tuples:
                vmuid2portno_req_tuples[vmuid] = []
            try:
                nn = int(nicno or '0')
            except:
                nn = nicno
            vmuid2portno_req_tuples[vmuid].append((nn, req))

        results = []
        for vmuid in vmuid2portno_req_tuples:
            if 'openstack' in cpdetails.ResourceModelName.lower():
                api.ExecuteResourceConnectedCommand(context.reservation.reservation_id, vmuid2resourcename[vmuid], 'PowerOff', 'power')

            # send requests one by one in order by requested NIC number -- only way to control NIC order in OpenStack
            for portno, req in sorted(vmuid2portno_req_tuples[vmuid]):
                cpname = vmuid2cpname[vmuid]
                logger.info('Executing single translated request on cloud provider %s: vmuid=%s portno=%s req=%s' % (cpname, vmuid, str(portno), req))
                nr = api.ExecuteCommand(context.reservation.reservation_id,
                                        cpname,
                                        'Resource',
                                        'ApplyConnectivityChanges', [
                                            InputNameValue('request', req)
                                        ]).Output
                logger.info('Result: %s' % nr)
                onr = json.loads(nr)
                onra = onr['driverResponse']['actionResults'][0]
                results.append(onra)

            if 'openstack' in cpdetails.ResourceModelName.lower():
                api.ExecuteResourceConnectedCommand(context.reservation.reservation_id, vmuid2resourcename[vmuid], 'PowerOn', 'power')

        return json.dumps({
            'driverResponse': {
                'actionResults': results
            }
        })

    def restore(self, context, path, configuration_type, restore_method, vrf_management_name):
        pass

    def save(self, context, folder_path, configuration_type, vrf_management_name):
        pass

    def orchestration_save(self, context, mode, custom_params):
        pass

    def orchestration_restore(self, context, saved_artifact_info, custom_params):
        pass

    def get_inventory(self, context):
        pass

    def load_firmware(self, context, path, vrf_management_name):
        pass

    def run_custom_command(self, context, custom_command):
        pass

    def health_check(self, context):
        pass

    def run_custom_config_command(self, context, custom_command):
        pass

    def update_firmware(self, context, remote_host, file_path):
        pass

    def send_custom_command(self, context, custom_command):
        pass

    def send_custom_config_command(self, context, custom_command):
        pass

    def shutdown(self, context):
        pass
