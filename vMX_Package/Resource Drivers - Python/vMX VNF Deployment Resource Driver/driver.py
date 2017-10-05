import re
import telnetlib
from collections import defaultdict
from random import randint
from time import sleep, time

import websocket
from cloudshell.core.logger.qs_logger import get_qs_logger
from pyVmomi import vim  # vim normal to be unresolved in PyCharm
from pyVim.connect import SmartConnect, Disconnect
import ssl

import paramiko
import requests
import json
from cloudshell.api.cloudshell_api import CloudShellAPISession, DeployAppInput, ResourceInfoDto, \
    ResourceAttributesUpdateRequest, PhysicalConnectionUpdateRequest, AttributeNameValue, ApiEditAppRequest, \
    SetConnectorRequest, InputNameValue, AppDetails, NameValuePair, DefaultDeployment, Deployment
from cloudshell.shell.core.context import ResourceCommandContext
from cloudshell.shell.core.resource_driver_interface import ResourceDriverInterface


class OpenStack(object):
    def openstack_rest(self, method, url, headers=None, data=None):
        self.logger.info('Sending POST %s headers=%s data=%s' % (url, headers, data))
        rv = requests.request(method, url, headers=headers, data=data)

        self.logger.info('Received %s: headers=%s body=%s' % (str(rv.status_code), str(rv.headers), rv.text))
        if int(rv.status_code) >= 400:
            raise Exception(r'Request failed. %s: %s. See log under c:\ProgramData\QualiSystems\logs for full details.' % (str(rv.status_code), rv.text))
        return rv

    def __init__(self, osurlbase,
                            osprojname,
                            osdomain,
                            osusername,
                            ospassword, logger):

        self.logger = logger

        r = self.openstack_rest('POST', osurlbase + '/auth/tokens', headers={'Content-Type': 'application/json'},
                           data=json.dumps({
                               'auth': {
                                   'identity': {
                                       'methods': ['password'],
                                       'password': {
                                           'user': {
                                               'name': osusername,
                                               'password': ospassword,
                                               'domain': {'id': osdomain},
                                           },
                                       },
                                   },
                                   'scope': {
                                       'project': {
                                           'name': osprojname,
                                           'domain': {'id': osdomain},
                                       }
                                   },
                               }
                           }))

        self.token = r.headers['X-Subject-Token']
        o = json.loads(r.text)
        for service in o['token']['catalog']:
            if service['name'] == 'nova':
                for endpoint in service['endpoints']:
                    if endpoint['interface'] == 'admin':
                        self.novaurl = endpoint['url']
                        break
                else:
                    raise Exception('Nova endpoint not found in %s' % r.text)
            if service['name'] == 'neutron':
                for endpoint in service['endpoints']:
                    if endpoint['interface'] == 'admin':
                        self.neutronurl = endpoint['url']
                        break
                else:
                    raise Exception('Neutron endpoint not found in %s' % r.text)

    def create_network(self, netname):
        r = self.openstack_rest('POST', '%s/v2.0/networks.json' % self.neutronurl,
                           headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token},
                           data=json.dumps({
                               "network": {
                                   "name": netname,
                                   "admin_state_up": True
                               }
                           })
                           )
        return json.loads(r.text)['network']['id']

    def create_subnet(self, name, netid, cidr, ranges, gateway, enable_dhcp):
        r = self.openstack_rest('POST', '%s/v2.0/subnets.json' % self.neutronurl,
                                headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token},
                                data=json.dumps({
                                    'subnet': {
                                        'name': name,
                                        'enable_dhcp': enable_dhcp,
                                        'network_id': netid,
                                        'allocation_pools': [
                                            {'start': a, 'end': b} for a, b in ranges
                                        ],
                                        'gateway_ip': gateway,
                                        'ip_version': 4,
                                        'cidr': cidr,
                                    }
                                }))
        return json.loads(r.text)['subnet']['id']

    def create_fixed_ip_port(self, name, netid, subnetid, ip):
        r = self.openstack_rest('POST', '%s/v2.0/ports.json' % self.neutronurl,
                                headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token},
                                data=json.dumps({
                                    'port': {
                                        'name': name,
                                        'network_id': netid,
                                        'fixed_ips': [
                                            {
                                                'subnet_id': subnetid,
                                                'ip_address': ip,
                                            }
                                        ],
                                        'admin_state_up': True,
                                    }
                                }))
        return json.loads(r.text)['port']['id']

    def remove_security_groups(self, portid):
        self.openstack_rest('PUT', '%s/v2.0/ports/%s.json' % (self.neutronurl, portid),
                       headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token},
                       data=json.dumps({
                           'port': {
                               'security_groups': []
                           }
                       }))

    def set_port_security_enabled(self, portid, enable):
        self.openstack_rest('PUT', '%s/v2.0/ports/%s.json' % (self.neutronurl, portid),
                       headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token},
                       data=json.dumps({
                           'port': {
                               'port_security_enabled': 'True' if enable else 'False'
                           }
                       }))

    def attach_port(self, vmid, portid):
        self.openstack_rest('POST', '%s/servers/%s/os-interface' % (self.novaurl, vmid),
                       headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token},
                       data=json.dumps({
                           'interfaceAttachment': {
                               'port_id': portid
                           }
                       }))

    def attach_net(self, vmid, netid):
        self.openstack_rest('POST', '%s/servers/%s/os-interface' % (self.novaurl, vmid),
                       headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token},
                       data=json.dumps({
                           'interfaceAttachment': {
                               'net_id': netid
                           }
                       }))

    def get_serial_console_url(self, vmid):
        r = self.openstack_rest('POST', '%s/servers/%s/action' % (self.novaurl, vmid),
                           headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token},
                           data=json.dumps({
                               'os-getSerialConsole': {
                                   'type': 'serial'
                               }
                           }))
        wso = json.loads(r.text)
        return wso['console']['url']

    def disconnect_net(self, vmid, netid):
        r = self.openstack_rest('GET', '%s/servers/%s/os-interface' % (self.novaurl, vmid),
                                headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token})
        for p in json.loads(r.text)['interfaceAttachments']:
            if p['net_id'] == netid:
                port50 = p['port_id']
                self.openstack_rest('DELETE', '%s/servers/%s/os-interface/%s' % (self.novaurl, vmid, port50),
                                    headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token})


