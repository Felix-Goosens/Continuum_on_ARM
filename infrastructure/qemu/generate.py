"""\
Generate a QEMU configuration file
The file is generated from scratch instead of using an existing template file as
too many things can change depending on user input.
"""

import logging
import sys
import re
from pathlib import Path


DOMAIN = """\
<domain type='kvm'>
    <name>%s</name>
    <memory>%i</memory>
    <os %s>
        <type %s>hvm</type>
        <boot dev="hd" />
        %s
    </os>
    <features>
        <acpi/>
    </features>
    <vcpu placement="static">%i</vcpu>
    %s
    <cputune>
        <period>%i</period>
        <quota>%i</quota>
%s
    </cputune>
    <devices>
        <interface type='bridge'>
            <source bridge='%s'/>
            <model type='e1000'/>
        </interface>
        <disk type='file' device='disk'>
            <driver type='qcow2' cache='none'/>
            <source file='/var/lib/libvirt/images/%s.qcow2'/>
            <target dev='vda' bus='virtio'/>
        </disk>
        <disk type='file' device='disk'>
            <source file='/var/lib/libvirt/images/user_data_%s.img'/>
            <target dev='vdb' bus='virtio'/>
        </disk>
        <console type="pty">
           <target type="serial" port="1"/>
        </console>
    </devices>
</domain>
"""

USER_DATA = """\
#cloud-config
hostname: %s
fqdn: %s
manage_etc_hosts: true
users:
  - name: %s
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: users, admin
    home: /home/%s
    shell: /bin/bash
    lock_passwd: false
    ssh-authorized-keys:
      - %s
ssh_pwauth: false
disable_root: false
chpasswd:
  list: |
     %s:password
  expire: False
write_files:
- path: /etc/cloud/cloud.cfg.d/99-custom-networking.cfg
  permissions: '0644'
  content: |
    network: {config: disabled}
- path: /etc/netplan/new-config.yaml
  permissions: '0644'
  content: |
    network:
      version: 2
      ethernets:
        %s:
          dhcp4: false
          addresses: [%s/16]
          gateway4: %s
          nameservers:
            addresses: [1.1.1.1, 8.8.8.8]
            search: []
runcmd:
 - rm /etc/netplan/50-cloud-init.yaml
 - netplan generate
 - netplan apply
# written to /var/log/cloud-init-output.log
final_message: "The system is finally up, after $UPTIME seconds"
"""


def find_bridge(machine, bridge):
    """Check if bridge <bridge> is available on the system.

    Args:
        machine (Machine object): Object representing the physical machine we currently use
        bridge (str): Bridge name to check

    Returns:
        int: Bool representing if we found the bridge on this machine
    """
    output, error = machine.process(
        "brctl show | grep '^%s' | wc -l" % (bridge), shell=True
    )
    if error != [] or output == []:
        logging.error("ERROR: Could not find a network bridge")
        sys.exit()

    return int(output[0].rstrip())


