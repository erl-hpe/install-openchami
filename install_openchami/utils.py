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
"""Utility functions to support the OpenCHAMI installer

"""
import sys
from json import dumps as json_dumps
from tempfile import NamedTemporaryFile
from os.path import sep as path_separator
from os.path import join as path_join
from os import (
    chown,
    chmod,
    makedirs,
)
from pathlib import Path
from subprocess import (
    run,
    CalledProcessError
)

from yaml import (
    SafeDumper,
    safe_load,
    YAMLError
)
from yaml import dump as yaml_dumps
from vtds_base import (
    render_template_file,
    merge_configs
)

from . import template

from .error import ContextualError, ConfigError


# Create a custom representer for yaml SafeDumper to dump multiline strings
# using the '|' notation and multiline string output properly indented
def __representer_strings_multiline(dumper, data):
    """String representer for yaml that dumps multiline strings using
    pipe notation.

    """
    if '\n' in data:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style="|"
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


# Create a yaml SafeDumper class that uses '|' notation for multiline
# strings
class MultilineStringSafeDumper(SafeDumper):
    """Safe Dumper Class for multiline strings so that when I register
    my representer I don't corrupt the standard SafeDumper

    """


# Register the multiline string representer with the custom safe dumper
MultilineStringSafeDumper.add_representer(
    str, __representer_strings_multiline
)


def error(msg):
    """Produce an error message on stderr

    """
    sys.stderr.write("ERROR: %s\n" % msg)


def warning(msg):
    """Produce a warning message on stderr.

    """
    sys.stderr.write("WARNING: %s\n" % msg)


def info(msg):
    """Produce an informational message on stderr.

    """
    sys.stderr.write("INFO: %s\n" % msg)


def compose_config(config_paths):
    """Read in the YAML configuration files found in the list
    'config_files' in the order they are presented. Use the first one
    to establish the base configuration, then overlay each successive
    one onto that base. Return the final result of the overlays.

    """
    overlays = []
    for config_path in config_paths:
        try:
            with open(config_path, 'r', encoding='UTF-8') as config_file:
                overlays.append(safe_load(config_file))
        except OSError as err:
            raise ContextualError(
                "cannot open config_overlay file '%s' - %s" % (
                    config_path, str(err)
                )
            ) from err
        except YAMLError as err:
            raise ContextualError(
                "error parsing config overlay file '%s' - %s" % (
                    config_path, str(err)
                )
            ) from err
    config = overlays[0]
    for overlay in overlays[1:]:
        config = merge_configs(config, overlay)
    return config


def dump_yaml(config):
    """Dump the configuration in 'config' to a string

    """
    return yaml_dumps(
        config,
        Dumper=MultilineStringSafeDumper,
        default_flow_style=False, sort_keys=False, indent=2
    )


def dump_json(config):
    """Dump the configuration in 'config' to a string

    """
    return json_dumps(config, indent=2)


def config_by_path(path, config, missing_ok=False):
    """Within a given config, find the data referenced by a dotted
    notation path into the configuration and return it. If any element
    of the path prior to the last element is not found, raise a
    ConfigError exception showing where the resolution failed. If the
    last element of the path can't be found, and 'missing_ok' is
    False, also raise the same exception. If the last element can't be
    found and 'missing_okay' is True, however, simply return None.

    """
    elements = path.split('.')
    tmp_path = ""
    found = config
    for element in elements:
        tmp_path += element
        if not isinstance(found, dict) or element not in found:
            if missing_ok and len(elements) == len(tmp_path.split('.')):
                # We have reached the end of the path and the last
                # item is missing. The caller said missing was okay,
                # so just return None
                return None
            # Could not find this element and either it is not
            # okay for the requested item to be missing, or we
            # have not reached the end of the path so it is a
            # parent item that is missing. Raise a ConfigError.
            raise ConfigError(
                "unable to resolve path '%s' in configuration" % tmp_path
            )
        found = found[element]
        tmp_path += '.'
    return found


