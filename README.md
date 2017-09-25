# Juniper-vMX

Automatic deployment of the multi-VM Juniper virtual MX (vMX) router on vSphere and OpenStack

A request to deploy a vMX router is modeled as a template resource added to the blueprint, 
instead of an app or service. This is so the user can interactively connect vMX ports to other components
before the vMX has actually been deployed, eliminating the need to manually type values for connector attributes.

The vMX is represented to the user as an autoloaded resource that should be indistinguishable from a 
hardware MX router. Juniper gen1 and gen2 shells are both supported. Other resources created during the
deployment are admin-only and hidden from the end user. The vMX resource is connected to the underlying VMs 
with a virtual L2 resource explained below. 

Point-to-point and VLAN service connections are fully supported.

vMX VM images from Juniper are deployed using `vSphere Deploy from Linked Clone` and 
`OpenStack Deploy From Glance Image` from standard cloud providers. 

Tested with vMX release 17.1R1.8

Uses general-purpose setup and teardown hooks: https://github.com/ericrqs/Setup-Teardown-Hooks

Protects against conflicting updates to the reservation using this mutex mechanism: https://github.com/ericrqs/Reservation-Mutex-Lock 


Blueprint with two back-to-back vMX requests: 
![](screenshots/two-vmx-blueprint.png)


Two deployed vMX connected back-to-back, non-admin user view:
![](screenshots/two-vmx-deployed-nonadmin.png)

Two deployed vMX connected back-to-back, admin view with admin-only items visible:
![](screenshots/two-vmx-deployed-admin.png)




## vMX Basics

A vMX is a set of VMs that behaves the same as an MX router. It can be autoloaded by Quali gen1 and gen2 Juniper shells. 
Both kinds of shell are supported in the vMX deployment.    
 
A vMX consists of one controller VM, which presents the same interface as the MX, and one or more 
card VMs linked to the controller. A NIC on a card will show up as an interface like ge-0/0/0 on 
the controller in the JunOS CLI. The controller and its cards cards are linked via an isolated network unique 
to each vMX instance. 

In CloudShell, the vMX VMs are hidden admin-only resources, and the vMX is represented by a resource 
with the Juniper shell model. The translation from the familiar Juniper MX resource interface to underlying VMs takes place 
in the background. 
 
The following are synonyms:

**Controller, VCP (virtual control plane), RE (routing engine)**

**Card, VFP (virtual forwarding plane), PFE (packet forwarding engine)**

A vMX controller communicates with its cards over a dedicated network connected to the second NIC on all VMs.
On the second NIC, the controller has a hard-coded IP of `128.0.0.1`, and cards set their IP to 
`128.0.0.16` for card 0, `128.0.0.17` for card 1, etc. 
The vMX automatically sets itself up by sending discovery messages over the `128.0.0.0` network.

Note: On OpenStack vMX may only officially support one card.


## User workflow

Drag a vMX template resource into a blueprint

Draw connectors to other elements in the blueprint

Ensure that Default Setup and Default Teardown have been replaced by hook_setup and hook_teardown
in blueprint properties. 

Reserve a new sandbox

The vMX will appear indistinguishable from any other Juniper MX router resource.  

At any time after deployment, you can add more connectors going to the deployed vMX. Run Connect 
manually on each connector, or just run Setup again, and and it should connect all unconnected connectors.
  
Note: On OpenStack, if you run Connect manually on connectors, you must do so in ascending order by vMX interface address.  


## Use in an existing sandbox

You can add a vMX template resource to an existing sandbox and connect it to other components there.

Make connections to either the vMX template before deploying or the vMX resource after deploying.

To deploy the vMX, run the function orch_hook_during_provisioning on the vMX template resource. 

The vMX will be automatically deleted by Teardown if hook_teardown is attached to the blueprint. If the blueprint still has
the default Teardown, you will need to manually delete the vMX and the associated virtual L2 resources after
the sandbox has ended.   


## vMX package installation

### General
Install the hook setup and teardown scripts: https://github.com/ericrqs/Setup-Teardown-Hooks

On any blueprint that needs to deploy a vMX, attach hook_setup and hook_teardown scripts to the sandbox in 
place of Default Setup and Default Teardown. It is recommended to set hook_setup and hook_teardown as the systemwide 
default Setup and Teardown scripts.
  

Drag vMX_Package.zip into the portal.

Create a vCenter or OpenStack cloud provider.


Create at least one vMX template resource. Add multiple port subresources with resource names like:
 
    ge-0-0-0 (first port on card 0)
    ge-0-0-1
    ...
    ge-0-0-9
    ge-1-0-0 (first port on card 1)
    ge-1-0-1
    ...

