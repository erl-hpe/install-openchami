#! /usr/bin/bash
# SPDX-FileCopyrightText: (C) Copyright 2026 OpenCHAMI a Series of LF Projects, LLC
# SPDX-License-Identifier: MIT

# Set up the system level pieces needed to start deploying
# OpenCHAMI. This script is intended to be run by a user with
# passwordless 'sudo' permissions. The base node preparation script
# sets up the user 'rocky' with that before chaining here.

# Common setup for the prepare node scripts

# Special error handling only for scripts, for interactive shells, if
# they ever source this code, error handling will be normal.
[[ "${-}" == *i* ]] || set -o errexit -o errtrace
function error_handler() {
    local filename="${1}"; shift
    local lineno="${1}"; shift
    local exitval="${1}"; shift
    echo "an error occurred -- here is the status of OpenCHAMI Components:" >&2
    sudo systemctl list-dependencies openchami.target >&2
    echo "exiting on error [${exitval}] from ${filename}:${lineno}" >&2
    exit "${exitval}"
}
[[ "${-}" == *i* ]] || trap 'error_handler "${BASH_SOURCE[0]}" "${LINENO}" "${?}"' ERR

function fail() {
    local message="${*:-"failing for no specified reason"}"
    echo "${BASH_SOURCE[1]}:${BASH_LINENO[0]}:[${FUNCNAME[1]}]: ${message}" >&2
}

function info() {
    local message="${*:-"failing for no specified reason"}"
    echo "INFO: ${message}" >&2
}

function derive_architecture() {
    case "$(uname -m)" in
        arm64|aarch64) echo "arm64";;
        amd64|x86_64) echo "amd64";;
        *) {
            fail "unknown platform architecture '$(uname -m)'"
            return 1
        };;
    esac
}

function discovery_version() {
    # The version of SMD changed how ochami needs to feed it manually
    # discovered node data at 2.19. We need an extra option to address
    # that if the version is 2.18 or lower.
    local major=""
    local minor=""
    IFS='.' read -r major minor _ < \
       <( \
          sudo podman ps | \
              grep '/smd:v' | \
              awk '{sub(/^.*:v/, "", $2); print $2 }'\
       )
    if [[ "${major}" -le "2" ]] && [[ "${minor}" -lt "19" ]]; then
       echo "--discovery-version=1"
    fi
}

function node_groups() {
    # Templated mechamism for getting a list of unique node 'group'
    # names from the list of managed nodes.
    sort -u <<EOF
{%- for node in nodes %}
{{ node.node_group }}
{%- endfor %}
EOF
}

function managed_macs() {
    cat <<EOF
{%- for node in nodes %}
{%- for interface in node.interfaces %}
{%- if interface.network_name == node.cluster_net_interface %}
{{ interface.mac_addr }}
{%- endif %}
{%- endfor %}
{%- endfor %}
EOF
}

function find_if_by_addr() {
    addr=${1}; shift || {
        fail "no ip addr supplied when looking up ip interface"
        return 1
    }
    ip --json a | \
        jq -r "\
          .[] | .ifname as \$ifname | \
          .addr_info | .[] | \
              select( .family == \"inet\") | \
              select( (.local) == \"${addr}\" ) | \
              \"\(\$ifname)\" \
        "
}

function save_dns() {
    local connection="${1}"; shift || {
        fail "no connection specified to save"
        return 1
    }
    # Set up to capture the initial DNS settings of connections if
    # this is the first time we are saving the DNS state for this
    # connection. After that, we leave the connection alone.
    [[ -f "${SAVED_DNS}" ]] || mkdir -p "$(dirname ${SAVED_DNS})"
    touch "${SAVED_DNS}"
    if ! grep -q "${connection}" "${SAVED_DNS}"; then
        nmcli connection show "${connection}" | \
            grep -e "ipv[46].dns-search:" \
                 -e "ipv[46].dns:" \
                 -e "ipv[46].ignore-auto-dns:" \
            | sed -e 's/: */ /' \
            | while read -r property val; do
            echo "${connection} ${property} ${val}" >> "${SAVED_DNS}"
        done
    fi
}

