"""
Simple interaction
"""
# Okay to be less-strict for these cautions/warnings in subtests
# pylint: disable=C0103,C0111,R0904,C0103
from dockertest import subtest
from dockertest.containers import DockerContainers
from autotest.client.shared import utils


class interaction(subtest.SubSubtestCaller):

    """ Subtest caller """
    config_section = 'docker_cli/interaction'


class interaction_base(subtest.SubSubtest):

    def initialize(self):
        """
        Creates dexpect session with host's bash, than starts container in it
        and stores host and container hostnames.
        """
        super(interaction_base, self).initialize()
        # Prepare a container
        containers = DockerContainers(self.parent_subtest)
        self.sub_stuff['containers'] = containers
        containers.create_docker_cfg('cont1', config=self.config)
        container = containers.get_container_by_dname('cont1')
        self.sub_stuff['container'] = container

        # No we have container up and running
        if self.config.get('session_attached'):
            self.failif(not utils.wait_for(container.is_alive, 5),
                        "Container did not started in 5s")
            self.sub_stuff['session'] = container.get_session("cont1_attached")
        else:
            self.failif(not container.get_main_session(), "You are trying to"
                        " use main process on detached container. Either set "
                        "container as interactive or session_attached to yes")
            self.sub_stuff['session'] = container.get_main_session()

    def run_once(self):
        super(interaction_base, self).run_once()
        session = self.sub_stuff['session']
        self.failif(not session.is_responsive(), "Session not responsive.")
        self.logdebug(session.cmd("ls"))
        session.send_control('s')
        self.failif(session.is_responsive(2), "Session is responsive even "
                    "though we sent ctrl+s")
        session.send_control('q')
        self.failif(not session.is_responsive(), "Session not responsive "
                    "after sending ctrl+q.")

        session.sendline('exit')
        not_alive = lambda: not session.is_alive()
        self.failif(not utils.wait_for(not_alive, 5), "Session is alive "
                    "even though exit was executed.")

    def cleanup(self):
        """
        Close all sessions and remove the container
        """
        super(interaction_base, self).cleanup()
        # Stop session
        if self.config['remove_after_test'] is True:
            # TODO: Put this into cleanup_managed_containers() when/if possible
            self.sub_stuff['containers'].remove_args = "-f -v"
            self.sub_stuff['containers'].cleanup_managed_containers()


class interactive_tty(interaction_base):
    """
    Using the --interactive mode
    """
    pass


class interactive_nontty(interaction_base):
    """
    Using the --interactive mode
    """
    pass


class attached_tty(interaction_base):
    """
    Using the --detached mode
    """
    pass


class attached_nontty(interaction_base):
    """
    Using the --detached mode
    """
    pass
