# Copyright (c) 2023 VEXXHOST, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import concurrent.futures
import os
import shutil
import subprocess
import tempfile

import click
import platformdirs
import requests
from diskcache import FanoutCache

from make_controlplane_registry import image_utils

CACHE = FanoutCache(
    directory=platformdirs.user_cache_dir("make_controlplane_registry", "vexxhost"),
)

# NOTE(mnaser): This is a list of all the Kubernetes versions which we've
#               released images for.  This list is used to determine which
#               images of Kubernetes we should publish to the registry.
VERSIONS = [ "v1.22.17" ]

#VERSIONS = [
#    "v1.23.13",
#    "v1.23.17",
#    "v1.24.7",
#    "v1.24.15",
#    "v1.25.3",
#    "v1.25.11",
#    "v1.26.2",
#    "v1.26.6",
#    "v1.27.3",
#]


@click.command()
@click.option(
    "--repository",
    required=True,
    help="Target image repository",
)
@click.option(
    "--parallel",
    default=8,
    help="Number of parallel uploads",
)
@click.option(
    "--insecure",
    is_flag=True,
    help="Allow insecure connections to the registry.",
)
def main(repository, parallel, insecure):
    """
    Load images into a remote registry for `container_infra_prefix` usage.
    """
    crane_path = shutil.which("crane")

    if crane_path is None:
        raise click.UsageError(
            """Crane is not installed. Please install it before running this command:
             https://github.com/google/go-containerregistry/blob/main/cmd/crane/README.md#installation"""
        )

    # NOTE(mnaser): This list must be maintained manually because the image
    #               registry must be able to support a few different versions
    #               of Kubernetes since it is possible to have multiple
    #               clusters running different versions of Kubernetes at the
    #               same time.
    images = set(
        _get_all_kubeadm_images()
        + _get_cilium_images()
        + _get_cert_manager_images()
        + _get_capi_images()
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
        future_to_image = {
            executor.submit(
                _mirror_image, image, repository, insecure, crane_path
            ): image
            for image in images
        }

        for future in concurrent.futures.as_completed(future_to_image):
            image = future_to_image[future]
            try:
                future.result()
            except Exception as e:
                click.echo(
                    f"Image upload failed for {image}: {e}",
                    err=True,
                )


def _mirror_image(image: str, repository: str, insecure: bool, crane_path: str):
    src = image
    dst = image_utils.get_image(image, repository)

    try:
        command = [crane_path]
        if insecure:
            command.append("--insecure")
        command += ["copy", "--platform", "linux/amd64", src, dst]
        click.echo(f"Command: {command}")

        subprocess.run(command, check=True)
    except subprocess.CalledProcessError:
        click.echo(
            "Image upload failed. Please ensure you're logged in via Crane.",
            err=True,
        )
        return


def _get_all_kubeadm_images():
    """
    Get the list of images that are used by Kubernetes by downloading "kubeadm"
    and running the "kubeadm config images list" command.
    """

    images = []
    for version in VERSIONS:
        images += _get_kubeadm_images(version)

    return images


@CACHE.memoize()
def _get_kubeadm_images(version: str):
    """
    Get the list of images that are used by Kubernetes by downloading "kubeadm"
    and running the "kubeadm config images list" command.
    """

    # Download kubeadm
    r = requests.get(f"https://dl.k8s.io/release/{version}/bin/linux/amd64/kubeadm")
    r.raise_for_status()

    # Write it to a temporary file
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(r.content)
        f.close()

        # Make it executable
        os.chmod(f.name, 0o755)

        # Run the command
        output = subprocess.check_output(
            [f.name, "config", "images", "list", "--kubernetes-version", version]
        )

        # Remove the temporary file
        os.unlink(f.name)

    # Parse the output
    return output.decode().replace("k8s.gcr.io", "registry.k8s.io").splitlines()


def _get_cilium_images():
    return [
        # Cilium 1.13.3
        "quay.io/cilium/cilium:v1.13.3",
        "quay.io/cilium/operator-generic:v1.13.3",
    ]

def _get_cert_manager_images():
    return [
      # Cert manager 1.12.2
      "quay.io/jetstack/cert-manager-ctl:v1.12.2",
      "quay.io/jetstack/cert-manager-cainjector:v1.12.2",
      "quay.io/jetstack/cert-manager-controller:v1.12.2",
      "quay.io/jetstack/cert-manager-webhook:v1.12.2",
    ]

def _get_capi_images():
    # cluster api 1.4.4
    return [
      "registry.k8s.io/cluster-api/cluster-api-controller:v1.4.4",
      "registry.k8s.io/cluster-api/kubeadm-bootstrap-controller:v1.4.4",
      "registry.k8s.io/cluster-api/kubeadm-control-plane-controller:v1.4.4",
      "registry.k8s.io/capi-openstack/capi-openstack-controller:v0.8.0"
    ]
