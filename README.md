# OpenCHAMI Installer

The OpenCHAMI Installer consists of a Python wrapper and a base
configuration designed to deploy OpenCHAMI onto a host node using the
quadlet implementation of OpenCHAMI described in the [OpenCHAMI
Tutorial](https://openchami.org/docs/tutorial/) using a "`host` mode"
configuration that creates and boots a single virtual managed
(compute) node co-resident on the OpenCHAMI headnode host. The
installer can be used either to deploy OpenCHAMI and the virtual
managed node on physical hardware or on a virtual machine, as long as
the virtual machine supports nested virtualization and has sufficient
resources to run both the OpenCHAMI headnode software and the virtual
managed node.

The OpenCHAMI Installer can also run in a "`cluster` mode" in which
the OpenCHAMI head node and all managed nodes are connected to a
physical or virtual network cluster where the managed nodes are not
co-resident on the host node. In this case, the assumption is that the
managed nodes can be powered on, powered off, and reset using RedFish
calls to RedFish instances running on Base Board Management
Controllers (BMCs) accesible across a network from the headnode.

  __NOTE: The 'cluster' mode is still under development and not quite
  ready for use.__

## System Requirements

At present, the OpenCHAMI Installer uses the 'dnf' package manager,
which is an RPM based package manager primarily used on RedHat
systems. The installer expects to run on a Rocky Linux or similar
distribution of Linux. Also, at present, OpenCHAMI runs most readily
on x86-64 architecture systems. The installer also expects an x86-64
architecture.

  __NOTE: The architecture limitation is under investigation and
  should be cleared up soon.__

For `host` mode operation, the OpenCHAMI Installer recommends a
minimum of 4GB of memory, 4 CPU cores and 40GB of free disk space on
the headnode.

__NOTE: while the tutorial claims to be able to deploy an OpenCHAMI
system in 20GB of free disk space, the first deployment consumes quite
a bit of that space permanently, causing subsequent deployment to run
out of space for transient data. With 40GB there is enough room to
handle the up-front consumption and leave enough room for transient
consumption on each subsequent deployment.__

The installer requires a minimum version 3.9 of Python installed on
the headnode.

The user running the OpenCHAMI Installer must either be `root` or have
`sudo` access on the headnode.

## Installing and Running the Installer

The installer is installed on the OpenCHAMI headnode when the
OpenCHAMI Release RPM is installed. This permits the installer to
track with versions of the OpenCHAMI Release repository. The steps
involved in installing and running the installer are as follows:

1. Install the OpenCHAMI Release RPM on the headnode -- see the
[OpenCHAMI Release README](https://github.com/OpenCHAMI/release/blob/main/README.md#openchami-releases) for more information.

2. Use the OpenCHAMI Installer to prepare the headnode for OpenCHAMI
installation:

```shell
sudo python3 -m install_openchami -p
```

3. Use the OpenCHAMI Installer to install OpenCHAMI and start a
virtual compute node:

```shell
sudo python3 -m install_openchami
```

Once this completes, wait a few minnutes for your compute node to be
ready. When it is ready, log in as the deployment user (by default
this is `rocky`) and run:

```shell
ssh root@compute-001
```

to log into your virtual compute node.

## Configuration

The OpenCHAMI Installer contains a rich configuration that allows for
both future changes to OpenCHAMI and changes to the way OpenCHAMI is
installed on your headnode. This configuration, in turn drives the
creation of configuration files and scripts used internally to install
OpenCHAMI. The base configuration drives an installation quite similar
to the OpenCHAMI tutorial with virtual managed (compute) nodes. This
base configuration can be modified at run time by providing the paths
to one or more YAML format configuration overlays on the command line.

For example, to add a second managed node, all that is required is a
configuration overlay that defines two nodes. Something like this
would work:

```yaml
nodes:
  # This is the original node specification for the virtual
  # compute node provided in the base configuration.
  - xname: x2000c0s0b0n0
    bmc_xname: x2000c0s0b0
    cluster_net_interface: openchami-net
    hostname: compute-001
    name: x2000c0s0b0n0
    nid: 1
    node_group: compute
    interfaces:
      - network_name: openchami-net
        mac_addr: 52:54:00:62:65:ad
        ip_addrs:
        - name: openchami-net
          ip_addr: 172.16.0.1
  # This is the new second node specification.
  #
  # The 'xname' and 'name' fields need to be different from the
  # first virtual compute node and need to have the same value.
  - xname: x2000c0s0b0n1
    # The bmc_xname can be the same (in a real cluster, this would
    # specify the actual name of the BMC)
    bmc_xname: x2000c0s0b0
    cluster_net_interface: openchami-net
    # The hostname must be different from the first virtual compute
    # node.
    hostname: compute-002
    # The name must match the xname
    name: x2000c0s0b0n1
    # The nid value reflects the fact that this is the second
    # compute node.
    nid: 2
    node_group: compute
    interfaces:
      - network_name: openchami-net
        # The MAC address must be different from that of the
        # first compute node. For virtual compute nodes
        # this can safely be randomly chosen as long as the changes are
        # in the rightmost three octets. For a real cluster, this must
        # reflect the MAC address actually in use on the node.
        mac_addr: 52:54:00:62:ad:65
        ip_addrs:
        - name: openchami-net
          # The ip_addr must be different from that of the first virtual
          # compute node. The base configuration uses a /24 network as the
          # virtual cluster network, so choose an IP address that only
          # changes the rightmost octet unless you are also reconfiguring
          # the virtual cluster network.
          ip_addr: 172.16.0.2
```

When creating a configuration overlay, it helps to know what the
configuration to be overlaid looks like, and what the configuration
looks like after applying the overlay. There are two options to the
installer that allow you to see the contents of the OpenCHAMI
Installer configuration. The first option dumps out the entire contents
of the base configuration file, complete with comments explaining the
pieces. This is a good place to start:

```shell
python3 -m install_openchami -b
```

From there you can cut and paste the necessary pieces to create new
configuration overlays.

The second option dumps out the final configuration after applying
your configuration overlay(s):

```shell
python3 -m install_openchami -c my_overlay_file.yaml
```

This configuration may be in a different order from the base
configuration and is not commented, but it allows you to verify that
your configuration changes were applied the way you want them.

To validate your OpenCHAMI Installer final configuration, use:

```shell
python3 -m install_openchami -v my_overlay_file.yaml
```

This helps ensure that your configuration is correct and
consistent, and, where possible, required system elements are in place
to support the configuration you have created.

Install your newly configured OpenCHAMI system by first preparing the
host node using:

```shell
sudo python3 -m install_openchami -p my_overlay_file.yaml
```

then installing the new configuration using:

```shell
sudo python3 -m install_openchami my_overlay_file.yaml
```

If all went well, there are now two virtual compute nodes and the
deployment user (by default `rocky`) is set up to SSH to either
one as `root`:

```shell
ssh root@compute-001
```

or

```
ssh root@compute-002
```

The OpenCHAMI Installer has been designed to be able to be run
correctly multiple times on the same host with new configurations, so
your new system should deploy cleanly with the new configuration.

## Limitations

The OpenCHAMI Installer currently has the following
limitations. Except where otherwise noted, solutions to these are
being investigated and implemented:

- the 'cluster' mode is experimental
- processor architectures other than x86-64 are not supported
- there is no 'remove' operation in the OpenCHAMI Installer
- while the OpenCHAMI Installer tries to be re-usable, it is not
  perfectly idempotent, so situations may arise where re-running the
  installer fails leaving the host in an inconsitent state. There are
  currently no known instances of this, but with arbitrary
  configuration overlays, not every case can be tested.
- the minimum configuration of the headnode can host up to two virtual
  managed nodes. To host more than that, use a host with more CPU,
  Memory and Disk resources.