def __render_manifest_file(manifest_file, config):
    """Render the file described in 'manifest_entry' to the output
    file specified in 'output' using 'config' as the template data.

    """
    template_name = manifest_file.get('template_name', None)
    target = manifest_file.get('target')
    if target[0] != path_separator:
        # This is a relative pathname, prepend the configured deployment
        # directory to it to make it absolute.
        deploy_dir = config_by_path(
            'manifest.deployment_directory', config
        )
        target = path_join(deploy_dir, target)
    # Create an empty 'target' file and set its ownership and access
    # so that it is protected from the start.
    deploy_uid = config_by_path('manifest.deployment_user.uid', config)
    deploy_gid = config_by_path('manifest.deployment_user.gid', config)
    file_uid = manifest_file.get('uid', None)
    file_gid = manifest_file.get('gid', None)
    uid = file_uid if file_uid is not None else deploy_uid
    gid = file_gid if file_gid is not None else deploy_gid
    mode = int(config_by_path('mode', manifest_file), base=8)
    make_dir = manifest_file.get('mkdir', False)
    if make_dir:
        target_dir = str(Path(target).parent)
        try:
            makedirs(target_dir, mode=0o755, exist_ok=True)
            chown(target_dir, uid, gid)
        except OSError as err:
            raise ContextualError(
                "unable to make directory path '%s' - %s" % (target_dir, err)
            ) from err
    try:
        with open(target, "w", encoding='UTF-8'):
            # don't really need to do this with the file open, but we did
            # need to create the file, so this makes a good thing to do in
            # the 'with ...' block, why not?
            chown(target, uid, gid)
            chmod(target, mode)
    except OSError as err:
        raise ContextualError(
            "unable to create manifest target file  '%s' - %s" % (target, err)
        ) from err
    # Now we are ready to render the file safely
    if template_name is None:
        # What were are doing here is finding the configuration from
        # which to write out the template file by the configuration
        # path specified in the manifest item's generation parameters,
        # hence the weird indirection.
        file_data = config_by_path(
            config_by_path('generation.config_path', manifest_file), config
        )
        with NamedTemporaryFile(mode='w+', encoding='UTF-8') as tmp_file:
            template_file = tmp_file.name
            if manifest_file['generation']['type'] == 'yaml':
                tmp_file.write(dump_yaml(file_data))
            else:
                tmp_file.write(dump_json(file_data))
            tmp_file.flush()
            tmp_file.seek(0)
            render_template_file(template_file, config, target)
    else:
        render_template_file(template(template_name), config, target)


def render_manifest(config, annotations=None):
    """Use Jinj2 to render all of the files in a supplied manifest to
    their specified destinations providing 'config' as the templating
    data. If 'annotations' are provided only the files that have one
    or more of the provided annotations are rendered. If 'annotations'
    are not provided or None, all files are rendered.

    """
    manifest_files = config_by_path('manifest.files', config)
    for manifest_file in manifest_files.values():
        if annotations:
            # Annotations were specified, see if this file matches any
            file_annotations = manifest_file.get('annotations', [])
            matches = [
                file_annotation
                for file_annotation in file_annotations
                if file_annotation in annotations
            ]
            if not matches:
                # No matching annotations, skip this file
                continue
        __render_manifest_file(manifest_file, config)


def __find_annotated_files(config, annotation):
    """Find the list of manifest files with a specific annotation and
    return the list fully resolved target paths.

    """
    manifest_files = config_by_path('manifest.files', config)
    # Get all the target paths
    found = [
        manifest_file['target']
        for manifest_file in manifest_files.values()
        if annotation in manifest_file.get('annotations', [])
    ]
    # Fix the ones that are not absolute
    deploy_dir = config_by_path('manifest.deployment_directory', config)
    found = [
        path_join(deploy_dir, path) if path[0] != path_separator else path
        for path in found
    ]
    return found


def run_install_script(config):
    """The manifest in the configuration will have one file that has
    the annotation 'install-entrypoint'. Find that script and execute
    it as the user specified in 'manifest.deployment_user.username'.

    """
    install_script = __find_annotated_files(config, 'install-entrypoint')[0]
    deploy_user = config_by_path('manifest.deployment_user.username', config)
    try:
        run(['su', '-', deploy_user, install_script], check=True)
    except CalledProcessError as err:
        raise ContextualError(
            "install script '%s' exited failed to run - %s" % (
                install_script, str(err)
            )
        ) from err


def run_host_prep_script(config):
    """The manifest in the configuration will have one file that has
    the annotation 'host-prep-entrypoint'. Find that script and execute
    it as the user specified in 'manifest.deployment_user.username'.

    """
    host_prep_script = __find_annotated_files(
        config, 'host-prep-entrypoint'
    )[0]
    try:
        run([host_prep_script], check=True)
    except CalledProcessError as err:
        raise ContextualError(
            "host-prep script '%s' exited failed to run - %s" % (
                host_prep_script, str(err)
            )
        ) from err