class Vcenter(object):
    def __init__(self, ip, user, password, logger):
        """

        :param ip: str
        :param user: str
        :param password: str
        :param logger: Logger
        """
        self.ip = ip
        self.user = user
        self.password = password
        self.logger = logger
        self.si = None
        self.content = None

    def __enter__(self):
        sslContext = ssl.create_default_context()
        sslContext.check_hostname = False
        sslContext.verify_mode = ssl.CERT_NONE

        self.logger.info('connecting to vCenter %s' % self.ip)
        self.si = SmartConnect(host=self.ip, user=self.user, pwd=self.password, sslContext=sslContext)
        self.logger.info('connected')
        self.content = self.si.RetrieveContent()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.logger.info('disconnecting vCenter %s' % self.ip)
        try:
            Disconnect(self.si)
        except:
            self.logger('Exception during vCenter disconnect')
        self.content = None
        self.si = None

    def get_name2vm(self):
        """

        :param vcenter: ConnectVcenter
        :return: dict[str, vim.VirtualMachine]
        """
        rv = {}
        for vm in self.content.viewManager.CreateContainerView(self.content.rootFolder, [vim.VirtualMachine], True).view:
            rv[vm.name] = vm
        return rv

    @staticmethod
    def add_serial_port(vm, telnetport, logger):
        """

        :param vm: vim.VirtualMachine
        :param telnetport: int
        :param logger: Logger
        :return:
        """
        logger.info('adding serial port to %s' % vm.name)
        spec = vim.vm.ConfigSpec()
        serial_spec = vim.vm.device.VirtualDeviceSpec()
        serial_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        serial_spec.device = vim.vm.device.VirtualSerialPort()
        serial_spec.device.yieldOnPoll = True
        serial_spec.device.backing = vim.vm.device.VirtualSerialPort.URIBackingInfo()
        serial_spec.device.backing.serviceURI = 'telnet://:%d' % telnetport
        serial_spec.device.backing.direction = 'server'
        spec.deviceChange.append(serial_spec)
        vm.ReconfigVM_Task(spec=spec)
        logger.info('added serial port')

    @staticmethod
    def get_mac2nicname(vm):
        rv = {}
        for d in vm.config.hardware.device:
            try:
                mac = d.macAddress
                mac = mac.lower().replace('-', ':')
                network_adapter_n = d.deviceInfo.label
                rv[mac] = network_adapter_n.replace('Network adapter ', '')
            except:
                pass
        return rv


class Mutex(object):
    def __init__(self, api, resid, logger=None, mutex_name='MUTEX'):
        self.api = api
        self.resid = resid
        self.logger = logger
        self.mutex_name = mutex_name

    def __enter__(self):
        t0 = time()
        for _ in range(100):
            try:
                self.api.AddServiceToReservation(self.resid, 'Mutex Service', self.mutex_name, [])
                if self.logger:
                    self.logger.info('Got mutex after %d seconds' % (time() - t0))
                break
            except Exception as e:
                if self.logger:
                    self.logger.info('Failed to add mutex service: %s; sleeping 2-5 seconds' % str(e))
                sleep(randint(2, 5))
        else:
            if self.logger:
                self.logger.info('Waited over 200 seconds without getting the mutex; continuing without it')

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            if self.logger:
                self.logger.info('Releasing mutex, caught error %s %s %s' % (str(exc_type), str(exc_val), str(exc_tb)))
        else:
            if self.logger:
                self.logger.info('Releasing mutex')
        self.api.RemoveServicesFromReservation(self.resid, [self.mutex_name])


def add_app(api, resid, appname, alias, x, y):
    """

    :param api: CloudShellAPISession
    :param resid: str
    :param appname: str
    :param alias: str
    :param x: float
    :param y: float
    :return:
    """
    api.AddAppToReservation(resid, appname, '', x, y)
    api.EditAppsInReservation(resid, [
        ApiEditAppRequest(appname, alias, '', None, None)
    ])