def start(config, machines):
    """Create QEMU config files for each machine

    Args:
        config (dict): Parsed configuration
        machines (list(Machine object)): List of machine objects representing physical machines
    """
    logging.info("Start writing QEMU config files for cloud / edge")

    # Get the SSH public key
    home = str(Path.home())
    f = open(home + "/.ssh/id_rsa_benchmark.pub", "r")
    ssh_key = f.read().rstrip()
    f.close()

    # ------------------------------------------------------------------------------------------------
    # NOTE
    # If an error occurs in the following lines, please:
    # 1. Comment this part of the code between the two ---- lines out
    # 2. Set the "bridge_name" variable to the name of your bridge (e.g. br0, virbr0, etc.)
    # 3. Set the gateway variable to the IP of your gateway (e.g. 10.0.2.2, 192.168.122.1, etc)
    # ------------------------------------------------------------------------------------------------
    # Find out what bridge to use
    bridge = find_bridge(machines[0], "br0")
    bridge_name = "br0"
    if bridge == 0:
        bridge = find_bridge(machines[0], "virbr0")
        bridge_name = "virbr0"
        if bridge == 0:
            logging.error("ERROR: Could not find a network bridge")
            sys.exit()

    # Get gateway address
    output, error = machines[0].process(
        "ip route | grep ' %s '" % (bridge_name), shell=True
    )
    if error != [] or output == []:
        logging.error("ERROR: Could not find gateway address")
        sys.exit()

    gateway = 0
    pattern = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")
    gatewaylists = [pattern.findall(line) for line in output]

    if bridge_name == "br0":
        # For br0, pick gateway of machine
        gateway = gatewaylists[0][0]
    else:
        # For virbr0
        for gatewaylist in gatewaylists:
            if len(gatewaylist) > 1:
                if gateway != 0:
                    logging.error("ERROR: Found multiple gateways")
                    sys.exit()

                gateway = gatewaylist[1].rstrip()
    # ------------------------------------------------------------------------------------------------

    cc = config["infrastructure"]["cloud_cores"]
    ec = config["infrastructure"]["edge_cores"]
    pc = config["infrastructure"]["endpoint_cores"]

    period = 100000
    pinnings = []

    for i, machine in enumerate(machines):
        # Counter for pinning vcpu to physical cpu
        start_core = 0

        efi = ""
        machine_spec = ""
        loader = ""
        host_passthrough = ""

        interface = "ens2"

        if(machine.arch == "aarm64"):
            efi = "firmware='efi'"
            machine_spec = "arch='aarch64' machine='virt'"
            loader = "<loader readonly='yes' secure='no'/>"
            host_passthrough = "<cpu mode='host-passthrough' />"

            interface = "enp2s1"

        # Clouds
        for ip, name in zip(
            machine.cloud_controller_ips + machine.cloud_ips,
            machine.cloud_controller_names + machine.cloud_names,
        ):
            f = open(".tmp/domain_%s.xml" % (name), "w")
            memory = 1048576 * cc

            if config["infrastructure"]["cpu_pin"]:
                pinnings = [
                    '        <vcpupin vcpu="%i" cpuset="%i"/>' % (a, b)
                    for a, b in zip(range(cc), range(start_core, start_core + cc))
                ]
                start_core += cc

            f.write(
                DOMAIN
                % (
                    name,
                    memory,
                    efi,
                    machine_spec,
                    loader,
                    cc,
                    host_passthrough,
                    period,
                    int(period * config["infrastructure"]["cloud_quota"]),
                    "\n".join(pinnings),
                    bridge_name,
                    name,
                    name,
                )
            )
            f.close()

            f = open(".tmp/user_data_%s.yml" % (name), "w")
            hostname = name.replace("_", "")
            f.write(
                USER_DATA % (hostname, hostname, name, name, ssh_key, name, interface, ip, gateway)
            )
            f.close()

        # Edges
        for ip, name in zip(machine.edge_ips, machine.edge_names):
            f = open(".tmp/domain_%s.xml" % (name), "w")
            memory = 1048576 * ec

            if config["infrastructure"]["cpu_pin"]:
                pinnings = [
                    '        <vcpupin vcpu="%i" cpuset="%i"/>' % (a, b)
                    for a, b in zip(range(ec), range(start_core, start_core + ec))
                ]
                start_core += ec

            f.write(
                DOMAIN
                % (
                    name,
                    memory,
                    efi,
                    machine_spec,
                    loader,
                    ec,
                    host_passthrough,
                    period,
                    int(period * config["infrastructure"]["edge_quota"]),
                    "\n".join(pinnings),
                    bridge_name,
                    name,
                    name,
                )
            )
            f.close()

            f = open(".tmp/user_data_%s.yml" % (name), "w")
            f.write(USER_DATA % (name, name, name, name, ssh_key, name, interface, ip, gateway))
            f.close()

        # Endpoints
        for ip, name in zip(machine.endpoint_ips, machine.endpoint_names):
            f = open(".tmp/domain_%s.xml" % (name), "w")
            memory = 1048576 * pc

            if config["infrastructure"]["cpu_pin"]:
                pinnings = [
                    '        <vcpupin vcpu="%i" cpuset="%i"/>' % (a, b)
                    for a, b in zip(range(pc), range(start_core, start_core + pc))
                ]
                start_core += pc

            f.write(
                DOMAIN
                % (
                    name,
                    memory,
                    efi,
                    machine_spec,
                    loader,
                    pc,
                    host_passthrough,
                    period,
                    int(period * config["infrastructure"]["endpoint_quota"]),
                    "\n".join(pinnings),
                    bridge_name,
                    name,
                    name,
                )
            )
            f.close()

            f = open(".tmp/user_data_%s.yml" % (name), "w")
            f.write(USER_DATA % (name, name, name, name, ssh_key, name, interface, ip, gateway))
            f.close()

        # Base image(s)
        for ip, name in zip(machine.base_ips, machine.base_names):
            f = open(".tmp/domain_%s.xml" % (name), "w")

            f.write(DOMAIN % (name, 1048576, efi, machine_spec, loader, 1, host_passthrough, 0, 0, "", bridge_name, name, name))
            f.close()

            f = open(".tmp/user_data_%s.yml" % (name), "w")
            f.write(USER_DATA % (name, name, name, name, ssh_key, name, interface, ip, gateway))
            f.close()
