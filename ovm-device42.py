#!/usr/bin/python

import requests
import json
import time
import uuid
import base64
import ConfigParser
import os
import sys

#taken from the Oracle VM Web Services API Developer's Guide
def check_manager_state(baseUri,s):
    while True:
        r=s.get(baseUri+'/Manager')
        manager=r.json()
        if manager[0]['managerRunState'].upper() == 'RUNNING':
            break

        time.sleep(1)
    return

#Get a list of VMs on a host
def get_vms(baseUri,s):
    r=s.get(baseUri+'/Vm')
    return r.json()

#Get all the specs from a VM instance
def get_vmDetails(vm):
    vmdata = {}

    vmdata.update({'type': 'virtual'})
    vmdata.update({'virtual_subtype': 'oracle_vm'})
    if vm['serverId'] is not None:
        vmdata.update({'virtual_host': vm['serverId']['name']})
    else:
         vmdata.update({'virtual_host_clear': 'yes' })
    vmdata.update({'name': vm['name']})
    vmdata.update({'memory': vm['currentMemory']})
    vmdata.update({'cpucount': vm['currentCpuCount']})
    vmdata.update({'os': vm['osType']})

    if vm['vmRunState'] == 'RUNNING':
        vmdata.update({'in_service': 'yes'})
    else:
        vmdata.update({'in_service': 'no'})
        
    #convert uuid to proper format
    systemuuid = str(uuid.UUID(vm['id']['value'].replace(':','')))
    vmdata.update({'uuid': systemuuid.lower()})
    
    #Oracle VM sets the serial number of the system to be the same as the uuid (at least for HVM/PVHVM)
    #Setting it here means things don't get weird if a VM is discovered with Linux discovery
    vmdata.update({'serial_no': systemuuid.lower()})
    
    return vmdata

#Get mac addresses and IP info for a VM
def get_virtualNicDetails(vm):
    vniclist = []
    
    for vnic in vm['virtualNicIds']:
        vnicdata = {}
        vnicdata.update({'macaddress': vnic['name']})
        vnicdata.update({'device': vm['name']})
        vniclist.append(vnicdata)
        
    return vniclist

#Get info about physical ethernet ports in a host
def get_ethernetPortDetails(baseUri,s,id):
    r=s.get(baseUri +'/EthernetPort/' + id)

    ethernetPort = r.json()
    ethdata = {}

    ethdata.update({'macaddress': ethernetPort['macAddress']})
    ethdata.update({'port_name': ethernetPort['interfaceName']})
    ethdata.update({'device': ethernetPort['serverId']['name']})
    ethdata.update({'override': 'smart'})

    if ethernetPort['ipaddresses'] is not None:
        for ip in ethernetPort['ipaddresses']:
            ethdata.update({'ipaddress': ip['address']})
            ethdata.update({'tag': ethernetPort['interfaceName']})

    return ethdata

#get a list of all OVS hosts managed by this manager
def get_servers(baseUri,s):
    r=s.get(baseUri+'/Server')
    return r.json()

#Get all the specs from a OVS host
def get_serverDetails(server):
    sysdata = {}

    sysdata.update({'name': server['hostname']})
    sysdata.update({'memory': server['memory']})
    sysdata.update({'cpucount': server['populatedProcessorSockets']})
    sysdata.update({'cpucore': server['coresPerProcessorSocket']})
    sysdata.update({'cpupower': int(server['processorSpeed'] / 1000)})
    sysdata.update({'serial_no': server['serialNumber']})
    sysdata.update({'hardware': server['productName']})
    sysdata.update({'manufacturer': server['manufacturer']})
    sysdata.update({'os': 'Oracle VM Server'})
    sysdata.update({'osver': server['ovmVersion'].split('-')[0]})
    sysdata.update({'osverno': server['ovmVersion'].split('-')[1]})
    sysdata.update({'is_it_virtual_host': 'yes'})

    #convert uuid to proper format
    systemuuid = str(uuid.UUID(server['id']['value'].replace(':','')))
    sysdata.update({'uuid': systemuuid.lower()})

    #set the appropriate value for 'in_service'
    if server['serverRunState'] == 'RUNNING':
        sysdata.update({'in_service': 'yes'})
    else:
        sysdata.update({'in_service': 'no'})

    return sysdata

def main():
    #parse the config file
    config = ConfigParser.ConfigParser()
    config.readfp(open(os.path.join(sys.path[0],'ovm-device42.cfg')))
    ovmusername = config.get('ovm','username')
    ovmpassword = config.get('ovm','password')
    baseUri = config.get('ovm','baseUri')
    d42username = config.get('device42','username')
    d42password = config.get('device42','password')
    device42Uri = config.get('device42','baseUri')
    
    #start a session with the Oracle VM Manager
    s=requests.Session()
    s.auth=(ovmusername, ovmpassword)
    s.headers.update({'Accept': 'application/json', 'Content-Type': 'application/json'})
    check_manager_state(baseUri,s)

    dsheaders = {'Authorization': 'Basic ' + base64.b64encode(d42username + ':' + d42password), 'Content-Type': 'application/x-www-form-urlencoded'}

    #get a list of servers
    for server in get_servers(baseUri, s):
        #get details of a server then post to device42
        print 'Processing Oracle VM Server ' + server['name']
        sysdata = get_serverDetails(server)
        r=requests.post(device42Uri+'/api/1.0/device/',data=sysdata,headers=dsheaders)

	#get a list of ethernet ports in the server
        for ethernetPort in server['ethernetPortIds']:
            #get details on the port then post to device42
            portDetails = get_ethernetPortDetails(baseUri,s,ethernetPort['value'])
            r=requests.post(device42Uri+'/api/1.0/macs/',data=portDetails,headers=dsheaders)
            if 'ipaddress' in portDetails:
                r=requests.post(device42Uri+'/api/1.0/ips/',data=portDetails,headers=dsheaders)

    #get a list of every VM
    for vm in get_vms(baseUri, s):
        #ignore VMs that are actually templates
        if vm['vmRunState'] != 'TEMPLATE':
            #get data on the VM then post it
            print 'Processing virtual machine ' + vm['name']
            vmdata = get_vmDetails(vm)
            r=requests.post(device42Uri+'/api/1.0/device/',data=vmdata,headers=dsheaders)
            
            #make sure the name of the device matches so we dont end up creating a new device for this mac address
            r=requests.get(device42Uri+'/api/1.0/devices/serial/'+vmdata['serial_no']+'/',headers=dsheaders)
            existingname = r.json()['name']
            for vnic in get_virtualNicDetails(vm):
                if existingname != vnic['device']:
                    vnic.update({'device': existingname})
                r=requests.post(device42Uri+'/api/1.0/macs/',data=vnic,headers=dsheaders)

if __name__ == '__main__': 
    main()
