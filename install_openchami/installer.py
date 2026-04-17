#
# MIT License
#
# (C) Copyright 2026 Hewlett Packard Enterprise Development LP
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

# pylint: disable=consider-using-f-string
"""The home of the Installer class that orchestrates installation.

"""


import sys
from os.path import sep as path_separator
from os import (
    makedirs,
    chown
)
import re
from grp import getgrnam
from pwd import getpwnam
from pathlib import Path

from passlib.pwd import genword as generate_password


from . import BASE_CONFIG_PATH
from .utils import (
    compose_config,
    dump_yaml,
    config_by_path,
    render_manifest,
    run_install_script,
    run_host_prep_script
)
from .error import ContextualError, ConfigError
from . import template


class Installer:
    """The OpenCHAMI installer class that orchestrates installation of
    OpenCHAMI on a system.

    """
    def __init__(self, options, config_overlays):
        """Construct the installer instance using the config overlays
        and options provided from the caller.

        """
        self.config_overlays = config_overlays
        self.options = options
        self.config = None

    def load_config(self):
        """Read in the configuration and attach it to this object

        """
        self.config = compose_config([BASE_CONFIG_PATH] + self.config_overlays)

    def __prep_manifest_file(self, file_key):
        """Prepare the pieces of the manifest file configuration that
        need to be resolved at run time.

        """
        # Stash an owning UID and GID in the file manifest if there is an owner
        file_manifest = config_by_path(
            'manifest.files.%s' % file_key, self.config
        )
        owner = config_by_path(
            'manifest.files.%s.owner' % file_key, self.config, missing_ok=True
        )
        if owner is not None:
            file_manifest['uid'] = getpwnam(owner).pw_uid
        group = config_by_path(
            'manifest.files.%s.group' % file_key, self.config,
            missing_ok=True
        )
        if group is not None:
            file_manifest['gid'] = getgrnam(group).gr_gid

    def __prep_manifest(self):
        """Prepare the contents of the manifest portion of the
        configuration

        """
        # Set up the user id and primary group id of the deployment user
        deploy_user = config_by_path(
            'manifest.deployment_user', self.config
        )
        username = config_by_path(
            'manifest.deployment_user.username', self.config
        )
        prep_host = self.options.get('prep-host', False)
        if not prep_host:
            # Validation already checked, so the user exists
            user_info = getpwnam(username)
            deploy_user['uid'] = user_info.pw_uid
            deploy_user['gid'] = user_info.pw_gid
            deploy_dir = config_by_path(
                'manifest.deployment_directory', self.config
            )
            makedirs(deploy_dir, mode=0o755, exist_ok=True)
            chown(deploy_dir, deploy_user['uid'], deploy_user['gid'])
        annotations = (
            ['host-prep-entrypoint', 'host-prep-support'] if prep_host
            else []
        )
        manifest_files = config_by_path('manifest.files', self.config)
        for file_key, manifest_file in manifest_files.items():
            file_annotations = manifest_file.get('annotations', [])
            matches = [
                file_annotation
                for file_annotation in file_annotations
                if file_annotation in annotations
            ]
            if annotations and not matches:
                # No matching annotations, skip this file
                continue
            self.__prep_manifest_file(file_key)

    def __prep_bmcs(self):
        """Prepare the configuration of BMCs

        """
        # Run through the BMCs and generate redfish passwords for the
        # ones that don't have one explicitly set.
        bmcs = config_by_path('bmcs', self.config)
        for bmc in bmcs.values():
            bmc['password'] = generate_password(length=20)

    def __prep_hosting(self):
        """Prepare the hosting configuration

        """
        # nothing to do, just return

    def __prep_nodes(self):
        """Prepare the 'nodes' section of the config

        """
        # nothing to do, just return

    def __prep_images(self):
        """Prepare the 'images' section of the config

        """
        # Nothing to do, just return

    def prepare(self):
        """Prepare the Installer to install the system by reading in
        the configuration, merging the overlays onto the
        configuration, and generating any configuration data that need
        to be generated.

        """
        self.__prep_manifest()
        self.__prep_bmcs()
        self.__prep_hosting()
        self.__prep_nodes()
        self.__prep_images()
        return 0

    def __check_and_get_dict_key(
            self, key, dictionary, value_type, none_ok=False
    ):
        """Validate and return the contents of a path in the
        configuration, checking that the path exists and has the
        correct type.

        """
        if key not in dictionary:
            if none_ok:
                return None
            raise ConfigError("key '%s' not found" % key)
        if not isinstance(dictionary[key], value_type):
            raise ConfigError(
                "key '%s' is a %s and should be a %s" % (
                    key, str(type(dictionary[key])), str(value_type)
                )
            )
        return dictionary[key]

    def __check_and_get_config_path(
            self, config_path, value_type, none_ok=False
    ):
        """Validate and return the contents of a path in the
        configuration, checking that the path exists and has the
        correct type.

        """
        value = config_by_path(config_path, self.config)
        if value is None and none_ok:
            return value
        if not isinstance(value, value_type):
            raise ConfigError(
                "'%s' has a value of type '%s' and should have a value of "
                "type '%s'" % (
                    config_path, str(type(value)), str(value_type)
                )
            )
        return value

    def __valid_manifest_deploy_dir(self):
        """Make sure the 'deployment_directory' field of the manifest
        is specified, is a string and looks like it might be an
        absolute pathname

        """
        deployment_directory = self.__check_and_get_config_path(
            'manifest.deployment_directory', str
        )
        if deployment_directory[0] != path_separator:
            raise ConfigError(
                "'manifest.deployment_directory' value '%s' is not an "
                "absolute pathname"
            )

    def __valid_manifest_deploy_user(self):
        """Validate the 'deployment_user' section of the manifest

        """
        self.__check_and_get_config_path(
            'manifest.deployment_user', dict
        )
        username = self.__check_and_get_config_path(
            'manifest.deployment_user.username', str
        )
        primary_group = self.__check_and_get_config_path(
            'manifest.deployment_user.primary_group', str
        )
        supplementary_groups = self.__check_and_get_config_path(
            'manifest.deployment_user.supplementary_groups', list
        )
        for group in supplementary_groups:
            if not isinstance(str, group):
                raise ConfigError(
                    "supplementary group '%s' in "
                    "'manifest.deployment_user.supplmentary_groups "
                    "should be a string but is of type '%s'" % (
                        str(group), str(type(group))
                    )
                )
        if not self.options['prep-host']:
            try:
                user_info = getpwnam(username)
            except KeyError as err:
                raise ConfigError(
                    "'manifest.deployment_user.username' user '%s' is not "
                    "provisioned as a user on this host "
                    "try running installer in 'prep-host' mode "
                    "before installing OpenCHAMI" % username
                ) from err
            try:
                primary_info = getgrnam(primary_group)
            except KeyError as err:
                raise ContextualError(
                    "error looking up deployment user primary "
                    "group '%s' (try running installer in 'prep-host' "
                    "mode before installing OpenCHAMI) - %s" % (
                        primary_group, str(err)
                    )
                ) from err
            try:
                supplementary_info = [
                    getgrnam(group)
                    for group in supplementary_groups
                ]
            except KeyError as err:
                raise ContextualError(
                    "error looking up deployment user supplmentary "
                    "groups (try running installer in 'prep-host' "
                    "mode before installing OpenCHAMI) - %s" % str(err)
                ) from err
            if user_info.pw_gid != primary_info.gr_gid:
                raise ConfigError(
                    "deployment user '%s' does not have group '%s' as "
                    "its primary group try running installer in 'prep-host' "
                    "mode before installing OpenCHAMI" % (
                        username,
                        primary_group
                    )
                )
            for group_info in supplementary_info:
                if username not in group_info.gr_mem:
                    raise ConfigError(
                        "user '%s' is not a member of group '%s' as a "
                        "supplementary group try running installer in "
                        "'prep-host' mode before installing OpenCHAMI" % (
                            username,
                            group_info.gr_name
                        )
                    )

    def __valid_manifest_file_gen(self, file_key):
        """For generated manifest file items (items that have no
        template specified) validate the manifest contents with
        respect to generation parameters.

        """
        config_path = self.__check_and_get_config_path(
            "manifest.files.%s.generation.config_path" % file_key, str
        )
        # Make sure that the configuration path from which the
        # template file will be composed is, in fact, present and a
        # dictionary.
        self.__check_and_get_config_path(config_path, dict)

        # Make sure the generation type is either YAML or JSON
        gen_type = self.__check_and_get_config_path(
            "manifest.files.%s.generation.type" % file_key, str
        )
        if gen_type not in ('yaml', 'json'):
            raise ConfigError(
                "'manifest.files.%s.generation.type' is '%s' but must "
                "be either 'yaml' or 'json'" % (file_key, gen_type)
            )

    def __valid_manifest_file_tpl(self, file_key):
        """For template based manifest file items (items with a
        template specified) validate the template information.

        """
        template_name = self.__check_and_get_config_path(
            "manifest.files.%s.template_name" % file_key, str
        )
        template_path = Path(template(template_name))
        if not template_path.exists():
            raise ConfigError(
                "(internal) missing template file '%s' "
                "referenced from 'manifest.files.%s.template_name'" % (
                    template_name, file_key
                )
            )

    def __valid_manifest_file(self, file_key):
        """Validate the contents of a manifest item

        """
        # Look at the template name for the specified file
        # structure. It it is None, then the template is generated, if
        # not the template is a file. It needs to be explicitely None
        # to be generated, missing is not okay.
        template_name = self.__check_and_get_config_path(
            "manifest.files.%s.template_name" % file_key, str, none_ok=True
        )
        if template_name is None:
            self.__valid_manifest_file_gen(file_key)
        else:
            self.__valid_manifest_file_tpl(file_key)
        # Verify that 'target' is specified and is a string
        self.__check_and_get_config_path(
            "manifest.files.%s.target" % file_key, str
        )
        # Verify that 'mode' is specified and is a legal value
        mode = self.__check_and_get_config_path(
            "manifest.files.%s.mode" % file_key, str
        )
        mode_re = re.compile("^[0-7][0-7][0-7]$")
        if not mode_re.match(mode):
            raise ConfigError(
                "'manifest.files.%s.mode' has a value of '%s' which "
                "is invalid since it should be a three digit octal "
                "value" % (file_key, mode)
            )
        # The owner and group fields in a file manifest are optional,
        # but they have to be strings and exist on the installation
        # host if they are present. Also, if we are doing host
        # preparation, an explicit and existing owner and group must
        # be present.
        owner = config_by_path(
            'manifest.files.%s.owner' % file_key, self.config, missing_ok=True
        )
        if self.options.get('prep-host', False) and owner is None:
            raise ConfigError(
                "manifest file 'manifest.files.%s' must have an explicit "
                "owner"
            )
        if owner is not None:
            if not isinstance(owner, str):
                raise ConfigError(
                    "'manifest.files.%s.owner' has a value of type '%s' and "
                    "should have a value of type '%s' or be null" % (
                        file_key, str(type(owner)), str(str)
                    )
                )
            try:
                getpwnam(owner)
            except KeyError as err:
                raise ContextualError(
                    "'manifest.files.%s.owner' specifies a username '%s' "
                    "that is not yet provisioned on the host" % (
                        file_key, owner
                    )
                ) from err
        group = config_by_path(
            'manifest.files.%s.group' % file_key, self.config, missing_ok=True
        )
        if self.options.get('prep-host', False) and group is None:
            raise ConfigError(
                "manifest file 'manifest.files.%s' must have an explicit "
                "group"
            )
        if group is not None:
            if not isinstance(group, str):
                raise ConfigError(
                    "'manifest.files.%s.group' has a value of type '%s' and "
                    "should have a value of type '%s' or be null" % (
                        file_key, str(type(group)), str(str)
                    )
                )
            try:
                getgrnam(group)
            except KeyError as err:
                raise ContextualError(
                    "'manifest.files.%s.group' specifies a group '%s' that is "
                    "not yet provisioned on the host" % (
                        file_key, group
                    )
                ) from err

    def __valid_manifest_files(self):
        """Validate the 'files' section of the manifest

        """
        manifest_files = self.__check_and_get_config_path(
            'manifest.files', dict
        )
        if not manifest_files:
            raise ConfigError(
                "'manifest.files' must contain at least one item"
            )
        # If we are running in prep-host mode, we are only going to
        # deploy a subset of the manifest, only check the files we
        # plan to deploy.
        prep_host = self.options.get('prep-host', False)
        annotations = (
            ['host-prep-entrypoint', 'host-prep-support'] if prep_host
            else []
        )
        for file_key, manifest_file in manifest_files.items():
            file_annotations = manifest_file.get('annotations', [])
            matches = [
                file_annotation
                for file_annotation in file_annotations
                if file_annotation in annotations
            ]
            if annotations and not matches:
                # No matching annotations, skip this file
                continue
            self.__valid_manifest_file(file_key)

    def __valid_required_annotation(self, annotation, max_count=None):
        """Make sure that the specified 'annotation' is present on at
        least one file in the manifest and, if 'max_count' is
        specified, no more than 'max_count' files.

        """
        manifest_files = self.__check_and_get_config_path(
            'manifest.files', dict
        )
        found = [
            manifest_file_key
            for manifest_file_key, manifest_file in manifest_files.items()
            if annotation in manifest_file.get('annotations', [])
        ]
        if not found:
            raise ConfigError(
                "there is no file with the required annotation "
                "'%s' in 'manifest.files'" % annotation
            )
        if max_count is not None and len(found) > max_count:
            raise ConfigError(
                "there should be a maximum of %d file%s with the "
                "annotation '%s' in 'manifest.files', these files "
                "all have that annotation: %s" % (
                    max_count,
                    's' if max_count > 1 else '',
                    annotation,
                    str(found)
                )
            )

    def __valid_manifest(self):
        """Validate the contents of the manifest portion of the
        configuration

        """
        self.__check_and_get_config_path('manifest', dict)
        self.__valid_manifest_deploy_dir()
        self.__valid_manifest_deploy_user()
        self.__valid_manifest_files()
        self.__valid_required_annotation('image-builder')
        self.__valid_required_annotation('install-entrypoint', 1)
        self.__valid_required_annotation('host-prep-entrypoint', 1)

    def __valid_bmcs(self):
        """Validate the configuration of BMCs

        """
        self.__check_and_get_config_path('bmcs', dict)

    def __valid_hosting(self):
        """Validate the hosting configuration

        """
        self.__check_and_get_config_path('hosting_config', dict)

    def __valid_node(self, node):
        """Verify that the contents of a node is complete and
        consistent.
        """
        name = "<unnamed-node>"
        try:
            name = self.__check_and_get_dict_key('name', node, str)
            self.__check_and_get_dict_key('xname', node, str)
            self.__check_and_get_dict_key('bmc_xname', node, str)
            cluster_net_interface = self.__check_and_get_dict_key(
                'cluster_net_interface', node, str
            )
            self.__check_and_get_dict_key('hostname', node, str)
            self.__check_and_get_dict_key('nid', node, int)
            self.__check_and_get_dict_key('node_group', node, str)
            interfaces = self.__check_and_get_dict_key(
                'interfaces', node, list
            )
        except ConfigError as err:
            raise ConfigError(
                "node '%s' is not properly formed - %s" % (name, str(err))
            ) from err
        cluster_interface = None
        for interface in interfaces:
            network_name = "<unnamed-network>"
            try:
                network_name = self.__check_and_get_dict_key(
                    'network_name', interface, str
                )
                self.__check_and_get_dict_key(
                    'mac_addr', interface, str
                )
                if network_name == cluster_net_interface:
                    cluster_interface = interface
            except ConfigError as err:
                raise ConfigError(
                    "network '%s' in node '%s' is not properly formed - %s" % (
                        name, network_name, str(err)
                    )
                ) from err
        if cluster_interface is None:
            raise ConfigError(
                "node '%s' has no interface connected to the cluster "
                "network ('%s')" % (name, cluster_net_interface)
            )

    def __valid_nodes(self):

        """Validate the 'nodes' section of the config

        """
        nodes = self.__check_and_get_config_path('nodes', list)
        if not nodes:
            raise ConfigError(
                "the 'nodes' section is empty"
            )
        for node_key in nodes:
            self.__valid_node(node_key)

    def __valid_images(self):
        """Validate the 'images' section of the config

        """
        self.__check_and_get_config_path('images', dict)
        self.__check_and_get_config_path('images.build_order', list)
        builders = self.__check_and_get_config_path('images.builders', dict)
        if not builders:
            raise ConfigError(
                "config must provide at least one image builder in "
                "'images.builders' section"
            )
        deployment_targets = self.__check_and_get_config_path(
            'images.deployment_targets', dict
        )
        if not deployment_targets:
            raise ConfigError(
                "config must provide at least one deployment target in "
                "'images.deployment_targets' section"
            )
        # Check that all deployment targets are deploying an image
        # built by a known image builder and are targeting a known
        # node group.
        #
        # First, make a set of node groups to use in validating
        # deployment target keys.
        nodes = self.config.get('nodes', {})
        node_groups = {
            node['node_group']
            for node in nodes
            if 'node_group' in node
        }
        for node_group, image_key in deployment_targets.items():
            if image_key not in builders:
                raise ConfigError(
                    "unknown image builder key '%s' used for node group "
                    "'%s' in 'images.deployment_targets'" % (
                        image_key, node_group
                    )
                )
            if node_group not in node_groups:
                raise ConfigError(
                    "unknown config target node group '%s' "
                    "found in 'images.deployment_targets' section "
                    "known node groups are: %s" % (
                        node_group,
                        " % ".join(sorted(list(node_groups)))
                    )
                )

    def validate(self):
        """Validate the final configuration to be sure that everything
        is reasonable before attempting an installation.

        """
        self.load_config()
        deployment_mode = self.__check_and_get_config_path(
            'deployment_mode', str
        )
        if deployment_mode not in ('host', 'cluster'):
            raise ConfigError(
                "unknown deployment_mode: '%s' "
                "expected 'host' or 'cluster'" % deployment_mode
            )
        self.__valid_manifest()
        self.__valid_bmcs()
        self.__valid_hosting()
        self.__valid_nodes()
        self.__valid_images()

    def install(self):
        """Render all the templates and place the resulting files
        according to the manifest, then run the requested installation
        action (either prepare the host or install OpenCHAMI). If the
        'files-only' option is specified, install the files but do not
        run the installation.

        """
        self.load_config()
        self.validate()
        self.prepare()
        prep_host = self.options.get('prep-host', False)
        annotations = (
            ['host-prep-entrypoint', 'host-prep-support'] if prep_host
            else None
        )
        render_manifest(self.config, annotations)
        if not self.options.get('files-only', False):
            if not prep_host:
                run_install_script(self.config)
            else:
                run_host_prep_script(self.config)

    def show_config(self):
        """Display the configuration resulting from applying the base
        configuration and all of the overlay files on standard output.

        """
        self.load_config()
        sys.stdout.write(dump_yaml(self.config))

    def show_base_config(self):
        """Display the base configuration file (with comments) on
        standard output.

        """
        with open(BASE_CONFIG_PATH, 'r', encoding='UTF-8') as base_config:
            sys.stdout.write(base_config.read() + '\n')
