#! /usr/bin/bash
#
# MIT License
#
# (C) Copyright 2025-2026 Hewlett Packard Enterprise Development LP
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

# Pick up the common setup for the prepare scripts
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" > /dev/null && pwd )"
source "${SCRIPT_DIR}/prep_setup.sh"

info "preparing platform - install required packages"
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
{%- for package in hosting_config.extra_packages.main %}
        {{ package }} \
{%- endfor %}
"
dnf -y check-update || true
# packages needed before main package list install
dnf install -y ${PRE_INSTALL_PACKAGES}
# packages needed to install and use OpenCHAMI
dnf -y install ${PACKAGES}  # list of packages, should not be quoted

# Don't enable libvirt if we are not running in host mode
{%- if deployment_mode == 'host' %}
systemctl enable --now libvirtd
{%- endif %}

info "preparing platform - create the deployment user '${DEPLOY_USER}'"
if ! getent group "${DEPLOY_GROUP}"; then
    info "creating primary group '{{ group }}' for '${DEPLOY_USER}'"
    groupadd "${DEPLOY_GROUP}"
fi
{%- for group in manifest.deployment_user.supplementary_groups %}
if ! getent group "{{ group }}"; then
    info "creating supplementary group '{{ group }}' for '${DEPLOY_USER}'"
    groupadd "{{ group }}"
fi
{%- endfor %}
if ! getent passwd "${DEPLOY_USER}"; then
    info "creating user '${DEPLOY_USER}'"
    useradd -g "${DEPLOY_GROUP}" "${DEPLOY_USER}"
fi
{%- for group in manifest.deployment_user.supplementary_groups %}
if ! getent group "{{ group }}"; then
    info "adding supplementary group '{{ group }}' to '${DEPLOY_USER}'"
    usermod -aG "{{ group }}" "${DEPLOY_USER}"
fi
{%- endfor %}
# Remove the deployment user from /etc/sudoers and then put it back
# with NOPASSWD access
info "giving user '${DEPLOY_USER}' passwordless sudo access"
sed -i -e "/[[:space:]]*${DEPLOY_USER}/d" /etc/sudoers
echo "${DEPLOY_USER} ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