![](screenshots/vmx-template.png)
![](screenshots/vmx-template-subresources.png)

The first number in the port name is the card number, the middle number is always 0, and the last number is the port number.
Note that `/` in the JunOS name `ge-0/0/0` is replaced with `-` in all CloudShell resource names in the vMX deployment and in both
generations of the JunOS shell.

Create enough port subresources to cover all your scenarios.

Note that on vSphere, the vNICs must also be added statically to the card prototype VM in vSphere ahead of time. 
The first 3 NICs are reserved, so make sure the card template VM has 4-13 vNICs in order to 
support 1-10 traffic interfaces. vMX has a limit of about 10 traffic interfaces per card. 
There may be issues with interfaces beyond the 7th.
 

Set attribute VLAN Type on the vMX template resource:
- vSphere: Always **VLAN**
- OpenStack: **VLAN** or **VXLAN** to match the setting on the OpenStack cloud provider
    

Import the gen1 or gen2 shell for Juniper JunOS routers.

Set the attributes on the vMX template resource to match the Juniper shell:

- For the gen1 Juniper shell:
    - Deployed Resource Family: **Router**
    - Deployed Resource Model: **Juniper JunOS Router**
    - Deployed Resource Driver: **Generic Juniper JunOS Driver Version3**
- For the gen2 Juniper router shell:
    - Deployed Resource Family: **CS_Router**
    - Deployed Resource Model: **Juniper JunOS Router 2G**
    - Deployed Resource Driver: **Juniper JunOS Router 2G**


Create vMX template resources with multiple port subresources named ge-0-0-0, ge-0-0-1, ..., ge-1-0-0, ge-1-0-1, 
where 

Set other attributes that will be copied to the attributes of the vMX end product.

You must set certain attributes in order for the vMX to autoload properly: 
- SNMP Version: v2c
- SNMP Community String: public
- Enable SNMP: must be True
- User: user to create
- Password: password to set
- Enable Password: must be same as Password
- Backup Password: must be same as Password

![](screenshots/vmx-template-attrs1.png)
![](screenshots/vmx-template-attrs2.png)
![](screenshots/vmx-template-attrs3.png)


Only Password will be used. It will be set as the password for the specified user and also for root. 
Enable Password should be identical to Password. 

 
 
### vSphere-specific

Important: In Resource Manager, Resource Families, Deployment Options family, 
VCenter Deploy VM From Linked Clone, change Wait for IP to be a user input, or 
change the default to false. On all vMX-related apps, Wait for IP must be false.
![](screenshots/wait-for-ip-user-input.png)

