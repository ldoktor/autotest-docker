"""
This module helps managing in-test created containers/images and other objects

:copyright: Red Hat, Inc.
"""
import time

from dockertest import xceptions
from dockertest.dockercmd import NoFailDockerCmd
from dockertest.images import DockerImage
from interaction import InteractiveAsyncDockerCmd


class DockerManager(object):
    """
    This class implements "docker manager" - an object which helps to create,
    maintain and destroy in-test used containers, images and other stuff.
    :warning: This function is not thread-safe, use your own locking!
    """
    def __init__(self, subtest):
        #: Subtest this DockerManager is related to
        self.subtest = subtest
        #: List of created containers
        self.containers = []
        #: List of created images
        self.images = []

    def create_container(self, cmd, subargs=None, name=None, image=None,
                         timeout=None, verbose=True,
                         docker_cmd=InteractiveAsyncDockerCmd):
        """
        Creates container and adds it into containers cache (for cleanup)
        :param cmd: Container's main process (bash -c $cmd or custom list)
        :param subargs: A list of strings containing additional args
        :param name: Name of the container (if existing, random chars appended)
        :param image: Override default image
        :param timeout: Override default cmd timeout
        :param verbose: Verbosity of the Docker Command
        :param docker_cmd: ASYNC docker_cmd to be used
        :return: tuple(this DockerContainer, executed docker_cmd)
        """
        # Prepare docker cmd
        if not name:
            name = "container"
        name = self.subtest.containers.get_unique_name(name)
        if subargs is None:
            subargs = []
        subargs.append("--name %s" % name)
        if not image:
            _config = self.subtest.config
            image = DockerImage.full_name_from_defaults(_config)
        subargs.append(image)
        if isinstance(cmd, list):
            subargs.extend(cmd)
        else:
            subargs.extend(("bash", "-c", cmd))
        # Store name and execute
        self.containers.append(name)
        process = docker_cmd(subtest=self.subtest, subcmd="run",
                             subargs=subargs, timeout=timeout, verbose=verbose)
        process.execute()
        # Wait until it occurs in `docker ps`
        container = None
        end_time = time.time() + 60
        while time.time() < end_time:
            conts = self.subtest.containers.list_containers_with_name(name)
            if len(conts) == 1:
                container = conts[0]
                break
            elif len(conts) > 1:
                msg = ("Multiple containers matching the name %s." % name)
                raise xceptions.DockerTestError(msg)
            time.sleep(0.5)
        if not container:
            msg = ("Can't find the created container with the name %s in "
                   "`docker ps` in 60s." % name)
            raise xceptions.DockerTestError(msg)
        # Replace name with container
        self.containers.append(container)
        self.containers.remove(name)    # this will remove the first container
        return container, process

    def add_container(self, container):
        """
        Add custom container to the list of created containers
        """
        if container in self.containers:
            raise xceptions.DockerKeyError("Container %s already in cache (%s)"
                                           % (container, self.containers))
        self.containers.append(container)

    def rm_container(self, container):
        """
        Remove container from the list of created containers
        """
        self.containers.remove(container)

    def add_image(self, image):
        """
        Add image to the list of created images
        """
        if image in self.images:
            raise xceptions.DockerKeyError("Container %s already in cache (%s)"
                                           % (image, self.images))
        self.images.append(image)

    def rm_image(self, image):
        """
        Remove image from the list of created images
        """
        self.images.remove(image)

    def cleanup_containers(self):
        """
        Safely removes all created containers
        """
        docker_containers = self.subtest.containers
        while self.containers:
            container = self.containers.pop()
            if isinstance(container, str):
                conts = docker_containers.list_containers_with_name(container)
            else:
                container = container.long_id
                conts = docker_containers.list_containers_with_cid(container)
            if len(conts) == 0:
                continue  # Docker was created, but apparently doesn't exist
            elif len(conts) > 1:
                msg = ("Multiple containers matches name %s, not removing any "
                       "of them...", container)
                raise xceptions.DockerTestError(msg)
            NoFailDockerCmd(self.subtest, 'rm', ['--force', '--volumes',
                                                 container]).execute()

    def cleanup_images(self):
        """
        Safely removes all created images
        """
        for image in self.images:
            msg = ("I'd really love to help you removing image %s, but nobody "
                   "told me how.\nYours faitfully, Autotest docker\n"
                   "PS: all of these %s images were not removed. Ha haaa"
                   % (image, self.images))
            raise NotImplementedError(msg)
