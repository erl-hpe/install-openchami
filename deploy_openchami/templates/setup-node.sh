#! /usr/bin/bash
# SPDX-FileCopyrightText: (C) Copyright 2026 OpenCHAMI a Series of LF Projects, LLC
# SPDX-License-Identifier: MIT

# Phase 1: setup-node
#
# - Reset DNS to original settings (in case they have been changed)
# - Install required packages
# - Create the deployment user (check first, create if absent)
# - Add deployment user to sudoers with NOPASSWD (check first)
# - Copy deployment user's s3cfg file to user's directory
# - Turn on IP forwarding
# - Set up the virtual environment for 'host' mode if applicable
#
# Can be run stand-alone as the deployment user; uses sudo internally
# for all privileged operations.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" > /dev/null && pwd )"
source "${SCRIPT_DIR}/prep_setup.sh"

{%- if not openchami_config.deployment_phases.setup_node %}
info "setup-node: skipping node setup as requested by config"
exit 0
{%- else %}

# ── Reset DNS Settings and Clean Out /etc/hosts ────────────────────────
reset_dns
sudo sed -i /etc/hosts -e "/{{ hosting_config.net_head_domain }}/d"

# ── Install required packages ──────────────────────────────────────────
info "setup-node: installing required packages"

PRE_INSTALL_PACKAGES="\
        epel-release \
{%- for package in hosting_config.extra_packages.pre %}
        {{ package }} \
{%- endfor %}
"
PACKAGES="\
{%- if deployment_mode == 'host' %}
        libvirt \
        qemu-kvm \
        virt-install \
        virt-manager \
{%- endif %}
        dnsmasq \
        podman \
        buildah \
        git \
        ansible-core \
        openssl \
        nfs-utils \
        s3cmd \
        make \
        rpmdevtools \
{%- if openchami_config.ochami.build %}
        scdoc \
{%- endif %}
{%- for package in hosting_config.extra_packages.main %}
        {{ package }} \
{%- endfor %}
"
sudo dnf -y check-update || true
# shellcheck disable=SC2086
sudo dnf install -y ${PRE_INSTALL_PACKAGES}
# shellcheck disable=SC2086
sudo dnf -y install ${PACKAGES}

{%- if deployment_mode == 'host' %}
sudo systemctl enable --now libvirtd
{%- endif %}

{%- if openchami_config.ochami.build %}
# Install latest stable Go (needed to build ochami)
sudo dnf remove -y golang || true
sudo rm -rf /usr/local/go
sudo rm -f /usr/bin/go
GOLANG_VERSION="$(curl -s 'https://go.dev/VERSION?m=text' | head -1)"
GOLANG_ARCH="$(derive_architecture)"
curl -s -o "${DEPLOY_DIR}/golang.tgz" \
     "https://dl.google.com/go/${GOLANG_VERSION}.linux-${GOLANG_ARCH}.tar.gz"
(cd /usr/local; sudo tar xzf "${DEPLOY_DIR}/golang.tgz")
sudo ln -sf /usr/local/go/bin/go /usr/bin/go
{%- endif %}

# ── Create the deployment user ─────────────────────────────
info "setup-node: checking/creating deployment user '${DEPLOY_USER}'"

if ! getent group "${DEPLOY_GROUP}" > /dev/null; then
    info "setup-node: creating primary group '${DEPLOY_GROUP}'"
    sudo groupadd "${DEPLOY_GROUP}"
else
    info "setup-node: primary group '${DEPLOY_GROUP}' already exists, skipping"
fi

{%- for group in manifest.deployment_user.supplementary_groups %}
if ! getent group "{{ group }}" > /dev/null; then
    info "setup-node: creating supplementary group '{{ group }}'"
    sudo groupadd "{{ group }}"
else
    info "setup-node: supplementary group '{{ group }}' already exists, skipping"
fi
{%- endfor %}

if ! getent passwd "${DEPLOY_USER}" > /dev/null; then
    info "setup-node: creating user '${DEPLOY_USER}'"
    sudo useradd -g "${DEPLOY_GROUP}" "${DEPLOY_USER}"
else
    info "setup-node: user '${DEPLOY_USER}' already exists, skipping"
fi

