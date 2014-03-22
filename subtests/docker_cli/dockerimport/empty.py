"""
Sub-subtest module used by dockerimport test

1. Create an empty tar file
2. Pipe empty file into docker import command
3. Check imported image is available
"""

# Okay to be less-strict for these cautions/warnings in subtests
# pylint: disable=C0103,C0111,R0904,C0103

import os, logging
from autotest.client import utils
from dockertest import output
from dockertest.subtest import SubSubtest
from dockertest.dockercmd import DockerCmd, NoFailDockerCmd

try:
    import docker
    DOCKERAPI = True
except ImportError:
    DOCKERAPI = False

class empty(SubSubtest):

    def initialize(self):
        super(empty, self).initialize()
        # FIXME: Need a standard way to do this
        image_name_tag = ("%s%s%s"
                          % (self.parentSubtest.config['image_name_prefix'],
                             utils.generate_random_string(4),
                             self.parentSubtest.config['image_name_postfix']))
        image_name, image_tag = image_name_tag.split(':', 1)
        self.subStuff['image_name_tag'] = image_name_tag
        self.subStuff['image_name'] = image_name
        self.subStuff['image_tag'] = image_tag

    def run_once(self):
        super(empty, self).run_once()
        os.chdir(self.tmpdir)
        tar_command = self.config['tar_command']
        tar_options = self.config['tar_options']
        tar_command = "%s %s" % (tar_command, tar_options)
        subargs = ['-', self.subStuff['image_name_tag']]
        docker_command = DockerCmd(self.parentSubtest, 'import', subargs)
        self.run_tar(tar_command, str(docker_command))

    def postprocess(self):
        super(empty, self).postprocess()
        # name parameter cannot contain tag, don't assume prefix/postfix content
        self.check_output()
        self.check_status()
        # new
        image_name = self.subStuff['image_name']
        image_tag = self.subStuff['image_tag']
        di = DockerImages(self.parentSubtest)
        imgs = di.list_imgs_with_full_name_components(repo=image_name,
                                                      tag=image_tag)
        self.failif(len(imgs) > 1, "Got multiple images named %s:%s (%s)"
                    % (image_name, image_tag, imgs))
        self.subStuff['image_id'] = None
        if imgs:
            self.subStuff['image_id'] = imgs[0].long_id
        self.image_check()

    def image_check(self):
        # Fail subsubtest if...
        result_id = self.subStuff['result_id']
        image_id = self.subStuff['image_id']
        self.logdebug("Resulting ID: %s", result_id)
        self.failif(result_id != image_id,
                    "Repository Id's do not match (%s,%s)"
                    % (result_id, image_id))

    def cleanup(self):
        super(empty, self).cleanup()
        if self.parentSubtest.config['try_remove_after_test']:
            dkrcmd = NoFailDockerCmd(self.parentSubtest, 'rmi',
                                     [self.subStuff['result_id']])
            dkrcmd.execute()

    def run_tar(self, tar_command, dkr_command):
        command = "%s | %s" % (tar_command, dkr_command)
        # Free, instance-specific namespace
        cmdresult = utils.run(command, ignore_status=True, verbose=False)
        self.subStuff['cmdresult'] = cmdresult
        self.loginfo("Command result: %s", cmdresult.stdout.strip())
        self.subStuff['result_id'] = cmdresult.stdout.strip()

    def check_output(self):
        outputgood = output.OutputGood(self.subStuff['cmdresult'],
                                       ignore_error=True)
        self.failif(not outputgood, str(outputgood))

    def check_status(self):
        condition = self.subStuff['cmdresult'].exit_status == 0
        self.failif(not condition, "Non-zero exit status")
