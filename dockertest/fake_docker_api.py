"""
Simplified implementation of python-docker-py for useage in autotest-docker
:warning: This is not full implementation, use with extreme caution!
"""

import httplib
import socket
import json


class UHTTPConnection(httplib.HTTPConnection):
    """
    Subclass of Python library HTTPConnection that uses a unix-domain socket.
    """

    def __init__(self, path="/var/run/docker.sock"):
        httplib.HTTPConnection.__init__(self, 'localhost')
        self.path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        self.sock = sock


class Client(object):
    """
    Fake implementation of docker.client.Client used for basic autotest testing
    """
    def __init__(self, url="/var/run/docker.sock"):
        self._connection = UHTTPConnection(url)

    def _get(self, url):
        """
        Get response from docker daemon
        :param url: Desired url (eg. '/version')
        :type url: str
        :return: Docker response
        :rtype: httplib.HTTPResponse
        """
        self._connection.request("GET", url)
        return self._connection.getresponse()

    @staticmethod
    def _result(response):
        """
        Process HTTP response
        :param response: response
        :type response: httplib.HTTPResponse
        :raise IOError: When response status is not 200 (OK)
        :return: json processed output of the response
        :rtype: dict
        """
        if response.status != 200:
            raise IOError("Bad response status %s (%s)\nRaw data: %s"
                          % (response.status, response.reason, response.read())
                          )
        return json.loads(response.read())

    def version(self):
        """
        Return docker version
        """
        return self._result(self._get("/version"))
