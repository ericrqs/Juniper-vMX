import re
import telnetlib
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

        missing = []
        for a in ['Chassis App', 'Module App', 'Deployed Resource Family', 'Deployed Resource Model']:
            if a not in context.resource.attributes:
                missing.append(a)
        if missing:
            raise Exception('Template resource missing values for attributes: %s' % ', '.join(missing))

        px = 100
        py = 100
        for p in api.GetReservationResourcesPositions(resid).ResourceDiagramLayouts:
            if p.ResourceName == vmxtemplate_resource:
                px = p.X
                py = p.Y
                break
        x = px
        y = py
        vmx_resource = vmxtemplate_resource.replace('Template ', '').replace('Template', '') + '_' + str(randint(1, 10000))
        fakel2name = '%s L2' % vmx_resource

        todeploy = []
        todeploymodels = []

        with Mutex(api, resid, logger):
            api.AddAppToReservation(resid, vcp_app_template_name, '', x, y+100)

            vcp_app_requested_name = '%s_vcp' % vmx_resource
            api.EditAppsInReservation(resid, [
                ApiEditAppRequest(vcp_app_template_name, vcp_app_requested_name, '', None, None)
            ])
            todeploy.append(vcp_app_requested_name)
            todeploymodels.append(chassis_deployed_model_name)
            for i in range(ncards):
                if '%d' in vfp_app_template_name_template:
                    vfp_app_template_name = vfp_app_template_name_template % i
                else:
                    vfp_app_template_name = vfp_app_template_name_template
                api.AddAppToReservation(resid, vfp_app_template_name, '', x, y+100+100+100*i)
                vfp_app_requested_name = '%s_vfp%d' % (vmx_resource, i)
                api.EditAppsInReservation(resid, [
                    ApiEditAppRequest(vfp_app_template_name, vfp_app_requested_name, '', None, None)
                ])
                todeploy.append(vfp_app_requested_name)
                todeploymodels.append(card_deployed_model_name)

        # rd = api.GetReservationDetails(resid).ReservationDescription
        # cpdetails = None
        # for app in rd.Apps:
        #     if app.Name == vcp_app_requested_name:
        #         cpname = app.DeploymentPaths[0].DeploymentService.CloudProvider
        #         cpdetails = api.GetResourceDetails(cpname)
        #         break

        def openstack_rest(method, url, headers=None, data=None):
            logger.info('Sending POST %s headers=%s data=%s' % (url, headers, data))
            rv = requests.request(method, url, headers=headers, data=data)

            logger.info('Received %s: headers=%s body=%s' % (str(rv.status_code), str(rv.headers), rv.text))
            if int(rv.status_code) >= 400:
                raise Exception(r'Request failed. %s: %s. See log under c:\ProgramData\QualiSystems\logs for full details.' % (str(rv.status_code), rv.text))
            return rv

        api.DeployAppToCloudProviderBulk(resid, todeploy)

        deployed = []
        deployed_vcp = []
        deployed_vfp = []
        vfpcardidstr2deployedapp = {}
        vmname2details = {}
        with Mutex(api, resid, logger):
            rd = api.GetReservationDetails(resid).ReservationDescription
            for r in rd.Resources:
                if r.Name.startswith(vmx_resource + '_'):
                    deployed.append(r.Name)
                    if 'vcp' in r.Name.lower():
                        deployed_vcp.append(r.Name)
                        vmname2details[r.Name] = api.GetResourceDetails(r.Name)
                    elif 'vfp' in r.Name.lower():
                        deployed_vfp.append(r.Name)
                        cardidstr = r.Name.split('_')[2].replace('vfp', '').split('-')[0]
                        vfpcardidstr2deployedapp[cardidstr] = r.Name
                        vmname2details[r.Name] = api.GetResourceDetails(r.Name)
        logger.info('deployed apps = %s' % str(deployed))
        logger.info('vfpcardidstr2deployedapp = %s' % str(vfpcardidstr2deployedapp))

        cpname = vmname2details[deployed_vcp[0]].VmDetails.CloudProviderFullName
        cpdetails = api.GetResourceDetails(cpname)
        mac2nicname = {}

        vmxip = context.resource.attributes.get('Management IP', 'dhcp')
        if vmxip.lower() == 'dhcp':
            ipcommand = 'set interfaces fxp0.0 family inet dhcp'
        else:
            if '/' not in vmxip:
                if vmxip.startswith('172.16.'):
                    vmxip += '/16'
                else:
                    vmxip += '/24'
            ipcommand = 'set interfaces fxp0.0 family inet address %s' % vmxip
        username = context.resource.attributes.get('User', 'user')
        userpassword = api.DecryptPassword(context.resource.attributes.get('Password', '')).Value
        rootpassword = userpassword
        userfullname = context.resource.attributes.get('User Full Name', username)

        def power_on(vmresources):
            for c in vmresources:
                cid = vmname2details[c].VmDetails.UID
                openstack_rest('POST', '%s/servers/%s/action' % (novaurl, cid),
                               headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                               data=json.dumps({"os-start": None})
                               )
                sleep(10)

        def power_off_and_wait(vmresources):
            for c in vmresources:
                cid = vmname2details[c].VmDetails.UID
                openstack_rest('POST', '%s/servers/%s/action' % (novaurl, cid),
                               headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                               data=json.dumps({"os-stop": None})
                               )
            sleep(10)
            topoweroff = list(vmresources)
            for _ in range(20):
                for c in list(topoweroff):
                    cid = vmname2details[c].VmDetails.UID
                    k = openstack_rest('GET', '%s/servers/%s' % (novaurl, cid),
                                       headers={'Content-Type': 'application/json', 'X-Auth-Token': token}
                                       ).text
                    if 'SHUTOFF' in k:
                        topoweroff.remove(c)
                if not topoweroff:
                    break
                sleep(10)
            if topoweroff:
                raise Exception('Failed to power off VMs within 3 minutes: %s' % topoweroff)

        if 'vsphere' in cpdetails.ResourceModelName.lower() or \
             'vcenter' in cpdetails.ResourceModelName.lower():
            telnetpool = {
                'isolation': 'Exclusive',
                'reservationId': resid,
                'poolId': 'vmxconsoleport',
                'ownerId': '',
                'type': 'NextAvailableNumericFromRange',
                'requestedRange': {
                    'start': 9300,
                    'end': 9330
                }
            }
            logger.info('Connect child resources 5a')

            telnetport = int(api.CheckoutFromPool(json.dumps(telnetpool)).Items[0])

            cpd = api.GetResourceDetails(api.GetResourceDetails(deployed_vcp[0]).VmDetails.CloudProviderFullName)
            vcenterip = cpd.Address
            vcenteruser = [x.Value for x in cpd.ResourceAttributes if x.Name == 'User'][0]
            vcenterpassword = api.DecryptPassword(
                [x.Value for x in cpd.ResourceAttributes if x.Name == 'Password'][0]).Value

            sslContext = ssl.create_default_context()
            sslContext.check_hostname = False
            sslContext.verify_mode = ssl.CERT_NONE

            logger.info('connecting to vCenter %s' % vcenterip)
            si = SmartConnect(host=vcenterip, user=vcenteruser, pwd=vcenterpassword, sslContext=sslContext)
            logger.info('connected')
            content = si.RetrieveContent()
            vm = None

            for c in content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True).view:
                for cardvm in deployed_vfp:
                    if c.name == cardvm:
                        for d in c.config.hardware.device:
                            try:
                                mac = d.macAddress
                                mac = mac.lower().replace('-', ':')
                                network_adapter_n = d.deviceInfo.label
                                mac2nicname[mac] = network_adapter_n.replace('Network adapter ', '')
                            except:
                                pass
                if c.name == deployed_vcp[0]:
                    vm = c
            logger.info('mac2nicname = %s' % mac2nicname)
            esxi_ip = vm.runtime.host.name
            logger.info('adding serial port to %s' % context.resource.name)
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

            Disconnect(si)

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
            command_patterns = [
                ("", "login:"),
                ("root", "#"),
                ("cli", ">"),
                ("configure", "#"),
                (ipcommand, "#"),
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

            vmxip = ips[-1]

        if 'openstack' in cpdetails.ResourceModelName.lower():
            osurlbase = [a.Value for a in cpdetails.ResourceAttributes if a.Name == 'Controller URL'][0]
            osprojname = [a.Value for a in cpdetails.ResourceAttributes if a.Name == 'OpenStack Project Name'][0] or 'admin'
            osdomain = [a.Value for a in cpdetails.ResourceAttributes if a.Name == 'OpenStack Domain Name'][0] or 'default'
            osusername = [a.Value for a in cpdetails.ResourceAttributes if a.Name == 'User Name'][0]
            ospassword = api.DecryptPassword([a.Value for a in cpdetails.ResourceAttributes if a.Name == 'Password'][0]).Value


            r = openstack_rest('POST', osurlbase + '/auth/tokens', headers={'Content-Type': 'application/json'}, data=json.dumps({
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
            token = r.headers['X-Subject-Token']
            o = json.loads(r.text)
            novaurl = ''
            neutronurl = ''
            for x in o['token']['catalog']:
                if x['name'] == 'nova':
                    for y in x['endpoints']:
                        if y['interface'] == 'admin':
                            novaurl = y['url']
                if x['name'] == 'neutron':
                    for y in x['endpoints']:
                        if y['interface'] == 'admin':
                            neutronurl = y['url']

            if not novaurl or not neutronurl:
                raise Exception('Failed to find nova or neutron endpoint in %s' % r.text)

            # power off as soon as possible
            power_off_and_wait(sorted(deployed_vcp) + sorted(deployed_vfp))

            netname128 = 'net128-%s' % vmx_resource
            r = openstack_rest('POST', '%s/v2.0/networks.json' % neutronurl,
                              headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                              data=json.dumps({
                                  "network": {
                                      "name": netname128,
                                      "admin_state_up": True
                                  }
                              })
                              )
            netid128 = json.loads(r.text)['network']['id']
            subnetname128 = '%s-subnet' % netname128
            r = openstack_rest('POST', '%s/v2.0/subnets.json' % neutronurl,
                              headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                              data=json.dumps({
                                  'subnet': {
                                      'name': subnetname128,
                                      'enable_dhcp': False,
                                      'network_id': netid128,
                                      'allocation_pools': [
                                          {'start': '128.0.0.1', 'end': '128.0.0.254'}
                                      ],
                                      'gateway_ip': None,
                                      'ip_version': 4,
                                      'cidr': '128.0.0.0/24',
                                  }
                              })
                              )

            subnetid128 = json.loads(r.text)['subnet']['id']

            netname50 = 'net50-%s' % vmx_resource
            r = openstack_rest('POST', '%s/v2.0/networks.json' % neutronurl,
                              headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                              data=json.dumps({
                                  "network": {
                                      "name": netname50,
                                      "admin_state_up": True
                                  }
                              })
                              )
            netid50 = json.loads(r.text)['network']['id']
            subnetname50 = '%s-subnet' % netname50
            r = openstack_rest('POST', '%s/v2.0/subnets.json' % neutronurl,
                              headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                              data=json.dumps({
                                  'subnet': {
                                      'name': subnetname50,
                                      'enable_dhcp': False,
                                      'network_id': netid50,
                                      'allocation_pools': [
                                          {'start': '50.0.0.1', 'end': '50.0.0.254'}
                                      ],
                                      'gateway_ip': None,
                                      'ip_version': 4,
                                      'cidr': '50.0.0.0/24',
                                  }
                              })
                              )

            subnetid50 = json.loads(r.text)['subnet']['id']

            port128ids = []
            for ip in ['128.0.0.1'] + ['128.0.0.%d' % (16+i) for i in range(ncards)]:
                r = openstack_rest('POST', '%s/v2.0/ports.json' % neutronurl,
                                  headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                                  data=json.dumps({
                                      'port': {
                                          'name': '%s-%s' % (vmx_resource, ip.replace('.', '-')),
                                          'network_id': netid128,
                                          'fixed_ips': [
                                              {
                                                  'subnet_id': subnetid128,
                                                  'ip_address': ip,
                                              }
                                          ],
                                          'admin_state_up': True,
                                      }
                                  })
                                  )
                port128ids.append(json.loads(r.text)['port']['id'])

            for port128id in port128ids:
                openstack_rest('PUT', '%s/v2.0/ports/%s.json' % (neutronurl, port128id),
                    headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                    data=json.dumps({
                        'port': {
                            'security_groups': []
                        }
                    })
                    )
                openstack_rest('PUT', '%s/v2.0/ports/%s.json' % (neutronurl, port128id),
                    headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                    data=json.dumps({
                        'port': {
                            'port_security_enabled': 'False'
                        }
                    })
                    )

            for i, c in enumerate(deployed_vcp + sorted(deployed_vfp)):
                cid = vmname2details[c].VmDetails.UID
                openstack_rest('POST', '%s/servers/%s/os-interface' % (novaurl, cid),
                              headers = {'Content-Type': 'application/json', 'X-Auth-Token': token},
                              data=json.dumps({
                                  'interfaceAttachment': {
                                      'port_id': port128ids[i]
                                  }
                              })
                              )
                sleep(5)

            for c in sorted(deployed_vfp):
                cid = vmname2details[c].VmDetails.UID
                openstack_rest('POST', '%s/servers/%s/os-interface' % (novaurl, cid),
                              headers = {'Content-Type': 'application/json', 'X-Auth-Token': token},
                              data=json.dumps({
                                  'interfaceAttachment': {
                                      'net_id': netid50
                                  }
                              })
                              )
                sleep(5)

            power_on(deployed_vcp + deployed_vfp)

            sleep(30)
            cid = vmname2details[deployed_vcp[0]].VmDetails.UID
            r = openstack_rest('POST', '%s/servers/%s/action' % (novaurl, cid),
                    headers={'Content-Type': 'application/json', 'X-Auth-Token': token},
                    data=json.dumps({
                        'os-getSerialConsole': {
                            'type': 'serial'
                        }
                    })
                    )
            wso = json.loads(r.text)
            wsurl = wso['console']['url']
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

            command_patts = [
                ("", "login:"),
                ("root", "#"),
                ("cli", ">"),
                ("configure", "#"),
                (ipcommand, "#"),
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

            vmxdetails = api.GetResourceDetails(deployed_vcp[0])
            vmxip = vmxdetails.Address
            for a in vmxdetails.ResourceAttributes:
                if a.Name=='Public IP' and a.Value:
                    vmxip = a.Value
                    break

        for _ in range(36):
            try:
                logger.info('SSH attempt...')
                client = paramiko.SSHClient()
                client.load_system_host_keys()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(vmxip, port=22, username=vmxuser, password=vmxpassword, timeout=10)
                client.close()
                break
            except:
                logger.info('SSH failed, sleeping 10 seconds')
                sleep(10)
        else:
            raise Exception('Could not connect to %s (%s) after 5 minutes. Check the DHCP server and management network connectivity.' % (deployed_vcp[0], vmxip))

        logger.info('SSH attempt...')
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(vmxip, port=22, username='root', password=vmxpassword, timeout=10)
        logger.info('SSH connected')
        ch = client.invoke_shell()

        mac2ifname = {}
        gotallcards = False
        isfirst = True
        for _ in range(120):
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

            if len(seencards) >= len(deployed_vfp):
                mac2ifname = mac2ifname0
                gotallcards = True

                break

            api.WriteMessageToReservationOutput(resid, 'Still waiting for %d card(s)' % (len(deployed_vfp)-len(seencards)))
            sleep(5)
        client.close()
        logger.info('SSH disconnected')
        if not gotallcards:
            raise Exception('%d cards were not discovered within 10 minutes' % len(deployed_vfp))

        for kj in deployed_vfp:
            api.UpdateResourceAddress(kj, kj)

        api.CreateResource(router_family, router_model, vmx_resource, vmxip)
        api.AddResourcesToReservation(resid, [vmx_resource])
        api.SetReservationResourcePosition(resid, vmxtemplate_resource, px, py-50)
        api.SetReservationResourcePosition(resid, vmx_resource, px, py)
        if router_driver:
            api.UpdateResourceDriver(vmx_resource, router_driver)

        # destattrs = set([a.Name for a in api.GetResourceDetails(vmx_resource).ResourceAttributes])
        for a in api.GetResourceDetails(vmxtemplate_resource).ResourceAttributes:
            # if a not in destattrs:
            #     continue
            logger.info('Attribute: %s = %s' % (a.Name, a.Value))
            if 'password' in a.Name.lower() and a.Value:
                v = api.DecryptPassword(a.Value).Value
            else:
                v = a.Value
            if v:
                try:
                    api.SetAttributeValue(vmx_resource, a.Name, v)
                except:
                    pass
                try:
                    api.SetAttributeValue(vmx_resource, router_model + '.' + a.Name, v)
                except:
                    pass

        vmxrd = None
        for _ in range(5):
            api.AutoLoad(vmx_resource)
            vmxrd = api.GetResourceDetails(vmx_resource)
            def ff(rrd, pff0):
                pff0.append(rrd.Name)
                for ch in rrd.ChildResources:
                    if ch.Name:
                        ff(ch, pff0)
            pff1 = []
            ff(vmxrd, pff1)
            foundcards2ports = {}
            for pff in pff1:
                pffs = pff.split('/')[-1]
                if '-' in pffs:
                    card = pffs.split('-')[1]
                    if card not in foundcards2ports:
                        foundcards2ports[card] = []
                    foundcards2ports[card].append(pff)
            if len(foundcards2ports) >= ncards:
                logger.info('Autoload found ports: %s' % (foundcards2ports))
                break
            logger.info('Autoload did not find all cards (%d) or ports per card (10). Retrying in 10 seconds. Found: %s' % (ncards, foundcards2ports))
            sleep(10)
        else:
            raise Exception('Autoload did not discover all expected ports - unhandled vMX failure')


        vmx_port_full_paths = []
        vmx_port_basename2mac = {}
        vmx_port_basename2fullpath = {}

        def rtrav(hcr):
            if hcr.ChildResources and hcr.ChildResources[0].Name:
                for r in hcr.ChildResources:
                    rtrav(r)
            else:
                if '-' in hcr.Name.split('/')[-1]:
                    vmx_port_full_paths.append(hcr.Name)
                    vmx_port_basename2fullpath[hcr.Name.split('/')[-1]] = hcr.Name
                    vmx_port_basename2mac[hcr.Name.split('/')[-1]] = [a.Value for a in hcr.ResourceAttributes if a.Name.endswith('MAC Address')][0]

        logger.info('vmx_port_basename2fullpath=%s' % vmx_port_basename2fullpath)

        rtrav(vmxrd)

        api.CreateResource('Switch', 'VNF Connectivity Manager Virtual L2', fakel2name, '0')
        api.SetAttributeValue(fakel2name, 'Vlan Type', vlantype)
        api.UpdateResourceDriver(fakel2name, 'VNF Connectivity Manager L2 Driver')

        api.AddServiceToReservation(resid, 'VNF Cleanup Service', vmx_resource+' cleanup', [
            AttributeNameValue('Resources to Delete', ','.join([vmx_resource, fakel2name]))
        ])

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
            for i in range(0, len(vmx_port_full_paths))
        ])

        api.UpdatePhysicalConnections([
            PhysicalConnectionUpdateRequest(vmxport, '%s/port%d' % (fakel2name, i), '1')
            for i, vmxport in enumerate(vmx_port_full_paths)
        ])
        logger.info('vmx_port_full_paths = %s' % vmx_port_full_paths)
        vmx_port_basenames = [vmxport.split('/')[-1] for vmxport in vmx_port_full_paths]
        logger.info('vmx_port_basenames = %s' % vmx_port_basenames)

        api.SetAttributesValues([
            ResourceAttributesUpdateRequest('%s/port%d' % (fakel2name, i), [
                AttributeNameValue('VM Port Full Name', '%s/%s' % (vfpcardidstr2deployedapp[vmx_port_basename.split('-')[1]],
                                                                   vmx_port_basename)),
                AttributeNameValue('VM Port Full Address', '%s/%s' % (vfpcardidstr2deployedapp[vmx_port_basename.split('-')[1]],
                                                                      vmx_port_basename)),
            ])
            for i, vmx_port_basename in enumerate(vmx_port_basenames)
        ])

        api.CreateResources([
            ResourceInfoDto(
                'VNF Connectivity Manager Port',
                'VNF Connectivity Manager VM Port',
                vmx_port_basename,
                vmx_port_basename,
                '',
                vfpcardidstr2deployedapp[vmx_port_basename.split('-')[1]],
                ''
            )
            for vmx_port_basename in vmx_port_basenames
        ])

        api.SetAttributesValues([
            ResourceAttributesUpdateRequest(
                vfpcardidstr2deployedapp[vmx_port_basename.split('-')[1]] + '/' + vmx_port_basename,
                [
                    AttributeNameValue('Requested vNIC Name',
                                       mac2nicname.get(
                                           vmx_port_basename2mac.get(
                                               vmx_port_basename,
                                               'missing_mac_1_' + vmx_port_basename),
                                           vmx_port_basename.split('-')[-1]
                                       ))
                ]
            )
            for vmx_port_basename in vmx_port_basenames
        ])
        logger.info('deployed_vcp=%s deployed_vfp=%s deployed=%s' % (deployed_vcp, deployed_vfp, deployed))

        with Mutex(api, resid, logger):
            rd = api.GetReservationDetails(resid).ReservationDescription
            logger.info('All connectors: %s' % [(c.Source, c.Target) for c in rd.Connectors])
            tomove = []
            for c in rd.Connectors:
                if not c.Source:
                    continue
                if c.Source.startswith(vmxtemplate_resource + '/') or c.Target.startswith(vmxtemplate_resource + '/'):
                    tomove.append(c)
            logger.info('To move: %s' % [(c.Source, c.Target) for c in tomove])
            if tomove:
                toremove = []
                for t in tomove:
                    toremove.append(t.Source)
                    toremove.append(t.Target)
                api.RemoveConnectorsFromReservation(resid, toremove)

                ab = []
                movehist = []

                for c in tomove:
                    if c.Source.startswith(vmxtemplate_resource + '/'):
                        a, b = vmx_port_basename2fullpath[c.Source.split('/')[-1]], c.Target
                        ab.append((a, b, c.Direction, c.Alias, c.Attributes))
                        movehist.append((c.Source, c.Target, a, b))
                    if c.Target.startswith(vmxtemplate_resource + '/'):
                        a, b = c.Source, vmx_port_basename2fullpath[c.Target.split('/')[-1]]
                        ab.append((a, b, c.Direction, c.Alias, c.Attributes))
                        movehist.append((c.Source, c.Target, a, b))

                api.SetConnectorsInReservation(resid, [
                    SetConnectorRequest(a, b, direction, alias) for a, b, direction, alias, _ in ab
                ])
                # for c in tomove:
                #     if c.Attributes and c.Attributes[0].Name:
                for _, _, a, b, attrs in ab:
                    if attrs and attrs[0].Name:
                        api.SetConnectorAttributes(resid, a, b, attrs)
                logger.info('Moved connectors: %s' % ['%s -- %s  ->  %s -- %s' % (a, b, c, d) for a, b, c, d in movehist])
                rd = api.GetReservationDetails(resid).ReservationDescription
                logger.info('All connectors after move: %s' % [(c.Source, c.Target) for c in rd.Connectors])

            api.RemoveResourcesFromReservation(resid, [vmxtemplate_resource])

        if 'openstack' in cpdetails.ResourceModelName.lower():
            power_off_and_wait(deployed_vfp)
            for c in deployed_vfp:
                cid = vmname2details[c].VmDetails.UID
                jj = openstack_rest('GET', '%s/servers/%s/os-interface' % (novaurl, cid),
                              headers = {'Content-Type': 'application/json', 'X-Auth-Token': token}
                              ).text
                port50 = ''
                for p in json.loads(jj)['interfaceAttachments']:
                    if p['net_id'] == netid50:
                        port50 = p['port_id']
                        break
                if port50:
                    openstack_rest('DELETE', '%s/servers/%s/os-interface/%s' % (novaurl, cid, port50),
                                   headers={'Content-Type': 'application/json', 'X-Auth-Token': token}
                                   )
            power_on(deployed_vfp)
