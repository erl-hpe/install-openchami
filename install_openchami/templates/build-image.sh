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

# Report a failure message on stderr
function _bi_fail() {
    local func=${FUNCNAME[1]:-"unknown-function"} # Calling function
    local message="${*:-"failing for no specified reason"}"
    echo "${func}: ${message}" >&2
    return 1
}

function build-image() (
    set -e
    local config="${1}"; shift || _bi_fail "image config file not specified"
    # Build with the specified builder. Default to using the RH9 builder
    local builder="${1:-"ghcr.io/openchami/image-build-el9:v0.1.1"}"
    [[ -f "${config}" ]] || _bi_fail "${config} not found"
    podman run \
           --network=host \
           --rm \
           --device /dev/fuse \
           -e S3_ACCESS=admin \
           -e S3_SECRET=admin123 \
           -v "$(realpath "${config}")":/home/builder/config.yaml:Z \
           ${EXTRA_PODMAN_ARGS} \
           "${builder}" \
           image-build \
           --config config.yaml \
           --log-level DEBUG || _bi_fail "cannot build image defined in ${config}"
)

function build-image-rh9() {
    local config="${1}"; shift || _bi_fail "image config file not specified"
    build-image "${config}"
}

function build-image-rh8() {
    local config="${1}"; shift || _bi_fail "image config file not specified"
    local builder="ghcr.io/openchami/image-build:v0.1.0"
    build-image "${config}" "${builder}"
}

function generate-boot-config() {
    local image_subpath="${1}"; shift || _bi_fail "image subpath (example 'compute/debug') not provided as first argument"
    local headnode_ip="${1}"; shift || _bi_fail "management head-node IP address not provided as second argument"
    local macs="$(for mac in "$@"; do echo "${mac}"; done)"
    [[ "${macs}" != "" ]] || _bi_fail "no target node MAC addresses provided"
    cd /opt/workdir/boot
    local uris="$(s3cmd ls -Hr s3://boot-images | grep "${image_subpath}" | \
                        awk '{print $4}' | \
                        sed "s-s3://-http://${headnode_ip}:9000/-" | \
                        xargs)"
    local uri_img="$(echo "${uris}" | cut -d' ' -f1)"
    [[ "${uri_img}" != "" ]] || _bi_fail "no disk image found that matches '${image_subpath}'"
    local uri_initramfs="$(echo "${uris}" | cut -d' ' -f2)"
    [[ "${uri_initramfs}" != "" ]] || _bi_fail "no initrd image found that matches '${image_subpath}'"
    local uri_kernel="$(echo "${uris}" | cut -d' ' -f3)"
    [[ "${uri_kernel}" != "" ]] || _bi_fail "no kernel image found that matches '${image_subpath}'"
    cat <<EOF
---
kernel: '${uri_kernel}'
initrd: '${uri_initramfs}'
params: 'nomodeset ro root=live:${uri_img} ip=dhcp overlayroot=tmpfs overlayroot_cfgdisk=disabled apparmor=0 selinux=0 console=ttyS0,115200 ip6=off cloud-init=enabled ds=nocloud-net;s=http://${headnode_ip}:8081/cloud-init'
macs:
$(for mac in ${macs}; do echo "  - ${mac}"; done)
EOF
}

function __bmc_user() {
    local xname="${1}"; shift || _bi_fail "BMC xname not provided for __bmc_user"
    sudo cat /etc/vtds/bmc_info.json | \
        jq -r ".[] | select(.xname == \"${xname}\") | .redfish_username"
}

function __bmc_password() {
    local xname="${1}"; shift || _bi_fail "BMC xname not provided for __bmc_user"
    sudo cat /etc/vtds/bmc_info.json | \
        jq -r ".[] | select(.xname == \"${xname}\") | .redfish_password"
}

function __node_reset() {
    local reset_type="${1}"; shift || _bi_fail "no reset type supplied"
    local node_xname="${1}"; shift || _bi_fail "no node XNAME provided"
    local bmc_xname="${1}"; shift || _bi_fail "no BMC XNAME provided"
    local bmc_url="https://${bmc_xname}/redfish/v1/Systems"
    local node_action="${node_xname}/Actions/ComputerSystem.Reset"

    curl -k \
         -u "$(__bmc_user "${bmc_xname}"):$(__bmc_password "${bmc_xname}")" \
         -H "Content-Type: application/json" \
         -X POST -d "{\"ResetType\": \"${reset_type}\" }" \
         "${bmc_url}/${node_action}"
}

function power-on-node() {
    __node_reset "On" "$@"
}

function power-off-node() {
    __node_reset "ForceOff" "$@"
}

function restart-node() {
    __node_reset "ForceRestart" "$@"
}

function get-ochami-token() {
    export DEMO_ACCESS_TOKEN="$(sudo bash -lc 'gen_access_token')"
}