def get_cloud_provider_attributes(api, resource):
    """

    :param api: CloudShellAPISession
    :param resource: str
    :return: dict[str, str]
    """
    cpd2 = api.GetResourceDetails(api.GetResourceDetails(resource).VmDetails.CloudProviderFullName)
    rv = {}
    for a in cpd2.ResourceAttributes:
        v = a.Value
        if v:
            if 'password' in a.Name.lower() or 'secret' in a.Name.lower() or 'key' in a.Name.lower():
                try:
                    v = api.DecryptPassword(v).Value
                except:
                    pass
        rv[a.Name] = v

    rv['ResourceAddress'] = cpd2.Address
    rv['ResourceFamily'] = cpd2.ResourceModelName
    rv['ResourceModel'] = cpd2.ResourceModelName
    return rv


def get_details_of_deployed_app_resources(api, resid, app_aliases):
    """

    :param api: CloudShellAPISession
    :param resid: str
    :param app_aliases: list[str]
    :return: dict[str, ResourceInfo]
    """
    rd = api.GetReservationDetails(resid).ReservationDescription
    rv = {}
    for r in rd.Resources:
        for d in app_aliases:
            if r.Name.startswith(d + '_'):
                rv[r.Name] = api.GetResourceDetails(r.Name)
    return rv


def copy_resource_attributes(api, src, dest):
    """

    :param api: CloudShellAPISession
    :param src: str
    :param dest: str
    :return:
    """

    srcdet = api.GetResourceDetails(src)
    destdet = api.GetResourceDetails(dest)
    src_model = srcdet.ResourceModelName
    dest_model = destdet.ResourceModelName
    for a in srcdet.ResourceAttributes:
        v = a.Value
        if v:
            if 'password' in a.Name.lower() or 'secret' in a.Name.lower() or 'key' in a.Name.lower():
                try:
                    v = api.DecryptPassword(v).Value
                except:
                    pass
            barename = a.Name.replace(src_model + '.', '')
            try:
                api.SetAttributeValue(dest, barename, v)
            except:
                pass
            try:
                api.SetAttributeValue(dest, dest_model + '.' + barename, v)
            except:
                pass


def get_all_child_resources(api, resource):
    """

    :param api: CloudShellAPISession
    :param resource: str
    :return: dict[str, dict[str, str]]: full path to attr map
    """
    def ff(rrd, pff0):
        pff0[rrd.Name] = {a.Name: a.Value for a in rrd.ResourceAttributes}
        pff0[rrd.Name]['ResourceBasename'] = rrd.Name.split('/')[-1]
        pff0[rrd.Name]['ResourceAddress'] = rrd.FullAddress
        pff0[rrd.Name]['ResourceFamily'] = rrd.ResourceFamilyName
        pff0[rrd.Name]['ResourceModel'] = rrd.ResourceModelName
        for ch in rrd.ChildResources:
            if ch.Name:
                ff(ch, pff0)

    rd = api.GetResourceDetails(resource)
    rv = {}
    ff(rd, rv)
    return rv


def allocate_number_from_pool(api, resid, poolname, start, end):
    """

    :param api: CloudShellAPISession
    :param resid: str
    :param poolname: str
    :param start: int
    :param end: int
    :return:
    """
    pool = {
        'isolation': 'Exclusive',
        'reservationId': resid,
        'poolId': poolname,
        'ownerId': '',
        'type': 'NextAvailableNumericFromRange',
        'requestedRange': {
            'start': start,
            'end': end
        }
    }
    return int(api.CheckoutFromPool(json.dumps(pool)).Items[0])


def get_resource_position(api, resid, resource_name):
    """

    :param api: CloudShellAPISession
    :param resid: str
    :param resource_name: str
    :return: (float, float)
    """
    px = 100.0
    py = 100.0
    for p in api.GetReservationResourcesPositions(resid).ResourceDiagramLayouts:
        if p.ResourceName == resource_name:
            px = p.X
            py = p.Y
            break
    return px, py


def get_connectors_of(api, resid, resource_name):
    """

    :param api: CloudShellAPISession
    :param resid: str
    :param resource_name: str
    :return: list[Connector]
    """
    rd = api.GetReservationDetails(resid).ReservationDescription

    rv = []
    for c in rd.Connectors:
        if not c.Source:
            continue
        if c.Source.startswith(resource_name + '/') or c.Target.startswith(resource_name + '/'):
            rv.append(c)
    return rv


def delete_connectors(api, resid, connectors):
    """

    :param api: CloudShellAPISession
    :param resid: str
    :param connectors: list[Connector]
    :return:
    """
    if not connectors:
        return
    toremove = []
    for t in connectors:
        toremove.append(t.Source)
        toremove.append(t.Target)
    api.RemoveConnectorsFromReservation(resid, toremove)