function switch_dns() {
    # This function uses nmcli to find and remove all nameservers from
    # the current configuration and then to add back only the local
    # management network IP as a nameserver on the management
    # network. It is complicated because nmcli is complicated...
    local nameserver="${1}"; shift || {
        fail "no nameserver specified to switch to"
        return 1
    }
    local domain="${1}"; shift || {
        fail "no search domain specified"
        return 1
    }
    # First, get the list of connections (interfaces) with nameservers
    # assigned to them...
    local connections=""
    for connection in $(nmcli --terse --fields NAME connection show); do
        nmcli connection show "${connection}" | grep -q 'ipv4.dns:' || continue
        nmcli connection show "${connection}" | grep -q 'ipv4.dns: *--' && continue
        connections="${connections} ${connection}"
    done

    # Save the connection state of each affected connection, if it is
    # not already saved, so that we can restore the original
    # connection state, then strip off the nameserver from each of the
    # affected connections...
    info "switching dns on [${connections}]"
    for connection in ${connections}; do
        info "connection = '${connection}'"
        # shellcheck disable=SC2015
        save_dns "${connection}" && \
            sudo nmcli connection modify "${connection}" ipv4.dns "" && \
            sudo nmcli connection down "${connection}" && \
            sudo nmcli connection up "${connection}" || {
                fail "WARNING: unable to strip NS from '${connection}'"
                # Don't actually fail here, treat it as a warning...
                continue
            }
    done

    # Now find the first interface (nmcli connection) that routes to
    # the desired name server IP address.
    connection="$(ip --json route get "${nameserver}" | jq -r '.[0] | .dev')"
    [[ "${connection}" != "" ]] || {
        fail "no interface found that can reach the DNS server '${nameserver}'"
        return 1
    }

    # Set the nameserver on the connection and put the cluster domain
    # in the search on the same connection
    #
    # shellcheck disable=SC2015
    sudo nmcli connection modify "${connection}" ipv4.dns "${nameserver}" && \
        sudo nmcli connection modify "${connection}" ipv4.dns-search "${domain}" && \
        sudo nmcli connection down "${connection}" && \
        sudo nmcli connection up "${connection}" || {
            fail "unable to add NS to '${connection}'"
            return 1
        }
}

function reset_dns() {
    # Reset DNS on all connections that might have been modified by
    # 'switch_dns' to what they were before the first time
    # 'switch_dns' was called.
    if [[ -f "${SAVED_DNS}" ]]; then
        # Reset all of the saved connections to theis saved values
        #
        # shellcheck disable=SC2002
        cat "${SAVED_DNS}" | while read -r connection property val; do
            if [[ "${val}" == "--" ]]; then
                val=""
            fi
            sudo nmcli connection modify "${connection}" "${property}" "${val}"
        done
        # Cycle the connections down and up to restore the actual
        # run-time behavior.
        connections="$(cut -d ' ' -f 1 "${SAVED_DNS}" | sort -u)"
        for connection in ${connections}; do
            sudo nmcli connection down "${connection}" && \
            sudo nmcli connection up "${connection}"
        done
    fi
}

function yaml_to_json() {
    python3 -c 'import yaml, json, sys; yaml.SafeLoader.yaml_implicit_resolvers.pop(":", None); json.dump(yaml.safe_load(sys.stdin), sys.stdout, indent=2)'
}

# Some useful variables that can be templated
#
# shellcheck disable=SC2034
CLUSTER_DOMAIN="{{ hosting_config.net_head_domain }}"
# shellcheck disable=SC2034
CLUSTER_NAME="{{ hosting_config.cluster_name }}"
# shellcheck disable=SC2034
MANAGEMENT_HEADNODE_IP="{{ hosting_config.net_head_ip }}"
# shellcheck disable=SC2034
MANAGEMENT_HEADNODE_NAME="{{ hosting_config.nethead_hostname }}"
# shellcheck disable=SC2034
MANAGEMENT_HEADNODE_FQDN="{{ hosting_config.net_head_hostname }}.{{ hosting_config.net_head_domain }}"
# shellcheck disable=SC2034
MANAGEMENT_EXT_NAMESERVER="{{ hosting_config.net_head_dns_server }}"
# shellcheck disable=SC2034
MGMT_NET_HEAD_IFNAME="$(find_if_by_addr "${MANAGEMENT_HEADNODE_IP}")"
# shellcheck disable=SC2034
DEPLOY_DIR="{{ manifest.deployment_directory }}"
# shellcheck disable=SC2034
DEPLOY_USER="{{ manifest.deployment_user.username }}"
# shellcheck disable=SC2034
DEPLOY_GROUP="{{ manifest.deployment_user.primary_group }}"
# shellcheck disable=SC2034
S3_API_PORT="{{ openchami_config.s3.api_port }}"
# shellcheck disable=SC2034
S3_CONSOLE_PORT="{{ openchami_config.s3.console_port }}"
# shellcheck disable=SC2034
REGISTRY_API_PORT="{{ openchami_config.registry.api_port }}"
# shellcheck disable=SC2034
SAVED_DNS="${DEPLOY_DIR}/saved_data/dns_settings"
