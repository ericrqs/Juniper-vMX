import json

import requests
from cloudshell.core.logger.qs_logger import get_qs_logger
from cloudshell.helpers.scripts import cloudshell_scripts_helpers as helpers

api = helpers.get_api_session()
resid = helpers.get_reservation_context_details().id

logger = get_qs_logger(log_group=resid, log_file_prefix='VNF')

delete_resources = helpers.get_resource_context_details().attributes['Resources to Delete'].split(',')

if delete_resources and delete_resources[0]:
    api.DeleteResources(delete_resources)

delete_networks = helpers.get_resource_context_details().attributes['Cloud Provider Objects to Delete'].split(',')
cpname = helpers.get_resource_context_details().attributes['Cloud Provider Name']


class OpenStack(object):
    def openstack_rest(self, method, url, headers=None, data=None):
        self.logger.info('Sending POST %s headers=%s data=%s' % (url, headers, data))
        rv = requests.request(method, url, headers=headers, data=data)

        self.logger.info('Received %s: headers=%s body=%s' % (str(rv.status_code), str(rv.headers), rv.text))
        if int(rv.status_code) >= 400:
            raise Exception(r'Request failed. %s: %s. See log under c:\ProgramData\QualiSystems\logs for full details.' % (str(rv.status_code), rv.text))
        return rv

    def __init__(self,
                 osurlbase,
                 osprojname,
                 osdomain,
                 osusername,
                 ospassword,
                 logger):

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

    def delete_network(self, netid):
        self.openstack_rest('DELETE', '%s/v2.0/networks/%s.json' % (self.neutronurl, netid),
                            headers={'X-Auth-Token': self.token})

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

    def delete_port(self, portid):
        self.openstack_rest('DELETE', '%s/v2.0/ports/%s.json' % (self.neutronurl, portid),
                            headers={'Content-Type': 'application/json', 'X-Auth-Token': self.token})

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


if delete_networks and delete_networks[0]:
    cpattrs = {a.Name: a.Value for a in api.GetResourceDetails(cpname).ResourceAttributes}

    osurlbase = cpattrs.get('Controller URL')
    osprojname = cpattrs.get('OpenStack Project Name', 'admin')
    osdomain = cpattrs.get('OpenStack Domain Name', 'default')
    osusername = cpattrs.get('User Name')
    ospassword = api.DecryptPassword(cpattrs.get('Password')).Value

    openstack = OpenStack(osurlbase, osprojname, osdomain, osusername, ospassword, logger)

    for d in delete_networks:
        kind, id = d.split(':')
        if kind == 'port':
            openstack.delete_port(id)

    for d in delete_networks:
        kind, id = d.split(':')
        if kind == 'net':
            openstack.delete_network(id)
