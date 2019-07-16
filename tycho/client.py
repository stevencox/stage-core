import requests
import json
import logging
import os
import sys
import traceback
import argparse
import yaml
from string import Template
from tycho.tycho_utils import TemplateUtils
from kubernetes import client as k8s_client, config as k8s_config

logger = logging.getLogger (__name__)

class TychoClient:
    """ Client to Tycho dynamic application deployment API. """

    def __init__(self, url):
        #self.url = f"{url}/system"
        self.url = "{0}/system".format (url)
    def request (self, service, request):
        """ Send a request to the server. Generic underlayer to all requests. """
        #response = requests.post (f"{self.url}/{service}", json=request)
        response = requests.post ("{0}/{1}".format (self.url, service), json=request)
        #result_text = f"HTTP status {response.status_code} received from service: {service}"
        result_text = "HTTP status {0} received from service: {1}".format (response.status_code,
                                                                           service)
        logger.debug (result_text)
        if not response.status_code == 200:
            #raise Exception (f"Error: {result_text}")    
            raise Exception ("Error: {0}".format (result_text))    
        return response.json ()
    def format_name (self, name):
        """ Format a service name to be a valid DNS label. """
        return name.replace (os.sep, '-')
    def start (self, request):
        """ Start a service. """
        return self.request ("start", request)
    def delete (self, request):
        """ Delete a service. """
        return self.request ("delete", request)
    def down (self, name):
        """ Bring down a service. """
        try:
            response = self.delete ({ "name" : self.format_name(name) })
            print (json.dumps (response, indent=2))
        except Exception as e:
            traceback.print_exc (e)
    def up (self, name, system):
        """ Bring a service up starting with a docker-compose spec. """
        request = {
            "name" : self.format_name (name),
            "system" : system
        }
        response = self.start (request)
        error = response.get('result',{}).get('error', None)
        if error:
            print (''.join (error))
        else:
            print (json.dumps (response, indent=2))
            for process, spec in response.get('result',{}).get('containers',{}).items ():
                port = spec['port']
                #print (f"(minikube)=> http://192.168.99.111:{port}")
                print ("(minikube)=> http://192.168.99.111:{0}".format (port))
            
class TychoClientFactory:
    """ Locate Tycho. This is written to work in-cluster or standalone. """
    def __init__(self):
        """ Initialize connection to Kubernetes. """
        """ TODO: Authentication. """
        if os.getenv('KUBERNETES_SERVICE_HOST'):
            k8s_config.load_incluster_config()
        else:
            k8s_config.load_kube_config()
        api_client = k8s_client.ApiClient()
        self.api = k8s_client.CoreV1Api(api_client)
    def get_client (self, name="tycho-api", namespace="default"):
        """ Locate the client endpoint using the K8s API. """
        url = None
        client = None
        try:
            service = self.api.read_namespaced_service(
                name=name,
                namespace=namespace)
            ip_address = service.status.load_balancer.ingress[0].ip
            port = service.spec.ports[0].port
            #url = f"http://{ip_address}:{port}"
            url = "http://{0}:{1}".format (ip_address, port)
        except Exception as e:
            pass
            #traceback.print_exc (e)
            #logger.error (e)
        if url:
            client = TychoClient (url=url) 
        return client

class TychoClientUtils:
    """ Client utilities. """
    @staticmethod
    def apply_environment (environment, text):
        """ Given an environment configuration consisting of lines of Bash style variable assignemnts,
        parse the variables and apply them to the given text."""
        resolved = text
        if environment:
            mapping = {
                line.split("=", maxsplit=1)[0] : line.split("=", maxsplit=1)[1]
                for line in environment.split ("\n") if '=' in line
            }
            #print (f"{json.dumps (mapping, indent=2)}")
            print (json.dumps (mapping, indent=2))
            resolved = Template(text).safe_substitute (**mapping)
            print (resolved)
        return resolved
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Tycho Client')
    parser.add_argument('-u', '--up', help="Launch service.", action='store_true')
    parser.add_argument('-d', '--down', help="Delete a running system. Requires a system id.")
    parser.add_argument('-p', '--port', type=int, help="Port to expose.")
    parser.add_argument('-c', '--container', help="Container to run.")
    parser.add_argument('-n', '--name', help="Service name.")
    parser.add_argument('-s', '--service', help="Tycho API URL.", default="http://localhost:5000")
    parser.add_argument('--env', help="Env variable", default=None)
    parser.add_argument('--command', help="Container command", default=None)
    parser.add_argument('--settings', help="Environment settings", default=None)
    parser.add_argument('-f', '--file', help="A docker compose (subset) formatted system spec.")
    parser.add_argument('-t', '--trace', help="Trace (debug) logging", action='store_true', default=False)
    args = parser.parse_args ()

    """ Honor debug and trace settings. """
    if args.trace:
        logging.basicConfig(level=logging.DEBUG)

    """ Resolve environment settings file as text. """
    settings=""
    if args.settings:
        with open(args.settings, "r") as stream:
            settings = stream.read ()

    #client = TychoClient (url=args.service)

    name=None
    system=None
    if args.file:
        """ Load the docker-compose spec, applying environment settings. """
        name = args.file.split('.')[0]
        with open(args.file, "r") as stream:
            text = stream.read ()
            text = TychoClientUtils.apply_environment (settings, text)
            system = yaml.load (text)
    else:
        """ Generate a docker-compose spec based on the CLI args. """
        name = args.name
        template_utils = TemplateUtils ()
        template = """
          version: "3"
          services:
            {{args.name}}:
              image: {{args.container}}
              {% if args.command %}
              entrypoint: {{args.command}}
              {% endif %}
              {% if args.port %}
              ports:
                - "{{args.port}}"
              {% endif %}"""
        system = template_utils.render_text(
            TychoClientUtils.apply_environment (settings, template),
            context={ "args" : args })

    client = None
    """ Locate the Tycho API endpoint. Instantiate a client to use the endpoint. """
    if args.service == parser.get_default ("service"):
        """ If the endpoint is the default value, try to discover the endpoint in kube. """
        client_factory = TychoClientFactory ()
        client = client_factory.get_client ()
        if not client:
            """ That didn't work so use the default value. """
            client = TychoClient (url=args.service)
    if not client:
        #logger.info (f"creating client directly {args.service}")
        logger.info ("creating client directly {0}".format (args.service))
        client = TychoClient (url=args.service)
        
    if args.up:
        client.up (name=name, system=system)
    elif args.down:
        client.down (name=args.down)
