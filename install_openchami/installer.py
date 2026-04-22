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
from subprocess import (
    run,
    CalledProcessError,
)

from .config import Config
from .error import ContextualError


class Installer:
    """The OpenCHAMI installer class that orchestrates installation of
    OpenCHAMI on a system.

    """
    def __init__(self, options, config_overlays):
        """Construct the installer instance using the config overlays
        and options provided from the caller.

        """
        self.config = Config(options, config_overlays)
        self.options = options
        # Nothing to do, just return

    def prepare(self):
        """Prepare the Installer to install the system by reading in
        the configuration, merging the overlays onto the
        configuration, and generating any configuration data that need
        to be generated.

        """
        self.config.prepare()

    def validate(self):
        """Validate the final configuration to be sure that everything
        is reasonable before attempting an installation.

        """
        self.config.validate()


    def __run_install_script(self):
        """The manifest in the configuration will have one file that
            has the annotation 'install-entrypoint'. Find that script
            and execute it as the user specified in
            'manifest.deployment_user.username'.

        """
        install_script = self.config.find_annotated_files(
            'install-entrypoint'
        )[0]
        deploy_user = self.config.config_by_path(
            'manifest.deployment_user.username'
        )
        try:
            run(['su', '-', deploy_user, install_script], check=True)
        except CalledProcessError as err:
            raise ContextualError(
                "install script '%s' exited failed to run - %s" % (
                    install_script, str(err)
                )
            ) from err

    def __run_host_prep_script(self):
        """The manifest in the configuration will have one file that has
        the annotation 'host-prep-entrypoint'. Find that script and execute
        it as the user specified in 'manifest.deployment_user.username'.

        """
        host_prep_script = self.config.find_annotated_files(
            'host-prep-entrypoint'
        )[0]
        try:
            run([host_prep_script], check=True)
        except CalledProcessError as err:
            raise ContextualError(
                "host-prep script '%s' exited failed to run - %s" % (
                    host_prep_script, str(err)
                )
            ) from err

    def install(self):
        """Render all the templates and place the resulting files
        according to the manifest, then run the requested installation
        action (either prepare the host or install OpenCHAMI). If the
        'files-only' option is specified, install the files but do not
        run the installation.

        """
        self.validate()
        self.prepare()
        prep_host = self.options.get('prep-host', False)
        annotations = (
            ['host-prep-entrypoint', 'host-prep-support'] if prep_host
            else None
        )
        self.config.render_manifest(annotations)
        if not self.options.get('files-only', False):
            if not prep_host:
                self.__run_install_script()
            else:
                self.__run_host_prep_script()

    def show_config(self):
        """Display the configuration resulting from applying the base
        configuration and all of the overlay files on standard output.

        """
        self.config.show_config()

    def show_base_config(self):
        """Display the base configuration file (with comments) on
        standard output.

        """
        self.config.show_base_config()