{%- for group in manifest.deployment_user.supplementary_groups %}
if getent group "{{ group }}" > /dev/null; then
    if ! id -nG "${DEPLOY_USER}" | grep -qw "{{ group }}"; then
        info "setup-node: adding '${DEPLOY_USER}' to supplementary group '{{ group }}'"
        sudo usermod -aG "{{ group }}" "${DEPLOY_USER}"
    else
        info "setup-node: '${DEPLOY_USER}' already in group '{{ group }}', skipping"
    fi
fi
{%- endfor %}

# ── Grant passwordless sudo ────────────────────────────────
info "setup-node: checking/granting passwordless sudo for '${DEPLOY_USER}'"
SUDOERS_LINE="${DEPLOY_USER} ALL=(ALL) NOPASSWD: ALL"
if sudo grep -qF "${SUDOERS_LINE}" /etc/sudoers 2>/dev/null; then
    info "setup-node: passwordless sudo entry for '${DEPLOY_USER}' already present, skipping"
else
    sudo sed -i -e "/[[:space:]]*${DEPLOY_USER}/d" /etc/sudoers
    echo "${SUDOERS_LINE}" | sudo tee -a /etc/sudoers > /dev/null
    info "setup-node: passwordless sudo entry added for '${DEPLOY_USER}'"
fi

# ── Copy the deployment user's .s3cfg ─────────────────────────────────
info "setup-s3-and-registry: installing .s3cfg for '${DEPLOY_USER}'"
s3cfg=~{{ manifest.deployment_user.username }}/.s3cfg
sudo cp "${DEPLOY_DIR}/s3cfg" "${s3cfg}"
sudo chown "${DEPLOY_USER}":"${DEPLOY_GROUP}" "${s3cfg}"

# ── Put cluster information in /etc/hosts ───────────────────
info "setup-node: put cluster information in /e/tc/hosts"

# Set up an /etc/hosts entry for the OpenCHAMI management head node so
# we can use it for certs and for reaching the services before any other
# DNS is set up.
info "setup-node: adding head node (${MANAGEMENT_HEADNODE_IP}) to /etc/hosts"
sudo sed -i /etc/hosts -e "/${MANAGEMENT_HEADNODE_FQDN}/d"
echo "${MANAGEMENT_HEADNODE_IP} ${MANAGEMENT_HEADNODE_FQDN}" | \
    sudo tee -a /etc/hosts > /dev/null

info "setup-node: turning on IPv4 forwarding"
# Turn on IPv4 forwarding on the management node to allow other nodes
# to reach OpenCHAMI services
sudo sysctl -w net.ipv4.ip_forward=1

{%- if deployment_mode == 'host' %}
# ── Create Virtual Networks for Host Based Networking ─────
#
# First clean up what is there then add the ones we need
info "setup-node: removing the virtual network for the compute node VM(s) to use"
info "error messages are expected here if the network is not defined already"
sudo virsh net-destroy {{ hosting_config.cluster_net_name }} || true
sudo virsh net-undefine {{ hosting_config.cluster_net_name }} || true

info "setup-node: configuring a virtual network for the compute node VM(s) to use"
sudo virsh net-define /opt/workdir/openchami-net.xml
sudo virsh net-start {{ hosting_config.cluster_net_name }}
sudo virsh net-autostart {{ hosting_config.cluster_net_name }}

# While we are at it, also add the managed nodes' hostnames and IP
# addresses to /etc/hosts because, since we are in 'host' mode, we are
# not going to be using any other DNS for cluster host naming.
#
# XXX - At the moment we are using the first IP address in the first
#       interface. A better scheme should really be found using the
#       network name, the cluster network name and the interface name,
#       but I think that needs to be done in the python code not in
#       the shell code.
{%- for node in nodes %}
info "setup-node: adding managed node {{ node.hostname }} to /etc/hosts"
NID="$(printf "nid-%3.3d"  {{ node.nid }})"
NID_FQDN="${NID}.{{ hosting_config.net_head_domain }}"
NODE_FQDN="{{ node.hostname }}.{{ hosting_config.net_head_domain }}"
NODE_IP="{{ node.interfaces[0].ip_addrs[0].ip_addr }}"
sudo sed -i /etc/hosts -e "/${NODE_FQDN}/d"
echo "${NODE_IP} ${NODE_FQDN} {{ node.hostname }} ${NID_FQDN} ${NID}" | \
    sudo tee -a /etc/hosts > /dev/null
{%- endfor %}
{%- endif %}
{%- endif %}
info "setup-node: complete"
