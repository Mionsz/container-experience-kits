"""Class for Docker images management"""
import base64
import configparser
import json
import os
import subprocess  # nosec B404 # subprocess is set to shell=False

from pathlib import Path

import boto3
import click
import docker
import validators


def subprocess_run(*args, **kwargs):
    return subprocess.run(*args, **kwargs)  # pylint: disable=W1510


class DockerManagement:
    """
    Class contains methods for copy docker images between registries.
    """

    def __init__(self, from_registry, to_registry, images_to_replicate, region, cloud=None, show_log=False):
        """
        Init method for class.

        Parameters:
        from_registry (string): URL address of source registry
        to_registry (string): URL address of target registry
        images_to_duplicate (list): List of images to copy between registries
        cloud (string): [Not required] Type of cloud with target registry. Currently supported: ['aws']
        show_log (bool): [Not required] Show log of push image

        Return:
        None

        """
        self.docker_client = docker.from_env()
        self.cloud = cloud
        self.aws_region = region
        self.aws_access_key_id = None
        self.aws_access_secret_key = None
        self.cr_password = None
        self.cr_username = None
        self.cr_url = None
        self.show_log = show_log
        self.to_registry = to_registry
        self.images_to_replicate = images_to_replicate
        self.tagged_images = []

        if not validators.url(from_registry):
            click.secho('The source registry does not have a valid URL!', fg='red')
            return

        self.from_registry = from_registry.replace('https://', '')
        self.images_to_replicate = images_to_replicate
        click.echo(f"Images to replicate: {self.images_to_replicate}")

        if self.cloud == "aws":
            self.initialize_ecr()
        elif self.cloud == "azure":
            self.initialize_acr()

    def copy_images(self):
        """
        Copy images between registries.
        In case of set cloud, method using global variables with cloud credentials.

        Parameters:
        None

        Return:
        None

        """
        for image in self.images_to_replicate:
            self.pull_image(registry_url=self.from_registry,
                            image_name=image)
            new_image = self.tag_image(image_name=image,
                                       registry_old=self.from_registry,
                                       registry_new=self.to_registry)
            self.tagged_images.append(new_image)
            self.push_image(image=new_image['repository'],
                            tag=new_image['tag'],
                            registry=self.cr_url,
                            username=self.cr_username,
                            password=self.cr_password)

    def initialize_ecr(self):
        """
        Initializing ECR and getting AWS credentials for authentication in ECR.
        Method using local AWS credentials and config files for authentication.
        Method set the global variables used in previous method.

        Parameters:
        None

        Return:
        None

        """
        aws_credentials = os.path.join(Path.home(), '.aws', 'credentials')
        config = configparser.RawConfigParser()
        try:
            config.read(aws_credentials)
            credentials = config['default']
            self.aws_access_key_id = credentials['aws_access_key_id']
            self.aws_access_secret_key = credentials['aws_secret_access_key']
        except configparser.ParsingError as parser_error:
            click.secho(parser_error, fg='red')

        aws_session = boto3.Session(region_name=self.aws_region)
        ecr_client = aws_session.client('ecr', aws_access_key_id=self.aws_access_key_id,
                                        aws_secret_access_key=self.aws_access_secret_key,
                                        region_name=self.aws_region)

        ecr_credentials = (ecr_client.get_authorization_token()['authorizationData'][0])
        self.cr_username = "AWS"
        self.cr_password = (base64.b64decode(ecr_credentials['authorizationToken'])
                            .replace(b'AWS:', b'').decode('utf-8'))
        self.cr_url = self.to_registry

    def initialize_acr(self):
        acr_name = self.to_registry.split(".")[0]
        command = f'az acr login --name {acr_name} --expose-token'
        result = subprocess_run(command.split(' '), stdout=subprocess.PIPE)
        access_token = json.loads(result.stdout)["accessToken"]
        self.cr_password = access_token
        self.cr_username = "00000000-0000-0000-0000-000000000000"
        self.cr_url = self.to_registry

    def pull_image(self, registry_url, image_name, username=None, password=None):
        """
        Downloading image from remote to local registry.

        Parameters:
        registry_url (string): URL address of source registry
        image_name (string): Name of downloaded image
        username (string): User name for source registry
        password (string): Password for source registry

        Return:
        None

        """
        if not (username is None and password is None):
            self.docker_client.login(username=username,
                                     password=password,
                                     registry=registry_url)
            output = self.docker_client.images.pull(f"{registry_url}/{image_name}")
            click.echo(output)
        else:
            output = self.docker_client.images.pull(f"{registry_url}/{image_name}")
            click.echo(output)

    def tag_image(self, image_name, registry_old, registry_new):
        """
        Tagging image with new registry.

        Parameters:
        image_name (string): Name of image
        registry_old (string): URL address of source registry
        registry_new (string): URL address of target registry

        Return:
        string:Name of tagged image

        """
        image = self.docker_client.images.get(f"{registry_old}/{image_name}")
        if self.cloud == 'aws':
            target_image = registry_new
            tag = image_name.replace('/', '-').replace(':', '-')
        else:
            target_image = f"{registry_new}/{image_name}"
            tag = 'latest'
        result = image.tag(target_image, tag)
        if result:
            return {'repository': target_image, 'tag': tag}

    def push_image(self, image, tag, registry=None, username=None, password=None):
        """
        Pushing image to target registry.

        Parameters:
        image (string): Name of the image
        registry (string): URL address of target registry
        username (string): User name for target registry
        password (string): Password for target registry

        Return:
        None

        """
        click.echo("Pushing image:")
        auth_config = None
        if registry is not None and username is not None and password is not None:
            self.docker_client.login(username=username,
                                     password=password,
                                     registry=registry)
            auth_config = {'username': username, 'password': password}

        if auth_config is not None:
            push_log = self.docker_client.images.push(image, tag=tag, auth_config=auth_config)
            if not self.show_log:
                click.echo(push_log)
