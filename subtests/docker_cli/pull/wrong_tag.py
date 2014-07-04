"""
Test output of docker Pull command

docker pull full_name_wrong_tag

1. Try to download repository from wrong registry
2. Command should fail.
"""
from pull import pull_base, check_registry


class wrong_tag(pull_base):
    config_section = 'docker_cli/pull/wrong_tag'

    def setup(self):
        # check docker registry:
        registry_addr = self.config["docker_registry_host"]
        check_registry(registry_addr)