def move_connectors_of(api, resid, oldresource, mapfunc, logger):
    connectors = get_connectors_of(api, resid, oldresource)
    logger.info('All connectors before move: %s' % [(c.Source, c.Target) for c in connectors])

    delete_connectors(api, resid, connectors)

    ab = []
    movehist = []
    for c in connectors:
        if c.Source.startswith(oldresource + '/'):
            a, b = mapfunc(c.Source), c.Target
            ab.append((a, b, c.Direction, c.Alias, c.Attributes))
            movehist.append((c.Source, c.Target, a, b))
        if c.Target.startswith(oldresource + '/'):
            a, b = c.Source,mapfunc(c.Target)
            ab.append((a, b, c.Direction, c.Alias, c.Attributes))
            movehist.append((c.Source, c.Target, a, b))

    if ab:
        api.SetConnectorsInReservation(resid, [
            SetConnectorRequest(a, b, direction, alias) for a, b, direction, alias, _ in ab
        ])
    for _, _, a, b, attrs in ab:
        if attrs and attrs[0].Name:
            api.SetConnectorAttributes(resid, a, b, attrs)
    logger.info('Moved connectors: %s' % ['%s -- %s  ->  %s -- %s' % (a, b, c, d) for a, b, c, d in movehist])
    rd = api.GetReservationDetails(resid).ReservationDescription
    logger.info('All connectors after move: %s' % [(c.Source, c.Target) for c in rd.Connectors])


def create_fake_L2(api, fakel2name, vlantype, portfullpath2vmname_nicname):
    api.CreateResource('Switch', 'VNF Connectivity Manager Virtual L2', fakel2name, '0')
    api.SetAttributeValue(fakel2name, 'Vlan Type', vlantype)
    api.UpdateResourceDriver(fakel2name, 'VNF Connectivity Manager L2 Driver')

    portfullpaths = sorted(portfullpath2vmname_nicname.keys())
    api.CreateResources([
        ResourceInfoDto(
            'VNF Connectivity Manager Port',
            'VNF Connectivity Manager L2 Port',
            'port%d' % i,
            '%d' % i,
            '',
            fakel2name,
            ''
        )
        for i in range(0, len(portfullpaths))
    ])

    api.UpdatePhysicalConnections([
        PhysicalConnectionUpdateRequest(vmxport, '%s/port%d' % (fakel2name, i), '1')
        for i, vmxport in enumerate(portfullpaths)
    ])

    api.SetAttributesValues([
        ResourceAttributesUpdateRequest('%s/port%d' % (fakel2name, i), [
            AttributeNameValue('VM Name', portfullpath2vmname_nicname[portfullpath][0]),
            AttributeNameValue('VM Port vNIC Name', portfullpath2vmname_nicname[portfullpath][1]),
        ])
        for i, portfullpath in enumerate(portfullpaths)
    ])