In the vSphere client, for each potential ESXi host where the controller VM could get 
deployed, go to Configuration tab, Software section, Security Profile, Firewall, 
Properties... and enable "VM serial port connected over network" (not "VM serial port 
connected to vSPC"). If needed, you can click the Firewall button while standing on 
"VM serial port connected over network" and enable the access only for the IP of the execution server. 
![](screenshots/esxi-serial.png)


#### Controller
Import the 'vCP' template from Juniper. After importing the OVA, ensure that the VM is set to have 
at least 2048MB of memory. By default it receives so little memory that it can't boot.

Connect 'Network adapter 1' to your management network.

Power on the controller VM. Note that it may freeze for many minutes, sending output only 
to the serial console. Eventually it should many print FreeBSD boot messages and reach a login: prompt 
in the vSphere console.

Log in as 'root' with no password and run 'halt'. Wait a short time for the system to shut down. Power off the VM. 

Take a snapshot of the VM. Be sure to take the snapshot only after VM settings including NICs, and after booting at least once.
If you take a series of snapshots, be sure to note the full snapshot path from the tree under 'Manage Snapshots', `e.g. ss1/ss2/ss3`.


Create an app for the controller.

Be sure to set Wait for IP to False. If it is not visible, make the setting a user input in Resource Manager.

![](screenshots/vsphere-vcp1.png)
![](screenshots/vsphere-vcp2.png)
![](screenshots/vsphere-vcp3.png)
![](screenshots/vsphere-vcp4.png)



#### Cards

Take a snapshot of the card VM immediately with a name like `ss`. 

Then immediately take another snapshot called `card0`. This snapshot will be called `ss/card0`.

If you want to deploy cards beyond card 0, you must create additional snapshots of the 
card VM after setting the card id in a file on the VM.

For each card beyond card 0:
- Revert the VM to the first snapshot `ss`
- Boot the VM
- Log in as root/root
- Write the desired card id to a file called `/var/jnx/card/local/slot`. For example, for card #1:

        mkdir -p /var/jnx/card/local
        echo 1 > /var/jnx/card/local/slot
        reboot
        
- Log in again as root/root
- Ensure that the card id file has influenced the IP on interface 'int' using `ifconfig`. 
For example for card 1 this should display `128.0.0.17`.
        
- Shut down the VM OS: `halt`
       
- Power off the VM
- Take a snapshot with the card id in the name, e.g. `card1`

![](screenshots/vsphere-snapshots.png)


For each card snapshot, create a separate app. Use a naming convention for the apps. You will need to specify the name
on the vMX template with `%d` representing the card number. For example, if you name the card apps `vsphere-vfp0` and 
`vsphere-vfp1`, on the vMX template resource set Module App to `vsphere-vfp%d`.

Creating the app vsphere-vfp0 for card 0:
![](screenshots/vsphere-vfp0-1.png)
![](screenshots/vsphere-vfp0-2.png)
![](screenshots/vsphere-vfp0-3.png)
![](screenshots/vsphere-vfp0-4.png)

Difference in snapshot name for vsphere-vfp1 for card 1:
![](screenshots/vsphere-vfp1-2.png)



#### VLAN service
On vSphere, the deployment will automatically add an Auto VLAN service to the sandbox for the internal network. If you need to hide this
VLAN service from the user in the sandbox, copy the Virtual Network service family in Resource Manager and set the copy to admin-only. 
Rename the new Auto VLAN_1 as needed. Provide the model name of the new admin-only auto VLAN service in 
the Internal Network Service attribute on the vMX template resource.

![](screenshots/vlan-auto-admin-only.png)

    
### OpenStack-specific

Note: Tested on Newton only

#### 

 
 
## Implementation details

The vMX template resource has port subresources with names like ge-0-0-0. The port family is marked Locked by Default = false
so the same template resource can be used in multiple simultaneous reservations. To use multiple vMX instances in the same reservation,
create more copies of the vMX template resource.

The vMX template resource has a hook function orch_hook_during_provisioning that will be called by the hook_setup setup script
in parallel to standard app deployments.

### Setup

hook_setup calls vMX VNF Deployment Resource Driver.orch_hook_during_provisioning.

1. Add new vCP app and vFP app(s) to the reservation, with names based on the template resource name
1. Deploy the vCP and vFP(s) using DeployAppToCloudProviderBulk, producing deployed resources of type vMX VCP and vMX VFP
1. Platform-specific:
    - vSphere
        - Add an auto VLAN ('Auto VLAN' or the service name specified in the Internal Network Service attribute) and connect it to NIC #2 of vCP and vFP(s) 
        - With pyVmomi, add a serial console to the vCP to be accessed through an ESXi port in the range 9300..9330 (managed by a CloudShell number allocation pool)
        - Telnet to vCP serial console
        - Log in as root (no password)
        - Set root password
        - Create username and set password according to resource template attributes
        - Enable SSH
        - Enable DHCP or set static IP on management interface fxp0.0, and determine the final management IP
        - With pyVmomi, determine the mapping from vNIC index (e.g. Network adapter 3) to MAC address
    - OpenStack
        - Power off the vCP and vFP(s)
        - With the Neutron API, create an isolated network and a subnet 128.0.0.0/24 with ports with IPs 128.0.0.1 for the vCP and 128.0.0.16, 128.0.0.17, ... for the vFP(s)
        - With the Nova API, connect the vCP and vFPs to their dedicated ports on the 128.0.0.0/24 network
        - Create a dummy network and attach it to the vFPs (to be removed later in the deployment)
        - Power on the vCP and vFP(s) 
        - Connect via a WebSocket to the vCP serial console
        - Log in as root (no password)
        - Set root password
        - Create username and set password according to resource template attributes
        - Enable SSH
        - Enable DHCP on management interface fxp0.0
        - Determine the DHCP management IP
1. Wait until the vCP is reachable by SSH
1. SSH to the vCP as root using the new password
1. In the vCP's FreeBSD shell, run 'ifconfig' and wait until all expected interface names appear, such as ge-0-0-0. The first number in the interface name indicates the card number. If 3 vFPs were requested, wait until interfaces with names like ge-0-x-x, ge-1-x-x, and ge-2-x-x all appear. This will indicate that the handshake between the vCP and each vFP has taken place. Record the MAC addresses of all ge-x-x-x interfaces of the vCP. These will be MAC addresses on vFP VMs.
1. Create a new resource to represent the vMX. It can be either the gen1 or gen2 JunOS router shell. The family, model, and driver name are specified in the vMX template resource.
1. Set the IP of the vMX resource to the fxp0.0 IP determined earlier (static or DHCP) 
1. Copy all attributes from the vMX template resource to the new vMX resource. This includes critical attributes like User, Password, Enable Password, SNMP version (v2c recommended), SNMP community string (e.g. public), and Enable SNMP (must be true)
1. Run Autoload on the new vMX resource. This will use the standard JunOS driver for router hardware. 
1. If Autoload fails to detect all expected ports, retry several times
1. Create a virtual L2 switch
    - Create one port subresource for each autoloaded vMX port
        - Name: port#, an incremented number starting from 0
        - Attribute VM Port Full Name with the full resource name of the autoloaded vMX port
        - Attribute VM Port Full Address with the full address path of the autoloaded vMX port
1. Set physical connections between:
    - each new vMX resource port
    - its assigned virtual L2 port
1. Create subresources under each vMX VFP deployed app resource:
    - Names like ge-x-x-X
    - Requested vNIC Name:
        - for vSphere, using the translation map ge-x-x-X -> MAC address -> Network adapter Y
        - for OpenStack, the last number in the port name ge-x-x-X
1. Move connectors from the vMX template resource to the new vMX resource
1. Add a service VNF Cleanup Service with an attached hook vnf_cleanup_orch_hook_post_teardown
1. Platform-specific:
    - OpenStack
        - Power off the vFP(s)
        - Remove the dummy network connection
        - Power on the vFP(s)

### Teardown
1. Apps are destroyed automatically by the default teardown process and the VMs are automatically 
deleted by the cloud provider 
1. The hook vnf_cleanup_orch_hook_post_teardown on the VNF Cleanup Service deletes the virtual L2 and the 
autoloaded vMX resource

### Virtual L2 operation

Connectors to ports on a vMX resource are translated into cloud provider calls 
that reconfigure the underlying VMs

#### Structure
Resources like the following examples will exist after deploying from a vMX template resource that requested 2 cards:

- vMX vSphere 1_4253 (Router/Juniper JunOS Router or CS_Router/Juniper JunOS Router 2G) (autoloaded)
    - Chassis 1
        - em1
        - Module 1
            - SubModule 1
                - ge-0-0-0
                    - MAC Address: 00:50:56:be:50:cb
                - ge-0-0-1
                    - MAC Address: 00:50:56:be:64:ad
                - ...                    
            - SubModule 2
                - ge-1-0-0
                    - MAC Address: 00:50:56:be:50:23
                - ge-1-0-1
                    - MAC Address: 00:50:56:be:64:45
                - ...                    


- vMX vSphere 1_4253_vcp_8998ee87 (VNF Card/vMX VCP)
    - No subresources


- vMX vSphere 1_4253_vfp0_0733d592 (VNF Card/vMX VFP)
    - ge-0-0-0
        - Requested vNIC Name: 4
    - ge-0-0-1
        - Requested vNIC Name: 5
    - ...

- vMX vSphere 1_4253_vfp1_12345678 (VNF Card/vMX VFP)
    - ge-1-0-0
        - Requested vNIC Name: 4
    - ge-1-0-1
        - Requested vNIC Name: 5
    - ...


- vMX vSphere 1_4253 L2 (Switch/VNF Connectivity Manager Virtual L2)
    - port0
        - VM Port Full Address: vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-0
        - VM Port Full Name: vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-0
    - port1
        - VM Port Full Address: vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-1
        - VM Port Full Name: vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-1
    - ...
    - port10
        - VM Port Full Address: vMX vSphere 1_4253_vfp1_0733d592/ge-1-0-0
        - VM Port Full Name: vMX vSphere 1_4253_vfp1_0733d592/ge-1-0-0
    - port11
        - VM Port Full Address: vMX vSphere 1_4253_vfp1_0733d592/ge-1-0-1
        - VM Port Full Name: vMX vSphere 1_4253_vfp1_0733d592/ge-1-0-1
    - ...
   
These physical connections will be set between the virtual L2 and the main vMX resource:

- vMX vSphere 1_4253 L2/port0 <=> vMX vSphere 1_4253/Chassis 1/Module 1/SubModule 1/ge-0-0-0 
- vMX vSphere 1_4253 L2/port1 <=> vMX vSphere 1_4253/Chassis 1/Module 1/SubModule 1/ge-0-0-1
- ...
- vMX vSphere 1_4253 L2/port10 <=> vMX vSphere 1_4253/Chassis 1/Module 1/SubModule 1/ge-1-0-0 
- vMX vSphere 1_4253 L2/port11 <=> vMX vSphere 1_4253/Chassis 1/Module 1/SubModule 1/ge-1-0-1
- ...

After deployment, connectors will have been moved from ports on the vMX template resource such 
as

    vMX Template vSphere 1/ge-0-0-0
    
to ports on the vMX resource such as 

    vMX vSphere 1_4253/Chassis 1/Module 1/SubModule 1/ge-0-0-0


#### ApplyConnectivityChanges on the virtual L2

Because a vMX resource port like

    vMX vSphere 1_4253/Chassis 1/Module 1/SubModule 1/ge-0-0-0

has a physical connection

    vMX vSphere 1_4253 L2/port0 <=> vMX vSphere 1_4253/Chassis 1/Module 1/SubModule 1/ge-0-0-0 
 
the virtual L2 switch driver will receive connect and disconnect requests when visual connectors
going to the vMX resource are connected or disconnected.
 
The virtual L2 driver determines the related card VM, finds the cloud provider of that VM, and 
calls ApplyConnectivityChanges on the cloud provider using ExecuteCommand().

Suppose the user ran Connect on a visual connector going to this port: 

    vMX vSphere 1_4253/Chassis 1/Module 1/SubModule 1/ge-0-0-0

The system would call ApplyConnectivityChanges with a connection request involving 
this port on the virtual L2: 

    vMX vSphere 1_4253 L2/port0

Each connection request contains many details like a VLAN assignment. The connection requests are 
kept mostly as-is, but certain information is overwritten or added.


Attributes of the L2 port resource:

    Resource: vMX vSphere 1_4253 L2/port0
    VM Port Full Name = vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-0
    VM Port Full Address = vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-0

Attributes of the port under the deployed VM card:

    Resource: vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-0
    Requested vNIC Name = 4
    

The base VM card resource is:

    vMX vSphere 1_4253_vfp0_0733d592
    
VM UUID for both vSphere and OpenStack:
 
    api.GetResourceDetails('vMX vSphere 1_4253_vfp0_0733d592').VmDetails.UID
    

The virtual L2 driver updates the connection request JSON:
    
    ...
    "actionTarget": {
        "fullName": "vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-0",
        "fullAddress": "vMX vSphere 1_4253_vfp0_0733d592/ge-0-0-0"
        ...
    },
    "customActionAttributes": [
        ...
        {
            "attributeName": "VM_UUID",
            "attributeValue": <VmDetails.UID value>
        },
        {
            "attributeName": "Vnic Name",
            "attributeValue": "4"
        },
        ...
    ],
    ...

and calls the cloud provider:

    r = ExecuteCommand(resid, cloud_provider_name, 'Resource', 'ApplyConnectivityChanges', [
        InputNameValue('request', json.dumps({
            'driverRequest': {
                'actions': [
                    <single action JSON object as patched above>
                ]
            }
        }))
    ]).Output
    
A connectivity request with multiple actions is split into a series of connection requests with a single action each. 
They are sent to the cloud provider in ascending order by Vnic Name. This is because the OpenStack cloud provider
determines the NIC not from Vnic Name but from the order requests are sent.

The JSON output of the cloud providers ApplyConnectivityChanges calls is accumulated and bundled as the 
output of the virtual L2 ApplyConnectivityChanges. This is critical in order for the system to set attributes 
on the connector that are needed when disconnecting.


On OpenStack, before the connection request is sent, the card VM is powered off by calling the 
cloud provider and powered on again afterward. This is necessary to refresh the VM in OpenStack itself, 
the vMX software, or both.


Note these limitations under OpenStack:
- Interfaces must be chosen starting from the lowest on a card, with no gaps. For example, two connections to the 
first card must use ge-0-0-0 and ge-0-0-1 instead of arbitrary ports like ge-0-0-3 and ge-0-0-6.
- Disconnecting one connection and connecting a new one will not work in general. 
The only safe way to disconnect some connection and later connect some connection is to run Disconnect on all
connectors for that card and then reconnect all of them, either manually doing Connect on each one in 
ascending order (ge-0-0-0 first) or making a bulk call ConnectRoutesInReservation that will send them all to 
the virtual L2 as a batch without gaps at the bottom. 
- It would break if the standard Setup connectivity phase started to spread the connector endpoints across 
multiple calls to ConnectRoutesInReservation and not all connectors for a single vMX were sent in the 
same ApplyConnectivityChanges batch
- It would break if the system stopped translating a ConnectRoutesInReservation call into a single batch 
call to ApplyConnectivityChanges per L2 provider

 