class VmxVnfDeploymentResourceDriver(ResourceDriverInterface):
    def __init__(self):
        pass

    def initialize(self, context):
        pass

    def cleanup(self):
        pass

    def vmx_orch_hook_during_provisioning(self, context):
        logger = get_qs_logger(log_group=context.reservation.reservation_id, log_file_prefix='vMX')

        logger.info('deploy called')
        api = CloudShellAPISession(host=context.connectivity.server_address,
                                   token_id=context.connectivity.admin_auth_token,
                                   domain=context.reservation.domain)
        resid = context.reservation.reservation_id
        vmxtemplate_resource = context.resource.name

        logger.info('context attrs: ' + str(context.resource.attributes))

        vmxuser = context.resource.attributes['User']
        vmxpassword = api.DecryptPassword(context.resource.attributes['Password']).Value

        vcp_app_template_name = context.resource.attributes['Chassis App']
        vfp_app_template_name_template = context.resource.attributes['Module App']

        internal_vlan_service_name = context.resource.attributes['Internal Network Service'] or 'VLAN Auto'
        vlantype = context.resource.attributes.get('Vlan Type') or 'VLAN'

        ncards = int(context.resource.attributes.get('Number of modules', '1'))

        router_family = context.resource.attributes['Deployed Resource Family']
        router_model = context.resource.attributes['Deployed Resource Model']
        router_driver = context.resource.attributes['Deployed Resource Driver']

        chassis_deployed_model_name = context.resource.attributes['Controller App Resource Model']
        card_deployed_model_name = context.resource.attributes['Card App Resource Model']

        requested_vmx_ip = context.resource.attributes.get('Management IP', 'dhcp')
        username = context.resource.attributes.get('User', 'user')
        userpassword = api.DecryptPassword(context.resource.attributes.get('Password', '')).Value
        rootpassword = userpassword
        userfullname = context.resource.attributes.get('User Full Name', username)

        missing = []
        for a in ['Chassis App', 'Module App', 'Deployed Resource Family', 'Deployed Resource Model']:
            if a not in context.resource.attributes:
                missing.append(a)
        if missing:
            raise Exception('Template resource missing values for attributes: %s' % ', '.join(missing))

        if '%d' not in vfp_app_template_name_template:
            vfp_app_template_name_template += '%d'

        px, py = get_resource_position(api, resid, vmxtemplate_resource)

        vmx_resource = vmxtemplate_resource.replace('Template ', '').replace('Template', '') + '_' + str(randint(1, 10000))
        fakel2name = '%s L2' % vmx_resource

        todeploy = [
            (vcp_app_template_name, '%s_vcp' % vmx_resource, px, py + 100)
        ] + [
            (vfp_app_template_name_template % i, '%s_vfp%d' % (vmx_resource, i), px, py+100+100+100*i)
            for i in range(ncards)
        ]

        for _ in range(5):
            with Mutex(api, resid, logger):
                for template, alias, x, y in todeploy:
                    add_app(api, resid, template, alias, x, y)

            app_aliases = [alias for template, alias, x, y in todeploy]
            api.DeployAppToCloudProviderBulk(resid, app_aliases)

            with Mutex(api, resid, logger):
                vmname2details = get_details_of_deployed_app_resources(api, resid, app_aliases)

            deployed_vcp = sorted([x for x in vmname2details if 'vcp' in x])
            deployed_vfp = sorted([x for x in vmname2details if 'vfp' in x])
            deployed = deployed_vcp + deployed_vfp

            logger.info('deployed apps = %s' % str(deployed))

            vmxip, mac2nicname, netid50 = self.post_creation_vm_setup(api,
                                                                      resid,
                                                                      deployed,
                                                                      deployed_vcp,
                                                                      deployed_vfp,
                                                                      internal_vlan_service_name,
                                                                      requested_vmx_ip,
                                                                      rootpassword,
                                                                      userfullname,
                                                                      username,
                                                                      userpassword,
                                                                      vmname2details,
                                                                      vmx_resource,
                                                                      logger)

            if not vmxip:
                raise Exception('VCP did not receive an IP (requested %s)' % (requested_vmx_ip))

            if not self.wait_for_ssh_up(vmxip, vmxuser, vmxpassword, logger):
                raise Exception('VCP not reachable via SSH within 5 minutes at IP %s -- check management network' % vmxip)

            if self.ssh_wait_for_ge_interfaces(api, resid, vmxip, vmxpassword, ncards, logger):
                logger.info('All expected ge- interfaces found')
                break

            msg = '%d card(s) not discovered within 3 minutes - recreating VMs' % ncards
            logger.info(msg)
            api.WriteMessageToReservationOutput(resid, msg)

            api.DeleteResources(deployed)
            sleep(10)
        else:
            raise Exception('%d cards were not discovered after 10 minutes in 5 attempts' % ncards)

        for kj in deployed_vfp:
            api.UpdateResourceAddress(kj, kj)

        api.CreateResource(router_family, router_model, vmx_resource, vmxip)
        api.AddResourcesToReservation(resid, [vmx_resource])
        api.SetReservationResourcePosition(resid, vmxtemplate_resource, px, py-50)
        api.SetReservationResourcePosition(resid, vmx_resource, px, py)
        if router_driver:
            api.UpdateResourceDriver(vmx_resource, router_driver)

        copy_resource_attributes(api, vmxtemplate_resource, vmx_resource)

        for _ in range(5):
            api.AutoLoad(vmx_resource)

            children_flat = get_all_child_resources(api, vmx_resource)
            ge_children_flat = {a: b
                                for a, b in children_flat.iteritems()
                                if '/' in a and '-' in a.split('/')[-1]}

            foundcards2ports = defaultdict(list)
            for fullpath, attrs in ge_children_flat.iteritems():
                foundcards2ports[attrs['ResourceBasename'].split('-')[1]].append(attrs['ResourceBasename'])

            if len(foundcards2ports) >= ncards:
                logger.info('Autoload found ports: %s' % (foundcards2ports))
                break
            logger.info('Autoload did not find all cards (%d) or ports per card (10). Retrying in 10 seconds. Found: %s' % (ncards, foundcards2ports))
            sleep(10)
        else:
            raise Exception('Autoload did not discover all expected ports - unhandled vMX failure')

        self.post_autoload_cleanup(api, resid, deployed_vfp, vmname2details, netid50, logger)

        vfpcardidstr2deployedapp3 = {vfpname.split('_')[2].replace('vfp', '').split('-')[0]: vfpname for vfpname in
                                     deployed_vfp}

        def vm_from_ge_port(portname):
            if '/' in portname:
                portname = portname.split('/')[-1]
            return vfpcardidstr2deployedapp3[portname.split('-')[1]]

        logger.info('vfpcardidstr2deployedapp = %s' % str(vfpcardidstr2deployedapp3))

        autoloadport2vmname_nicname = {}
        for ch, attrs in ge_children_flat.iteritems():
            for attr, val in attrs.iteritems():
                if 'mac' in attr.lower() and 'address' in attr.lower():
                    autoloadport2vmname_nicname[ch] = (vm_from_ge_port(ch), mac2nicname.get(val, attrs['ResourceBasename'].split('-')[-1]))

        create_fake_L2(api, fakel2name, vlantype, autoloadport2vmname_nicname)

        api.AddServiceToReservation(resid, 'VNF Cleanup Service', vmx_resource+' cleanup', [
            AttributeNameValue('Resources to Delete', ','.join([
                vmx_resource,
                fakel2name
            ]))
        ])

        logger.info('deployed_vcp=%s deployed_vfp=%s deployed=%s' % (deployed_vcp, deployed_vfp, deployed))

        with Mutex(api, resid, logger):
            basename2fullpath = {fullpath.split('/')[-1]: fullpath for fullpath in autoloadport2vmname_nicname}

            def mapfunc(oldpath):
                basename = oldpath.split('/')[-1]
                return basename2fullpath[basename]

            move_connectors_of(api, resid, vmxtemplate_resource, mapfunc, logger)

            api.RemoveResourcesFromReservation(resid, [vmxtemplate_resource])

    def post_autoload_cleanup(self, api, resid, deployed_vfp, vmname2details, netid50, logger):
        cpdet = get_cloud_provider_attributes(api, deployed_vfp[0])
        cpmodel = cpdet['ResourceModel']
        if 'openstack' in cpmodel:
            osurlbase = cpdet['Controller URL']
            osprojname = cpdet['OpenStack Project Name'] or 'admin'
            osdomain = cpdet['OpenStack Domain Name'] or 'default'
            osusername = cpdet['User Name']
            ospassword = cpdet['Password']

            openstack = OpenStack(osurlbase, osprojname, osdomain, osusername, ospassword, logger)
            for v in deployed_vfp:
                api.ExecuteResourceConnectedCommand(resid, v, 'PowerOff', 'power')
                cid = vmname2details[v].VmDetails.UID
                openstack.disconnect_net(cid, netid50)
                api.ExecuteResourceConnectedCommand(resid, v, 'PowerOn', 'power')

    def post_creation_vm_setup(self,
                               api,
                               resid,
                               deployed,
                               deployed_vcp,
                               deployed_vfp,
                               internal_vlan_service_name,
                               requested_vmx_ip,
                               rootpassword,
                               userfullname,
                               username,
                               userpassword,
                               vmname2details,
                               vmx_resource,
                               logger):
        cpdet = get_cloud_provider_attributes(api, deployed_vcp[0])
        cpmodel = cpdet['ResourceModel']
        mac2nicname = {}
        vmxip = None
        if 'vsphere' in cpmodel.lower() or 'vcenter' in cpmodel.lower():
            vcenterip = cpdet['ResourceAddress']
            vcenteruser = cpdet['User']
            vcenterpassword = cpdet['Password']

            with Vcenter(vcenterip, vcenteruser, vcenterpassword, logger) as vcenter:
                name2vm = vcenter.get_name2vm()

                for cardvmname in deployed_vfp:
                    cardvm = name2vm[cardvmname]
                    mac2nicname.update(vcenter.get_mac2nicname(cardvm))
                logger.info('mac2nicname = %s' % mac2nicname)

                telnetport = allocate_number_from_pool(api, resid, 'vmxconsoleport', 9300, 9330)

                vm = name2vm[deployed_vcp[0]]
                esxi_ip = vm.runtime.host.name
                vcenter.add_serial_port(vm, telnetport, logger)

            with Mutex(api, resid, logger):
                api.AddServiceToReservation(resid, internal_vlan_service_name, 'vMX internal network', [])
                api.SetConnectorsInReservation(resid, [
                    SetConnectorRequest('vMX internal network', d, 'bi', 'br-int') for d in deployed
                ])
                endpoints = []
                for d in deployed:
                    endpoints.append('vMX internal network')
                    endpoints.append(d)
                    api.SetConnectorAttributes(resid, 'vMX internal network', d, [
                        AttributeNameValue('Requested Target vNIC Name', '2')
                    ])
                api.ConnectRoutesInReservation(resid, endpoints, 'bi')

            for d in deployed:
                api.ExecuteResourceConnectedCommand(resid, d, 'PowerOn', 'power')

            vmxip = self.vsphere_telnet_setup(api, resid, deployed_vcp, esxi_ip, telnetport, rootpassword, userfullname, username, userpassword, requested_vmx_ip, logger)
        netid50 = None
        if 'openstack' in cpmodel:
            for d in deployed:
                api.ExecuteResourceConnectedCommand(resid, d, 'PowerOff', 'power')

            osurlbase = cpdet['Controller URL']
            osprojname = cpdet['OpenStack Project Name'] or 'admin'
            osdomain = cpdet['OpenStack Domain Name'] or 'default'
            osusername = cpdet['User Name']
            ospassword = cpdet['Password']

            openstack = OpenStack(osurlbase, osprojname, osdomain, osusername, ospassword, logger)

            netid128 = openstack.create_network('net128-%s' % vmx_resource)
            subnetid128 = openstack.create_subnet('net128-%s-subnet' % vmx_resource, netid128, '128.0.0.0/24', [('128.0.0.1', '128.0.0.254')], None, False)

            netid50 = openstack.create_network('net50-%s' % vmx_resource)
            subnetid50 = openstack.create_subnet('net50-%s-subnet' % vmx_resource, netid50, '50.0.0.0/24', [('50.0.0.1', '50.0.0.254')], None, False)

            for ip, vmname in zip(
                            ['128.0.0.%d' % (1 + i) for i in range(len(deployed_vcp))] +
                            ['128.0.0.%d' % (16 + i) for i in range(len(deployed_vfp))],

                            sorted(deployed_vcp) + sorted(deployed_vfp)):
                port128id = openstack.create_fixed_ip_port('%s-%s' % (vmx_resource, ip.replace('.', '-')), netid128, subnetid128, ip)
                openstack.remove_security_groups(port128id)
                openstack.set_port_security_enabled(port128id, False)
                cid = vmname2details[vmname].VmDetails.UID
                openstack.attach_port(cid, port128id)
                sleep(5)

            for c in sorted(deployed_vfp):
                cid = vmname2details[c].VmDetails.UID
                openstack.attach_net(cid, netid50)
                sleep(5)

            cid = vmname2details[deployed_vcp[0]].VmDetails.UID
            wsurl = openstack.get_serial_console_url(cid)

            for d in deployed:
                api.ExecuteResourceConnectedCommand(resid, d, 'PowerOn', 'power')

            sleep(30)
            self.openstack_telnet_setup(api, resid, wsurl, len(deployed_vfp), rootpassword, username, userpassword, requested_vmx_ip, logger)

            vmxdetails = api.GetResourceDetails(deployed_vcp[0])
            vmxip = vmxdetails.Address
            for a in vmxdetails.ResourceAttributes:
                if a.Name == 'Public IP' and a.Value:
                    vmxip = a.Value
                    break

        return vmxip, mac2nicname, netid50

    @staticmethod
    def wait_for_ssh_up(vmxip, vmxuser, vmxpassword, logger):
        for _ in range(120):
            try:
                logger.info('SSH attempt...')
                client = paramiko.SSHClient()
                client.load_system_host_keys()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(vmxip, port=22, username=vmxuser, password=vmxpassword, timeout=10)
                client.close()
                return True
            except:
                logger.info('SSH failed, sleeping 10 seconds')
                sleep(10)
        return True

    @staticmethod
    def ssh_wait_for_ge_interfaces(api, resid, vmxip, vmxpassword, vfp_count, logger):
        gotallcards = False
        try:
            logger.info('SSH attempt...')
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(vmxip, port=22, username='root', password=vmxpassword, timeout=10)
            logger.info('SSH connected')
            ch = client.invoke_shell()

            mac2ifname = {}
            isfirst = True
            for _ in range(18):
                mac2ifname0 = {}
                if isfirst:
                    cmdpatt = [
                        ('', '#'),
                        ('ifconfig', '#'),
                    ]
                    isfirst = False
                else:
                    cmdpatt = [
                        ('ifconfig', '#'),
                    ]
                for cmd, patt in cmdpatt:
                    if cmd:
                        logger.info('ssh send %s' % cmd)
                        ch.send('%s\n' % cmd)
                    buf = ''
                    for ll in range(10):
                        b = ch.recv(10000)
                        logger.info('ssh recv: %s' % str(b))
                        if b:
                            buf += b
                        if patt in buf:
                            break
                        if not b:
                            break
                    ifconfig = buf

                tt = re.sub(r'''[^->'_0-9A-Za-z*:;,.#@/"(){}\[\] \t\r\n]''', '_', ifconfig)
                while tt:
                    api.WriteMessageToReservationOutput(resid, tt[0:500])
                    tt = tt[500:]
                m = re.findall(
                    r'^((fe|ge|xe|et)-\d+/\d+/\d+).*?([0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+)',
                    ifconfig, re.DOTALL + re.MULTILINE)
                for j in m:
                    ifname, _, mac = j
                    mac = mac.lower()
                    mac2ifname0[mac] = ifname
                logger.info('mac2ifname0 = %s' % mac2ifname0)
                seencards = set()
                for mac, ifname in mac2ifname0.iteritems():
                    seencards.add(int(ifname.split('-')[1].split('/')[0]))

                if len(seencards) >= vfp_count:
                    mac2ifname = mac2ifname0
                    gotallcards = True

                    break

                api.WriteMessageToReservationOutput(resid, 'Still waiting for %d card(s)' % (vfp_count - len(seencards)))
                sleep(10)
            client.close()
        except Exception as e:
            logger.info('Exception during SSH session: %s' % str(e))
        logger.info('SSH disconnected')
        return gotallcards

    @staticmethod
    def openstack_telnet_setup(api, resid, wsurl, ncards, rootpassword, username, userpassword, vmxip, logger):
        logger.info('serial console web socket url: %s' % wsurl)
        ws = websocket.create_connection(wsurl, subprotocols=['binary', 'base64'])

        def ws_read_until(patt):
            buf = ''
            wmbuf = ''
            done = False
            while True:
                r = ws.recv()
                logger.info('serial console recv %s' % r)
                if r:
                    buf += r
                    wmbuf += r
                if not r:
                    logger.info('ws.recv() returned empty')
                    done = True
                if patt in buf:
                    done = True
                if len(wmbuf) > 100 or done:
                    m = wmbuf[0:100]
                    wmbuf = wmbuf[100:]
                    api.WriteMessageToReservationOutput(resid, re.sub(r'''[^->'_0-9A-Za-z*:;,.#@/"(){}\[\] \t\r\n]''', '_', m))
                if done:
                    break
            logger.info('received %s' % buf)
            return buf

        if vmxip.lower() == 'dhcp':
            ip_command = 'set interfaces fxp0.0 family inet dhcp'
        else:
            if '/' not in vmxip:
                if vmxip.startswith('172.16.'):
                    vmxip += '/16'
                else:
                    vmxip += '/24'
            ip_command = 'set interfaces fxp0.0 family inet address %s' % vmxip

        command_patts = [
            ("", "login:"),
            ("root", "#"),
            ("cli", ">"),
            ("configure", "#"),
            (ip_command, "#"),
            ("commit", "#"),
            ("set system root-authentication plain-text-password", "password:"),
            (rootpassword, "password:"),
            (rootpassword, "#"),
            ("commit", "#"),
            ("set system services ssh root-login allow", "#"),
            ("commit", "#"),
            ("set system login user %s class super-user authentication plain-text-password" % username, "password:"),
            (userpassword, "password:"),
            (userpassword, "#"),
            ("commit", "#"),
        ]
        for i in range(ncards):
            for j in range(10):
                command_patts.append(('set interfaces ge-%d/0/%d.0 family inet dhcp' % (i, j), '#'))
        command_patts += [
            ("commit", "#"),
            ("exit", ">"),
            ("exit", "#"),
            ("exit", ":"),

        ]
        for command, patt in command_patts:
            if command:
                ws.send(command + '\n')
            sleep(3)
            ws_read_until(patt)
        ws.close()

    @staticmethod
    def vsphere_telnet_setup(api, resid, deployed_vcp, esxi_ip, telnetport, rootpassword, userfullname, username, userpassword, vmxip, logger):
        if vmxip.lower() == 'dhcp':
            ip_command = 'set interfaces fxp0.0 family inet dhcp'
        else:
            if '/' not in vmxip:
                if vmxip.startswith('172.16.'):
                    vmxip += '/16'
                else:
                    vmxip += '/24'
            ip_command = 'set interfaces fxp0.0 family inet address %s' % vmxip

        command_patterns = [
            ("", "login:"),
            ("root", "#"),
            ("cli", ">"),
            ("configure", "#"),
            (ip_command, "#"),
            ("commit", "#"),
            ("set system root-authentication plain-text-password", "password:"),
            (rootpassword, "password:"),
            (rootpassword, "#"),
            ("commit", "#"),
            ("edit system services ssh", "#"),
            ("set root-login allow", "#"),
            ("commit", "#"),
            ("exit", "#"),
            ("edit system login", "#"),
            ("set user %s class super-user" % username, "#"),
            ("set user %s full-name \"%s\"" % (username, userfullname), "#"),
            ("set user %s authentication plain-text-password" % username, "password:"),
            (userpassword, "password:"),
            (userpassword, "#"),
            ("commit", "#"),
            ("exit", "#"),
            ("exit", ">"),
            ("SLEEP", "10"),
            ("show interfaces fxp0.0 terse", ">"),
        ]
        while True:
            tn = telnetlib.Telnet(esxi_ip, telnetport)
            ts = ''
            for command, pattern in command_patterns:
                if command == 'SLEEP':
                    sleep(int(pattern))
                    continue
                if command:
                    tn.write(command + '\n')
                logger.info('Telnet: write %s' % command)
                s = ''
                stuck = False
                blankt = 0
                while True:
                    t = tn.read_until(pattern, timeout=5)
                    if t:
                        blankt = 0
                    else:
                        blankt += 1
                    tt = re.sub(r'''[^->'_0-9A-Za-z*:;,.#@/"(){}\[\] \t\r\n]''', '_', t)
                    while tt:
                        api.WriteMessageToReservationOutput(resid, tt[0:500])
                        tt = tt[500:]
                    s += t
                    logger.info('Telnet: read %s' % t)
                    if pattern in s:
                        ts += s
                        break
                    if blankt > 5 and 'Choice: ' in s[-100:]:
                        stuck = True
                        break
                if stuck:
                    logger.info('Telnet: DETECTED STUCK BOOT MENU, resetting and reconnecting')
                    # tn.write('J\n1\n')
                    api.ExecuteResourceConnectedCommand(resid, deployed_vcp[0], 'PowerOff', 'power')
                    sleep(10)
                    api.ExecuteResourceConnectedCommand(resid, deployed_vcp[0], 'PowerOn', 'power')
                    sleep(10)
                    break
                sleep(1)
            else:
                break
        ips = re.findall(r'\D(\d+[.]\d+[.]\d+[.]\d+)/', ts)
        if not ips:
            raise Exception('VCP did not get IP from DHCP')
        vmxip = ips[-1]
        return vmxip


