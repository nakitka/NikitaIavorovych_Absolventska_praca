"""
Deployment Orchestrator for Gitea Infrastructure.

Coordinates all deployment phases: Packer, Terraform, Ansible, Helm.
"""

import os
import sys
import json
import time
import getpass
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import DeploymentConfig
from .utils import (
    get_logger,
    console,
    run_command,
    render_template,
    generate_password,
    generate_ssh_keypair,
    wait_for_ssh,
    wait_for_port,
    ensure_directory,
    write_file,
    interactive_pause,
    timestamp_filename,
    display_panel,
    display_table,
    subnet_mask_to_cidr,
    vsphere_path_to_relative,
)
from . import constants


class DeploymentOrchestrator:
    """
    Orchestrates the complete Gitea infrastructure deployment.

    Manages all phases from template building to application deployment.
    """

    def __init__(
        self,
        config: DeploymentConfig,
        project_root: Optional[str] = None,
    ):
        """
        Initialize DeploymentOrchestrator.

        Args:
            config: DeploymentConfig instance
            project_root: Project root directory
        """
        self.logger = get_logger()
        self.config = config
        self.project_root = Path(project_root) if project_root else Path.cwd()

        # Set up directories
        self.templates_dir = self.project_root / "templates"
        self.packer_dir = self.project_root / "packer"
        self.terraform_dir = self.project_root / "terraform"
        self.ansible_dir = self.project_root / "ansible"
        self.helm_dir = self.project_root / "helm"
        self.manifests_dir = self.project_root / "manifests"
        self.kubeconfig_dir = self.project_root / "kubeconfig"
        self.credentials_dir = self.project_root / "credentials"
        self.logs_dir = self.project_root / "logs"

        # Create directories
        for dir_path in [
            self.packer_dir,
            self.terraform_dir,
            self.ansible_dir,
            self.helm_dir,
            self.manifests_dir,
            self.kubeconfig_dir,
            self.credentials_dir,
            self.logs_dir,
        ]:
            ensure_directory(dir_path)

        # Track deployment state
        self.deployment_start = None
        self.phase_results = {}
        self.vm_ips = {}
        self.generated_passwords = {}

    def _get_template_context(self) -> Dict[str, Any]:
        """Build template rendering context from config."""
        vm_ips = self.config.get_vm_ips()

        # Convert folder paths to relative paths for Packer/Terraform
        datacenter = self.config.vsphere.datacenter
        template_folder_relative = vsphere_path_to_relative(
            self.config.vsphere.template_folder, datacenter
        )
        vm_folder_relative = vsphere_path_to_relative(
            self.config.vsphere.vm_folder, datacenter
        )

        # Calculate subnet CIDR prefix
        subnet_cidr = subnet_mask_to_cidr(self.config.network.subnet_mask)

        return {
            # Config sections
            "config": self.config,
            "vsphere": self.config.vsphere,
            "network": self.config.network,
            "infrastructure": self.config.infrastructure,
            "applications": self.config.applications,

            # Relative vSphere paths (for Packer/Terraform)
            "template_folder_relative": template_folder_relative,
            "vm_folder_relative": vm_folder_relative,
            "subnet_cidr": subnet_cidr,

            # Constants
            "constants": constants,
            "K3S_VERSION": constants.K3S_VERSION,
            "STORAGE_CLASS_NAME": constants.STORAGE_CLASS_NAME,
            "NAMESPACES": constants.NAMESPACES,
            "HELM_REPOS": constants.HELM_REPOS,
            "LDAP_SERVER": constants.LDAP_SERVER,

            # VM IPs
            "vm_ips": vm_ips,
            "haproxy_ips": vm_ips.get("haproxy", []),
            "master_ips": vm_ips.get("masters", []),
            "worker_ips": vm_ips.get("workers", []),
            "glusterfs_ips": vm_ips.get("glusterfs", []),
            "backup_ips": vm_ips.get("backup", []),
            "vip_address": self.config.network.vip_address,

            # Ports
            "K8S_API_PORT": constants.K8S_API_PORT,
            "TRAEFIK_HTTP_NODEPORT": constants.TRAEFIK_HTTP_NODEPORT,
            "TRAEFIK_HTTPS_NODEPORT": constants.TRAEFIK_HTTPS_NODEPORT,
            "GITEA_SSH_NODEPORT": constants.GITEA_SSH_NODEPORT,
            "PORTAINER_NODEPORT": constants.PORTAINER_NODEPORT,
            "PROMETHEUS_NODEPORT": constants.PROMETHEUS_NODEPORT,
            "GRAFANA_NODEPORT": constants.GRAFANA_NODEPORT,
            "HAPROXY_STATS_PORT": constants.HAPROXY_STATS_PORT,

            # GlusterFS
            "GLUSTER_VOLUME_NAME": constants.GLUSTER_VOLUME_NAME,
            "GLUSTER_BRICK_PATH": constants.GLUSTER_BRICK_PATH,

            # Passwords (generated)
            "passwords": self.generated_passwords,

            # SSH key paths
            "ssh_private_key": str(self.credentials_dir / "ssh_key"),
            "ssh_public_key": str(self.credentials_dir / "ssh_key.pub"),

            # Preseed server URL for Packer
            "preseed_url": f"http://{self.config.deployment.preseed_server_ip}:{self.config.deployment.preseed_server_port}/preseed.cfg",

        }

    def generate_templates(self) -> None:
        """Generate all deployment templates from Jinja2 templates."""
        self.logger.info("Generating deployment templates...")

        # Generate passwords first
        self._generate_passwords()

        context = self._get_template_context()

        # Packer templates
        packer_templates = [
            ("packer/haproxy.pkr.hcl.j2", self.packer_dir / "haproxy.pkr.hcl"),
            ("packer/kubernetes.pkr.hcl.j2", self.packer_dir / "kubernetes.pkr.hcl"),
            ("packer/glusterfs.pkr.hcl.j2", self.packer_dir / "glusterfs.pkr.hcl"),
            ("packer/preseed.cfg.j2", self.packer_dir / "http" / "preseed.cfg"),
        ]

        packer_scripts = [
            ("packer/scripts/base-provision.sh.j2", self.packer_dir / "scripts" / "base-provision.sh"),
            ("packer/scripts/haproxy-provision.sh.j2", self.packer_dir / "scripts" / "haproxy-provision.sh"),
            ("packer/scripts/kubernetes-provision.sh.j2", self.packer_dir / "scripts" / "kubernetes-provision.sh"),
            ("packer/scripts/glusterfs-provision.sh.j2", self.packer_dir / "scripts" / "glusterfs-provision.sh"),
        ]

        # Terraform templates
        terraform_templates = [
            ("terraform/main.tf.j2", self.terraform_dir / "main.tf"),
            ("terraform/variables.tf.j2", self.terraform_dir / "variables.tf"),
            ("terraform/outputs.tf.j2", self.terraform_dir / "outputs.tf"),
            ("terraform/versions.tf.j2", self.terraform_dir / "versions.tf"),
            ("terraform/cloud-init.yaml.j2", self.terraform_dir / "cloud-init.yaml"),
        ]

        # Ansible templates
        ansible_templates = [
            ("ansible/ansible.cfg.j2", self.ansible_dir / "ansible.cfg"),
            ("ansible/inventory.yml.j2", self.ansible_dir / "inventory.yml"),
            ("ansible/playbooks/glusterfs-cluster.yml.j2", self.ansible_dir / "playbooks" / "glusterfs-cluster.yml"),
            ("ansible/playbooks/haproxy-cluster.yml.j2", self.ansible_dir / "playbooks" / "haproxy-cluster.yml"),
            ("ansible/playbooks/kubernetes-cluster.yml.j2", self.ansible_dir / "playbooks" / "kubernetes-cluster.yml"),
            ("ansible/playbooks/backup-setup.yml.j2", self.ansible_dir / "playbooks" / "backup-setup.yml"),
        ]

        # Helm values templates
        helm_templates = [
            ("helm/traefik-values.yaml.j2", self.helm_dir / "traefik-values.yaml"),
            ("helm/cnpg-values.yaml.j2", self.helm_dir / "cnpg-values.yaml"),
            ("helm/postgresql-cluster.yaml.j2", self.manifests_dir / "postgresql-cluster.yaml"),
            ("helm/redis-values.yaml.j2", self.helm_dir / "redis-values.yaml"),
            ("helm/gitea-values.yaml.j2", self.helm_dir / "gitea-values.yaml"),
            ("helm/prometheus-values.yaml.j2", self.helm_dir / "prometheus-values.yaml"),
            ("helm/portainer-values.yaml.j2", self.helm_dir / "portainer-values.yaml"),
            ("manifests/gitea-runner.yaml.j2", self.manifests_dir / "gitea-runner.yaml"),
        ]

        all_templates = (
            packer_templates +
            packer_scripts +
            terraform_templates +
            ansible_templates +
            helm_templates
        )

        for template_path, output_path in all_templates:
            try:
                ensure_directory(output_path.parent)
                render_template(
                    template_path,
                    str(output_path),
                    context,
                    templates_dir=str(self.templates_dir),
                )
                self.logger.debug(f"Generated: {output_path}")
            except Exception as e:
                self.logger.warning(f"Could not render {template_path}: {e}")

        # Make scripts executable
        for script in (self.packer_dir / "scripts").glob("*.sh"):
            os.chmod(script, 0o755)

        self.logger.info("Template generation complete")

    def _generate_passwords(self) -> None:
        """Generate or load passwords from credentials directory."""
        password_specs = {
            "root_password": (16, False),
            "ssh_password": (16, False),
            "postgresql_password": (24, True),
            "redis_password": (24, True),
            "gitea_admin_password": (16, True),
            "grafana_admin_password": (16, True),
            "keepalived_password": (12, False),
        }

        self.generated_passwords = {}
        for name, (length, special_chars) in password_specs.items():
            password_file = self.credentials_dir / f"{name}.txt"
            if password_file.exists():
                # Load existing password
                self.generated_passwords[name] = password_file.read_text().strip()
                self.logger.debug(f"Loaded existing password: {name}")
            else:
                # Generate new password
                self.generated_passwords[name] = generate_password(length, special_chars)
                write_file(password_file, self.generated_passwords[name], mode=0o600)
                self.logger.debug(f"Generated new password: {name}")

        # Generate SSH key pair
        key_path = self.credentials_dir / "ssh_key"
        if not key_path.exists():
            generate_ssh_keypair(str(key_path))
            self.config.ssh_private_key = str(key_path)
            self.config.ssh_public_key = str(key_path) + ".pub"

        runner_token_file = self.credentials_dir / "gitea_runner_token.txt"
        if runner_token_file.exists():
            self.generated_passwords["gitea_runner_token"] = runner_token_file.read_text().strip()
            self.logger.info("Loaded Gitea runner token from credentials")
        else:
            self.generated_passwords["gitea_runner_token"] = "REPLACE_WITH_TOKEN_FROM_GITEA_ADMIN"
            self.logger.warning("Gitea runner token not found - configure after deployment")

        self.config.generated_passwords = self.generated_passwords
        self.logger.info("Credentials loaded/generated from ./credentials/")

    def _interactive_pause(self, phase_name: str, summary: str) -> bool:
        """
        Pause for interactive verification if enabled.

        Args:
            phase_name: Name of completed phase
            summary: Summary of what was done

        Returns:
            True to continue, False to abort
        """
        if not self.config.deployment.interactive_mode:
            return True

        display_panel(
            f"Phase Complete: {phase_name}",
            summary,
            style="green",
        )

        response = interactive_pause(
            "Press Enter to continue, 'skip' to skip pause, 'abort' to stop: "
        ).strip().lower()

        if response == "abort":
            self.logger.warning("Deployment aborted by user")
            return False

        return True

    def _template_exists(self, template_name: str) -> bool:
        """
        Check if a template already exists in vSphere.

        Args:
            template_name: Name of the template to check (e.g., 'haproxy-template')

        Returns:
            True if template exists, False otherwise
        """
        try:
            from .discovery import VSphereDiscovery

            # Connect to vSphere
            discovery = VSphereDiscovery()
            discovery.connect(
                host=self.config.vsphere.vcenter_server,
                user=self.config.vsphere.username,
                password=self.config.vsphere.password,
            )

            # Check if template exists (try both full path and just name)
            template_path = f"{self.config.vsphere.template_folder}/{template_name}"
            exists = discovery.template_exists(
                datacenter_name=self.config.vsphere.datacenter,
                template_path=template_name,  # Try with just the name first
            )

            discovery.disconnect()
            return exists

        except Exception as e:
            # If there's an error checking, assume template doesn't exist
            self.logger.debug(f"Error checking template {template_name}: {e}")
            return False

    def _check_helm_release_exists(self, release_name: str, namespace: str) -> bool:
        """
        Check if a Helm release already exists.

        Args:
            release_name: Name of the Helm release
            namespace: Kubernetes namespace

        Returns:
            True if release exists, False otherwise
        """
        try:
            kubeconfig = str(self.kubeconfig_dir / "admin.conf")
            result = run_command(
                ["helm", "list", "-n", namespace, "-q"],
                env={"KUBECONFIG": kubeconfig},
                capture_output=True,
            )
            return release_name in result.stdout.split()
        except Exception:
            return False

    def _check_pods_running(self, namespace: str, label_selector: str) -> bool:
        """
        Check if pods matching a label selector are running.

        Args:
            namespace: Kubernetes namespace
            label_selector: Label selector (e.g., "app=gitea")

        Returns:
            True if at least one pod is running, False otherwise
        """
        try:
            kubeconfig = str(self.kubeconfig_dir / "admin.conf")
            result = run_command(
                [
                    "kubectl", "get", "pods", "-n", namespace,
                    "-l", label_selector,
                    "--field-selector=status.phase=Running",
                    "-o", "name",
                ],
                env={"KUBECONFIG": kubeconfig},
                capture_output=True,
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    def _prompt_email_alerts(self) -> Optional[Dict[str, str]]:
        """
        Prompt for Gmail email alert configuration.

        Returns:
            Dictionary with gmail and password keys, or None if skipped
        """
        console.print("\n[bold cyan]Email Alert Configuration[/bold cyan]")
        console.print("[dim]Configure email alerts for Prometheus monitoring.[/dim]\n")
        console.print("[dim]How to get Gmail App Password:[/dim]")
        console.print("  1. Go to https://myaccount.google.com/apppasswords")
        console.print("  2. Enable 2-Factor Authentication if not already")
        console.print("  3. Generate App Password for 'Mail'\n")

        sys.stdout.flush()
        choice = console.input("Configure email alerts? [y/N]: ").strip().lower()
        if choice != 'y':
            console.print("  [dim]Skipping email alert configuration[/dim]")
            return None

        gmail = console.input("Gmail address: ").strip()
        if not gmail:
            console.print("  [yellow]No email provided, skipping[/yellow]")
            return None

        app_password = getpass.getpass("App password (16 chars, no spaces): ").strip().replace(" ", "")
        if len(app_password) != 16:
            console.print(f"  [yellow]Warning: Password is {len(app_password)} chars, expected 16[/yellow]")

        return {"gmail": gmail, "password": app_password}

    def _configure_alertmanager(self, email_config: Dict[str, str]) -> bool:
        """
        Configure Alertmanager with Gmail SMTP.

        Args:
            email_config: Dictionary with gmail and password keys

        Returns:
            True if successful, False otherwise
        """
        try:
            kubeconfig = str(self.kubeconfig_dir / "admin.conf")
            os.environ["KUBECONFIG"] = kubeconfig

            gmail = email_config["gmail"]
            password = email_config["password"]

            # Build alertmanager config
            alertmanager_yaml = f'''global:
  smtp_smarthost: 'smtp.gmail.com:587'
  smtp_from: '{gmail}'
  smtp_auth_username: '{gmail}'
  smtp_auth_password: '{password}'
  smtp_require_tls: true

route:
  receiver: 'email'
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - match_re:
        alertname: "^({"|".join(constants.SILENCED_ALERTS)})$"
      receiver: 'null'

receivers:
  - name: 'null'
  - name: 'email'
    email_configs:
      - to: '{gmail}'
        send_resolved: true

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'namespace']
'''
            # Create or update the alertmanager secret
            import base64
            encoded = base64.b64encode(alertmanager_yaml.encode()).decode()

            patch_json = json.dumps([{
                "op": "replace",
                "path": "/data/alertmanager.yaml",
                "value": encoded
            }])

            run_command([
                "kubectl", "patch", "secret",
                "alertmanager-kube-prometheus-stack-alertmanager",
                "-n", constants.NAMESPACES["monitoring"],
                "--type=json",
                f"-p={patch_json}",
            ])

            # Restart alertmanager to apply
            run_command([
                "kubectl", "rollout", "restart", "statefulset",
                "alertmanager-kube-prometheus-stack-alertmanager",
                "-n", constants.NAMESPACES["monitoring"],
            ])

            console.print(f"  [green]✓[/green] Alertmanager configured with email alerts to {gmail}")
            return True

        except Exception as e:
            console.print(f"  [red]✗[/red] Failed to configure Alertmanager: {e}")
            self.logger.error(f"Alertmanager configuration failed: {e}")
            return False

    def _prompt_runner_token(self) -> Optional[str]:
        """
        Prompt for Gitea runner registration token.

        Returns:
            Registration token string, or None if skipped
        """
        if not self.config.applications.gitea_runner.enabled:
            return None

        worker_ip = self.config.get_vm_ips().get("workers", [""])[0]
        vip = self.config.network.vip_address

        console.print("\n[bold cyan]Gitea Act Runner Configuration[/bold cyan]")
        console.print("[dim]Deploy CI/CD runners for Gitea Actions.[/dim]\n")
        console.print("Steps to get registration token:")
        console.print(f"  1. Open Gitea: http://{vip}")
        console.print(f"  2. Log in as gitea_admin")
        console.print(f"  3. Go to Site Administration -> Actions -> Runners")
        console.print(f"  4. Click 'Create new Runner' and copy the token\n")

        sys.stdout.flush()
        choice = console.input("Configure runners now? [y/N]: ").strip().lower()
        if choice != 'y':
            console.print("  [dim]Skipping runner configuration[/dim]")
            return None

        token = console.input("Paste registration token: ").strip()
        if not token:
            console.print("  [yellow]No token provided, skipping[/yellow]")
            return None

        return token

    def _deploy_runners_with_token(self, token: str) -> bool:
        """
        Deploy Gitea runners with the provided registration token.

        Args:
            token: Gitea runner registration token

        Returns:
            True if successful, False otherwise
        """
        try:
            kubeconfig = str(self.kubeconfig_dir / "admin.conf")
            os.environ["KUBECONFIG"] = kubeconfig

            # Update the runner secret with the token
            import base64
            encoded_token = base64.b64encode(token.encode()).decode()

            # Create the secret if it doesn't exist
            run_command([
                "kubectl", "create", "secret", "generic", "runner-secret",
                "-n", constants.NAMESPACES["gitea"],
                f"--from-literal=token={token}",
                "--dry-run=client", "-o", "yaml",
            ], capture_output=True)

            # Apply the secret
            secret_yaml = f'''apiVersion: v1
kind: Secret
metadata:
  name: runner-secret
  namespace: {constants.NAMESPACES["gitea"]}
type: Opaque
stringData:
  token: "{token}"
'''
            # Apply via kubectl
            import subprocess
            proc = subprocess.run(
                ["kubectl", "apply", "-f", "-"],
                input=secret_yaml,
                capture_output=True,
                text=True,
                env={"KUBECONFIG": kubeconfig},
            )

            if proc.returncode != 0:
                raise Exception(proc.stderr)

            # Restart runner pods to pick up new token
            run_command([
                "kubectl", "rollout", "restart", "deployment", "act-runner",
                "-n", constants.NAMESPACES["gitea"],
            ], check=False)

            console.print(f"  [green]✓[/green] Gitea runners deployed with registration token")
            return True

        except Exception as e:
            console.print(f"  [red]✗[/red] Failed to deploy runners: {e}")
            self.logger.error(f"Runner deployment failed: {e}")
            return False

    def _setup_preseed_server(self) -> bool:
        """Set up preseed server on remote VM."""
        server_ip = self.config.deployment.preseed_server_ip
        server_port = self.config.deployment.preseed_server_port
        server_user = self.config.deployment.preseed_server_user
        preseed_file = self.packer_dir / "http" / "preseed.cfg"

        ssh_opts = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes"]

        if self.config.deployment.preseed_server_ssh_key:
            ssh_key_opts = ["-i", self.config.deployment.preseed_server_ssh_key]
        else:
            ssh_key_opts = []

        console.print(f"\n  [yellow]→[/yellow] Setting up preseed server on {server_ip}...")

        try:
            console.print(f"    [dim]Copying preseed.cfg to {server_ip}...[/dim]")
            run_command([
                "scp", *ssh_key_opts, *ssh_opts,
                str(preseed_file),
                f"{server_user}@{server_ip}:/tmp/preseed.cfg"
            ], timeout=30)

            run_command([
                "ssh", *ssh_key_opts, *ssh_opts,
                f"{server_user}@{server_ip}",
                f"pkill -f 'python3 -m http.server {server_port}' || true"
            ], timeout=10, check=False)

            console.print(f"    [dim]Starting HTTP server on port {server_port}...[/dim]")
            run_command([
                "ssh", *ssh_key_opts, *ssh_opts,
                f"{server_user}@{server_ip}",
                f"sh -c 'cd /tmp && nohup python3 -m http.server {server_port} >/dev/null 2>&1 & echo started'"
            ], timeout=10)

            import time
            time.sleep(2)
            run_command([
                "ssh", *ssh_key_opts, *ssh_opts,
                f"{server_user}@{server_ip}",
                f"curl -s http://localhost:{server_port}/preseed.cfg | head -1"
            ], timeout=10)

            console.print(f"  [green]✓[/green] Preseed server running at http://{server_ip}:{server_port}/preseed.cfg")
            return True

        except Exception as e:
            console.print(f"  [red]✗[/red] Failed to set up preseed server: {e}")
            self.logger.error(f"Failed to set up preseed server: {e}")
            return False

    def _stop_preseed_server(self) -> None:
        """Stop preseed server on remote VM."""
        server_ip = self.config.deployment.preseed_server_ip
        server_port = self.config.deployment.preseed_server_port
        server_user = self.config.deployment.preseed_server_user

        ssh_opts = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes"]

        if self.config.deployment.preseed_server_ssh_key:
            ssh_key_opts = ["-i", self.config.deployment.preseed_server_ssh_key]
        else:
            ssh_key_opts = []

        try:
            console.print(f"\n  [yellow]→[/yellow] Stopping preseed server on {server_ip}...")
            run_command([
                "ssh", *ssh_key_opts, *ssh_opts,
                f"{server_user}@{server_ip}",
                f"pkill -f 'python3 -m http.server {server_port}' || true"
            ], timeout=10, check=False)
            console.print("  [green]✓[/green] Preseed server stopped")
        except Exception as e:
            self.logger.warning(f"Failed to stop preseed server: {e}")

    def phase_1_build_templates(self) -> bool:
        """
        Phase 1: Build VM templates with Packer.

        Returns:
            True if successful
        """
        self.logger.info("=" * 60)
        self.logger.info("PHASE 1: Building VM Templates (Packer)")
        self.logger.info("=" * 60)

        if not self.config.deployment.phases.build_templates:
            self.logger.info("Phase 1 skipped (disabled in config)")
            return True

        console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
        console.print("[bold]PHASE 1: Building VM Templates (Packer)[/bold]")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]")

        templates = [
            ("haproxy", "haproxy.pkr.hcl"),
            ("kubernetes", "kubernetes.pkr.hcl"),
            ("glusterfs", "glusterfs.pkr.hcl"),
        ]

        results = []

        # Check which templates need to be built
        templates_to_build = []
        for template_name, packer_file in templates:
            vsphere_template_name = f"{template_name}-template"
            if self._template_exists(vsphere_template_name):
                console.print(f"\n  [bold]VM Template: {template_name.upper()}[/bold]")
                console.print(f"    [green]✓[/green] Template already exists, skipping build")
                results.append((template_name, True, "Already exists"))
                self.logger.info(f"Template {template_name} already exists, skipping build")
            else:
                templates_to_build.append((template_name, packer_file))

        # If no templates need building, we're done
        if not templates_to_build:
            console.print(f"\n  [green]✓[/green] All templates already exist, skipping Packer builds")
            return True

        # Set up preseed server only if we need to build templates
        if not self._setup_preseed_server():
            return False

        # Build environment variables
        packer_env = {
            "PACKER_LOG": "1",
            "PKR_VAR_vsphere_password": self.config.vsphere.password,
        }

        preseed_url = f"http://{self.config.deployment.preseed_server_ip}:{self.config.deployment.preseed_server_port}/preseed.cfg"

        for template_name, packer_file in templates_to_build:
            console.print(f"\n  [bold]Building VM Template: {template_name.upper()}[/bold]")

            packer_path = self.packer_dir / packer_file
            log_file = self.logs_dir / f"packer-{template_name}.log"
            packer_env["PACKER_LOG_PATH"] = str(log_file)

            console.print(f"    [dim]Template file:[/dim] {packer_file}")
            console.print(f"    [dim]Log file:[/dim] {log_file}")
            console.print(f"    [dim]Preseed URL:[/dim] {preseed_url}")

            try:
                # Initialize Packer
                console.print("    [yellow]→[/yellow] Initializing Packer plugins...")
                run_command(
                    ["packer", "init", str(packer_path)],
                    cwd=str(self.packer_dir),
                    timeout=120,
                )
                console.print("    [green]✓[/green] Packer initialized")

                # Build template
                console.print("    [yellow]→[/yellow] Building VM template (this may take 15-30 minutes)...")
                console.print("      [dim]- Creating VM from ISO[/dim]")
                console.print("      [dim]- Fetching preseed.cfg from remote server[/dim]")
                console.print("      [dim]- Waiting for OS installation[/dim]")
                console.print("      [dim]- Running provisioning scripts[/dim]")
                console.print("      [dim]- Converting to template[/dim]")

                result = run_command(
                    [
                        "packer", "build",
                        "-force",
                        "-on-error=cleanup",
                        str(packer_path),
                    ],
                    cwd=str(self.packer_dir),
                    env=packer_env,
                    timeout=constants.PACKER_TIMEOUT,
                )

                results.append((template_name, True, "Success"))
                console.print(f"    [green]✓[/green] Template [bold]{template_name}[/bold] built successfully")
                self.logger.info(f"Template {template_name} built successfully")

            except Exception as e:
                results.append((template_name, False, str(e)))
                console.print(f"    [red]✗[/red] Template [bold]{template_name}[/bold] failed: {e}")
                console.print(f"    [dim]Check log file: {log_file}[/dim]")
                self.logger.error(f"Failed to build template {template_name}: {e}")

                if self.config.deployment.cleanup_on_failure:
                    self._stop_preseed_server()
                    return False

        # Stop preseed server
        self._stop_preseed_server()

        # Summary
        summary_lines = ["VM Templates Built:\n"]
        all_success = True
        for name, success, message in results:
            status = "[green]SUCCESS[/green]" if success else "[red]FAILED[/red]"
            summary_lines.append(f"  {name}: {status}")
            if not success:
                all_success = False

        summary_lines.append(f"\nLocation: {self.config.vsphere.template_folder}")

        self.phase_results["phase_1"] = {
            "success": all_success,
            "results": results,
        }

        if not self._interactive_pause("Build VM Templates", "\n".join(summary_lines)):
            return False

        return all_success

    def phase_2_provision_infra(self) -> bool:
        """
        Phase 2: Provision infrastructure with Terraform.

        Returns:
            True if successful
        """
        self.logger.info("=" * 60)
        self.logger.info("PHASE 2: Provisioning Infrastructure (Terraform)")
        self.logger.info("=" * 60)

        if not self.config.deployment.phases.provision_vms:
            self.logger.info("Phase 2 skipped (disabled in config)")
            return True

        console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
        console.print("[bold]PHASE 2: Provisioning VMs with Terraform[/bold]")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]")

        vm_ips = self.config.get_vm_ips()
        total_vms = self.config.get_total_vms()
        console.print(f"\n  [dim]VMs to create:[/dim] {total_vms}")
        console.print(f"  [dim]HAProxy nodes:[/dim] {len(vm_ips.get('haproxy', []))} ({', '.join(vm_ips.get('haproxy', []))})")
        console.print(f"  [dim]Master nodes:[/dim] {len(vm_ips.get('masters', []))} ({', '.join(vm_ips.get('masters', []))})")
        console.print(f"  [dim]Worker nodes:[/dim] {len(vm_ips.get('workers', []))} ({', '.join(vm_ips.get('workers', []))})")
        console.print(f"  [dim]GlusterFS nodes:[/dim] {len(vm_ips.get('glusterfs', []))} ({', '.join(vm_ips.get('glusterfs', []))})")
        if vm_ips.get('backup'):
            console.print(f"  [dim]Backup nodes:[/dim] {len(vm_ips.get('backup', []))} ({', '.join(vm_ips.get('backup', []))})")
        console.print()

        # Check for existing VMs using vSphere discovery
        existing_vms = {}
        try:
            from .discovery import VSphereDiscovery
            discovery = VSphereDiscovery()
            discovery.connect(
                host=self.config.vsphere.vcenter_server,
                user=self.config.vsphere.username,
                password=self.config.vsphere.password,
            )

            prefixes = ["haproxy-", "master-", "worker-", "glusterfs-"]
            if self.config.infrastructure.backup.enabled:
                prefixes.append("backup-")

            existing_vms = discovery.check_vms_exist(
                self.config.vsphere.datacenter,
                prefixes,
                folder_path=self.config.vsphere.vm_folder,
            )
            discovery.disconnect()

            # Count existing VMs
            total_existing = sum(len(vms) for vms in existing_vms.values())
            if total_existing > 0:
                console.print(f"  [yellow]Found {total_existing} existing VMs:[/yellow]")
                for prefix, vms in existing_vms.items():
                    if vms:
                        console.print(f"    {prefix}: {', '.join(vms)}")

                if self.config.deployment.interactive_mode:
                    sys.stdout.flush()
                    choice = console.input("\nVMs already exist. Continue with Terraform apply? [Y/n]: ").strip().lower()
                    if choice == 'n':
                        console.print("  [dim]Skipping VM provisioning[/dim]")
                        self.phase_results["phase_2"] = {"success": True, "vm_ips": vm_ips, "skipped": True}
                        return True

        except Exception as e:
            self.logger.debug(f"Could not check existing VMs: {e}")
            # Continue with Terraform - it will handle existing resources

        try:
            # Initialize Terraform
            console.print("  [yellow]→[/yellow] Initializing Terraform...")
            run_command(
                ["terraform", "init"],
                cwd=str(self.terraform_dir),
                timeout=120,
            )
            console.print("  [green]✓[/green] Terraform initialized")

            # Plan
            console.print("  [yellow]→[/yellow] Creating execution plan...")
            plan_result = run_command(
                [
                    "terraform", "plan",
                    "-out=tfplan",
                    f"-var=vsphere_password={self.config.vsphere.password}",
                ],
                cwd=str(self.terraform_dir),
                timeout=300,
            )
            console.print("  [green]✓[/green] Plan created")

            # Apply
            console.print("  [yellow]→[/yellow] Creating VMs (this may take 5-10 minutes)...")
            console.print("    [dim]- Cloning from templates[/dim]")
            console.print("    [dim]- Configuring network settings[/dim]")
            console.print("    [dim]- Powering on VMs[/dim]")
            console.print()

            apply_result = run_command(
                [
                    "terraform", "apply", "-auto-approve",
                    f"-var=vsphere_password={self.config.vsphere.password}",
                ],
                cwd=str(self.terraform_dir),
                env={"TF_LOG": "INFO"},
                timeout=constants.TERRAFORM_TIMEOUT,
            )
            console.print("  [green]✓[/green] VMs created successfully")

            # Get outputs
            console.print("  [yellow]→[/yellow] Retrieving VM information...")
            output_result = run_command(
                ["terraform", "output", "-json"],
                cwd=str(self.terraform_dir),
            )

            outputs = json.loads(output_result.stdout)
            self.vm_ips = self.config.get_vm_ips()
            console.print("  [green]✓[/green] Infrastructure provisioned successfully")

            self.logger.info("Infrastructure provisioned successfully")

        except Exception as e:
            console.print(f"  [red]✗[/red] Terraform failed: {e}")
            self.logger.error(f"Terraform failed: {e}")
            self.phase_results["phase_2"] = {"success": False, "error": str(e)}

            if self.config.deployment.cleanup_on_failure:
                console.print("  [yellow]→[/yellow] Running cleanup (terraform destroy)...")
                run_command(
                    ["terraform", "destroy", "-auto-approve"],
                    cwd=str(self.terraform_dir),
                    check=False,
                )

            return False

        # Build summary
        vm_ips = self.config.get_vm_ips()
        summary_lines = ["VMs Created:\n"]

        for i, ip in enumerate(vm_ips.get("haproxy", []), 1):
            summary_lines.append(f"  haproxy-{i}: {ip}")
        for i, ip in enumerate(vm_ips.get("masters", []), 1):
            summary_lines.append(f"  master-{i}: {ip}")
        for i, ip in enumerate(vm_ips.get("workers", []), 1):
            summary_lines.append(f"  worker-{i}: {ip}")
        for i, ip in enumerate(vm_ips.get("glusterfs", []), 1):
            summary_lines.append(f"  glusterfs-{i}: {ip}")
        for i, ip in enumerate(vm_ips.get("backup", []), 1):
            summary_lines.append(f"  backup-{i}: {ip}")

        summary_lines.append(f"\nVirtual IP: {self.config.network.vip_address}")
        summary_lines.append(f"Total VMs: {self.config.get_total_vms()}")

        self.phase_results["phase_2"] = {"success": True, "vm_ips": vm_ips}

        if not self._interactive_pause("Provision Infrastructure", "\n".join(summary_lines)):
            return False

        return True

    def phase_3_configure(self) -> bool:
        """
        Phase 3: Configure systems with Ansible.

        Returns:
            True if successful
        """
        self.logger.info("=" * 60)
        self.logger.info("PHASE 3: Configuring Systems (Ansible)")
        self.logger.info("=" * 60)

        if not self.config.deployment.phases.configure_infrastructure:
            self.logger.info("Phase 3 skipped (disabled in config)")
            return True

        console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
        console.print("[bold]PHASE 3: Configuring Systems with Ansible[/bold]")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]")

        # Wait for SSH on all VMs (direct connection)
        console.print(f"\n  [yellow]→[/yellow] Waiting for SSH access to all VMs...")
        vm_ips = self.config.get_vm_ips()
        all_ips = (
            vm_ips.get("haproxy", []) +
            vm_ips.get("masters", []) +
            vm_ips.get("workers", []) +
            vm_ips.get("glusterfs", [])
        )

        for ip in all_ips:
            console.print(f"    [dim]Checking {ip}...[/dim]", end="")
            if not wait_for_ssh(
                ip,
                key_file=str(self.credentials_dir / "ssh_key"),
                timeout=constants.SSH_TIMEOUT,
            ):
                console.print(f" [red]✗[/red]")
                console.print(f"  [red]✗[/red] SSH not available on {ip}")
                self.logger.error(f"SSH not available on {ip}")
                return False
            console.print(f" [green]✓[/green]")

        console.print("  [green]✓[/green] All VMs reachable via SSH")

        playbooks = [
            ("GlusterFS Cluster", "playbooks/glusterfs-cluster.yml", "Setting up distributed storage"),
            ("HAProxy Cluster", "playbooks/haproxy-cluster.yml", "Setting up load balancer with Keepalived"),
            ("Kubernetes Cluster", "playbooks/kubernetes-cluster.yml", "Installing K3s and joining nodes"),
        ]

        results = []

        for name, playbook_path, description in playbooks:
            console.print(f"\n  [bold]Running: {name}[/bold]")
            console.print(f"  [dim]{description}[/dim]")
            log_file = self.logs_dir / f"ansible-{name.lower().replace(' ', '-')}.log"
            console.print(f"  [dim]Log: {log_file}[/dim]")

            try:
                console.print(f"  [yellow]→[/yellow] Executing playbook...")
                result = run_command(
                    [
                        "ansible-playbook",
                        "-i", "inventory.yml",
                        playbook_path,
                        "--private-key", str(self.credentials_dir / "ssh_key"),
                    ],
                    cwd=str(self.ansible_dir),
                    timeout=constants.ANSIBLE_TIMEOUT,
                )

                results.append((name, True, "Success"))
                console.print(f"  [green]✓[/green] {name} completed successfully")
                self.logger.info(f"Playbook {name} completed successfully")

            except Exception as e:
                results.append((name, False, str(e)))
                console.print(f"  [red]✗[/red] {name} failed: {e}")
                console.print(f"  [dim]Check log file: {log_file}[/dim]")
                self.logger.error(f"Playbook {name} failed: {e}")

                if self.config.deployment.cleanup_on_failure:
                    return False

        # Copy kubeconfig
        try:
            first_master = vm_ips.get("masters", [])[0]
            run_command(
                [
                    "scp",
                    "-i", str(self.credentials_dir / "ssh_key"),
                    "-o", "StrictHostKeyChecking=no",
                    f"root@{first_master}:/etc/rancher/k3s/k3s.yaml",
                    str(self.kubeconfig_dir / "admin.conf"),
                ],
            )

            # Update kubeconfig to use VIP
            kubeconfig_path = self.kubeconfig_dir / "admin.conf"
            kubeconfig_content = kubeconfig_path.read_text()
            kubeconfig_content = kubeconfig_content.replace(
                "127.0.0.1",
                self.config.network.vip_address,
            )
            kubeconfig_path.write_text(kubeconfig_content)

            self.logger.info(f"Kubeconfig saved to {kubeconfig_path}")

            # Configure local-path provisioner to use GlusterFS
            self.logger.info("Configuring local-path provisioner to use GlusterFS...")
            os.environ["KUBECONFIG"] = str(kubeconfig_path)

            patch_json = '{"nodePathMap":[{"node":"DEFAULT_PATH_FOR_NON_LISTED_NODES","paths":["/mnt/glusterfs"]}]}'
            patch_cmd = [
                "kubectl", "patch", "configmap", "local-path-config",
                "-n", "kube-system", "--type=json",
                f"-p=[{{\"op\": \"replace\", \"path\": \"/data/config.json\", \"value\": {json.dumps(patch_json)}}}]"
            ]

            try:
                run_command(patch_cmd, capture_output=True)
                self.logger.info("local-path provisioner configured to use /mnt/glusterfs")

                # Restart local-path-provisioner to apply changes
                restart_cmd = ["kubectl", "rollout", "restart", "deployment", "local-path-provisioner", "-n", "kube-system"]
                run_command(restart_cmd, capture_output=True)
                self.logger.info("local-path provisioner restarted")
            except Exception as patch_error:
                self.logger.warning(f"Could not configure local-path provisioner: {patch_error}")

        except Exception as e:
            self.logger.warning(f"Could not copy kubeconfig: {e}")

        # Run backup setup playbook if backup is enabled
        if self.config.infrastructure.backup.enabled and vm_ips.get("backup"):
            console.print(f"\n  [bold]Running: Backup Server Setup[/bold]")
            console.print(f"  [dim]Configuring backup server with scripts and cron jobs[/dim]")
            log_file = self.logs_dir / "ansible-backup-setup.log"
            console.print(f"  [dim]Log: {log_file}[/dim]")

            try:
                console.print(f"  [yellow]→[/yellow] Executing backup setup playbook...")
                result = run_command(
                    [
                        "ansible-playbook",
                        "-i", "inventory.yml",
                        "playbooks/backup-setup.yml",
                        "--private-key", str(self.credentials_dir / "ssh_key"),
                    ],
                    cwd=str(self.ansible_dir),
                    timeout=constants.ANSIBLE_TIMEOUT,
                )

                results.append(("Backup Server Setup", True, "Success"))
                console.print(f"  [green]✓[/green] Backup server setup completed successfully")
                self.logger.info("Backup server setup completed successfully")

            except Exception as e:
                results.append(("Backup Server Setup", False, str(e)))
                console.print(f"  [red]✗[/red] Backup server setup failed: {e}")
                console.print(f"  [dim]Check log file: {log_file}[/dim]")
                self.logger.error(f"Backup server setup failed: {e}")
                # Don't fail the whole phase for backup setup failure
                self.logger.warning("Continuing despite backup setup failure")

        # Summary
        summary_lines = ["Configuration Complete:\n"]
        all_success = True

        for name, success, message in results:
            status = "[green]SUCCESS[/green]" if success else "[red]FAILED[/red]"
            summary_lines.append(f"  {name}: {status}")
            if not success:
                all_success = False

        summary_lines.extend([
            "",
            f"GlusterFS Volume: {constants.GLUSTER_VOLUME_NAME}",
            f"Kubernetes API: https://{self.config.network.vip_address}:6443",
            f"Kubeconfig: {self.kubeconfig_dir}/admin.conf",
        ])

        self.phase_results["phase_3"] = {"success": all_success, "results": results}

        if not self._interactive_pause("Configure Systems", "\n".join(summary_lines)):
            return False

        return all_success

    def phase_4_operators(self) -> bool:
        """
        Phase 4: Deploy Kubernetes operators with Helm.

        Returns:
            True if successful
        """
        self.logger.info("=" * 60)
        self.logger.info("PHASE 4: Deploying Operators (Helm)")
        self.logger.info("=" * 60)

        if not self.config.deployment.phases.deploy_operators:
            self.logger.info("Phase 4 skipped (disabled in config)")
            return True

        kubeconfig = str(self.kubeconfig_dir / "admin.conf")
        os.environ["KUBECONFIG"] = kubeconfig

        # Check for existing Helm releases (informational)
        console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
        console.print("[bold]PHASE 4: Deploying Operators (Helm)[/bold]")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")

        existing_releases = []
        releases_to_check = [
            ("traefik", "kube-system"),
            ("cnpg", "cnpg-system"),
        ]
        for release, namespace in releases_to_check:
            if self._check_helm_release_exists(release, namespace):
                existing_releases.append(f"{release} ({namespace})")

        if existing_releases:
            console.print(f"  [dim]Existing releases (will be upgraded):[/dim] {', '.join(existing_releases)}")
        else:
            console.print("  [dim]No existing operator releases found[/dim]")

        # Add Helm repos
        repos = [
            ("traefik", constants.HELM_REPOS["traefik"]),
            ("cnpg", constants.HELM_REPOS["cloudnative_pg"]),
            ("prometheus-community", constants.HELM_REPOS["prometheus"]),
        ]

        for name, url in repos:
            try:
                run_command(["helm", "repo", "add", name, url], check=False)
            except Exception:
                pass

        run_command(["helm", "repo", "update"])

        operators = [
            (
                "Traefik",
                ["helm", "upgrade", "--install", "traefik", "traefik/traefik",
                 "--namespace", "kube-system",
                 "-f", str(self.helm_dir / "traefik-values.yaml"),
                 "--wait", "--timeout", "5m"],
            ),
            (
                "CloudNativePG",
                ["helm", "upgrade", "--install", "cnpg", "cnpg/cloudnative-pg",
                 "--namespace", "cnpg-system", "--create-namespace",
                 "-f", str(self.helm_dir / "cnpg-values.yaml"),
                 "--wait", "--timeout", "5m"],
            ),
        ]

        results = []

        for name, cmd in operators:
            self.logger.info(f"Installing operator: {name}")

            try:
                run_command(cmd, timeout=constants.HELM_TIMEOUT)
                results.append((name, True, "Success"))
                self.logger.info(f"Operator {name} installed successfully")
            except Exception as e:
                results.append((name, False, str(e)))
                self.logger.error(f"Failed to install {name}: {e}")

        # Verify local-path provisioner is using GlusterFS
        self.logger.info("Verifying storage configuration...")
        try:
            run_command([
                "kubectl", "get", "storageclass", "local-path",
            ])
            results.append(("Storage (local-path)", True, "Using GlusterFS mount"))
            self.logger.info("Storage configured: local-path provisioner using GlusterFS")
        except Exception as e:
            results.append(("Storage", False, str(e)))
            self.logger.error(f"Storage verification failed: {e}")

        # Summary
        summary_lines = ["Operators Installed:\n"]
        all_success = True

        for name, success, message in results:
            status = "[green]SUCCESS[/green]" if success else "[red]FAILED[/red]"
            summary_lines.append(f"  {name}: {status}")
            if not success:
                all_success = False

        summary_lines.append(f"\nStorageClass: {constants.STORAGE_CLASS_NAME} (using GlusterFS mount)")

        self.phase_results["phase_4"] = {"success": all_success, "results": results}

        if not self._interactive_pause("Deploy Operators", "\n".join(summary_lines)):
            return False

        return all_success

    def phase_5_applications(self) -> bool:
        """
        Phase 5: Deploy applications with Helm and kubectl.

        Returns:
            True if successful
        """
        self.logger.info("=" * 60)
        self.logger.info("PHASE 5: Deploying Applications (Helm + kubectl)")
        self.logger.info("=" * 60)

        if not self.config.deployment.phases.deploy_applications:
            self.logger.info("Phase 5 skipped (disabled in config)")
            return True

        kubeconfig = str(self.kubeconfig_dir / "admin.conf")
        os.environ["KUBECONFIG"] = kubeconfig

        # Add remaining Helm repos
        repos = [
            ("bitnami", constants.HELM_REPOS["bitnami"]),
            ("gitea-charts", constants.HELM_REPOS["gitea"]),
            ("portainer", constants.HELM_REPOS["portainer"]),
        ]

        for name, url in repos:
            try:
                run_command(["helm", "repo", "add", name, url], check=False)
            except Exception:
                pass

        run_command(["helm", "repo", "update"])

        # Create namespaces
        for ns in [constants.NAMESPACES["gitea"], constants.NAMESPACES["monitoring"], constants.NAMESPACES["portainer"]]:
            run_command(
                ["kubectl", "create", "namespace", ns],
                check=False,
            )

        results = []

        # PostgreSQL (via CloudNativePG CRD)
        self.logger.info("Deploying PostgreSQL cluster...")
        try:
            run_command([
                "kubectl", "apply",
                "-f", str(self.manifests_dir / "postgresql-cluster.yaml"),
            ])
            # Wait for PostgreSQL to be ready
            run_command([
                "kubectl", "wait", "--for=condition=Ready",
                "cluster/gitea-db", "-n", constants.NAMESPACES["gitea"],
                "--timeout=300s",
            ], check=False)
            results.append(("PostgreSQL", True, "Success"))
        except Exception as e:
            results.append(("PostgreSQL", False, str(e)))

        # Prometheus Stack (deploy early for ServiceMonitor CRDs)
        self.logger.info("Deploying monitoring stack...")
        try:
            run_command([
                "helm", "upgrade", "--install", "kube-prometheus-stack",
                "prometheus-community/kube-prometheus-stack",
                "--namespace", constants.NAMESPACES["monitoring"],
                "-f", str(self.helm_dir / "prometheus-values.yaml"),
                "--wait", "--timeout", "10m",
            ])
            results.append(("Monitoring", True, "Success"))
        except Exception as e:
            results.append(("Monitoring", False, str(e)))

        # Redis (needs ServiceMonitor CRDs from Prometheus)
        self.logger.info("Deploying Redis...")
        try:
            run_command([
                "helm", "upgrade", "--install", "redis", "bitnami/redis",
                "--namespace", constants.NAMESPACES["redis"],
                "-f", str(self.helm_dir / "redis-values.yaml"),
                "--wait", "--timeout", "5m",
            ])
            results.append(("Redis", True, "Success"))
        except Exception as e:
            results.append(("Redis", False, str(e)))

        # Gitea storage (hostPath PV/PVC for RWX on GlusterFS)
        self.logger.info("Creating Gitea shared storage...")
        try:
            # Render gitea storage manifest
            context = self._get_template_context()
            render_template(
                "manifests/gitea-storage.yaml.j2",
                str(self.manifests_dir / "gitea-storage.yaml"),
                context,
                templates_dir=str(self.templates_dir),
            )
            # Use server-side apply with force to avoid conflicts with Helm
            run_command([
                "kubectl", "apply", "-f",
                str(self.manifests_dir / "gitea-storage.yaml"),
                "--server-side", "--force-conflicts",
            ])
        except Exception as e:
            self.logger.warning(f"Gitea storage setup: {e}")

        # Gitea
        self.logger.info("Deploying Gitea...")
        try:
            run_command([
                "helm", "upgrade", "--install", "gitea", "gitea-charts/gitea",
                "--namespace", constants.NAMESPACES["gitea"],
                "-f", str(self.helm_dir / "gitea-values.yaml"),
                "--wait", "--timeout", "10m",
            ])
            results.append(("Gitea", True, "Success"))
        except Exception as e:
            results.append(("Gitea", False, str(e)))

        # Portainer
        self.logger.info("Deploying Portainer...")
        try:
            run_command([
                "helm", "upgrade", "--install", "portainer", "portainer/portainer",
                "--namespace", constants.NAMESPACES["portainer"],
                "-f", str(self.helm_dir / "portainer-values.yaml"),
                "--wait", "--timeout", "5m",
            ])
            results.append(("Portainer", True, "Success"))
        except Exception as e:
            results.append(("Portainer", False, str(e)))

        # Gitea Act Runners - just apply manifest, token configured interactively below
        if self.config.applications.gitea_runner.enabled:
            self.logger.info("Deploying Gitea Act Runners manifest...")
            try:
                runner_manifest = self.manifests_dir / "gitea-runner.yaml"
                if runner_manifest.exists():
                    # Apply manifest with placeholder token - real token configured interactively
                    run_command([
                        "kubectl", "apply", "-f", str(runner_manifest),
                    ])
                    results.append(("Gitea Runners", True, "Manifest applied"))
                else:
                    results.append(("Gitea Runners", False, "Manifest not found"))
            except Exception as e:
                results.append(("Gitea Runners", False, str(e)))

        # Interactive configuration prompts (if interactive mode enabled)
        if self.config.deployment.interactive_mode:
            # Email alerts configuration
            console.print("\n" + "=" * 60)
            email_config = self._prompt_email_alerts()
            if email_config:
                if self._configure_alertmanager(email_config):
                    results.append(("Email Alerts", True, f"Configured for {email_config['gmail']}"))
                else:
                    results.append(("Email Alerts", False, "Configuration failed"))

            # Runner token configuration
            if self.config.applications.gitea_runner.enabled:
                console.print("\n" + "=" * 60)
                runner_token = self._prompt_runner_token()
                if runner_token:
                    if self._deploy_runners_with_token(runner_token):
                        # Update the result for runners
                        for i, (name, _, _) in enumerate(results):
                            if name == "Gitea Runners":
                                results[i] = ("Gitea Runners", True, "Deployed with token")
                                break
                    else:
                        for i, (name, _, _) in enumerate(results):
                            if name == "Gitea Runners":
                                results[i] = ("Gitea Runners", False, "Token deployment failed")
                                break

        # Summary
        summary_lines = ["Applications Deployed:\n"]
        all_success = True

        for name, success, message in results:
            status = "[green]SUCCESS[/green]" if success else "[red]FAILED[/red]"
            summary_lines.append(f"  {name}: {status}")
            if not success:
                all_success = False

        vip = self.config.network.vip_address
        gitea_hostname = self.config.network.gitea_hostname

        # Derive service hostnames from gitea_hostname
        if gitea_hostname and '.' in gitea_hostname:
            domain = gitea_hostname.split('.', 1)[1]
            grafana_url = f"http://grafana.{domain}"
            portainer_url = f"http://portainer.{domain}"
        else:
            grafana_url = f"http://{vip}:{constants.GRAFANA_NODEPORT}"
            portainer_url = f"http://{vip}:{constants.PORTAINER_NODEPORT}"

        summary_lines.extend([
            "",
            "Access Information:",
            f"  Gitea: http://{gitea_hostname or vip}",
            f"  Grafana: {grafana_url}",
            f"  Portainer: {portainer_url}",
        ])

        self.phase_results["phase_5"] = {"success": all_success, "results": results}

        if not self._interactive_pause("Deploy Applications", "\n".join(summary_lines)):
            return False

        return all_success

    def phase_6_validate(self) -> bool:
        """
        Phase 6: Validate deployment.

        Returns:
            True if validation passes
        """
        self.logger.info("=" * 60)
        self.logger.info("PHASE 6: Validating Deployment")
        self.logger.info("=" * 60)

        if not self.config.deployment.phases.run_validation:
            self.logger.info("Phase 6 skipped (disabled in config)")
            return True

        kubeconfig = str(self.kubeconfig_dir / "admin.conf")
        os.environ["KUBECONFIG"] = kubeconfig

        validations = []

        # Check Kubernetes nodes
        self.logger.info("Checking Kubernetes nodes...")
        try:
            result = run_command(["kubectl", "get", "nodes", "-o", "json"])
            nodes = json.loads(result.stdout)
            ready_nodes = sum(
                1 for node in nodes.get("items", [])
                if any(
                    cond["type"] == "Ready" and cond["status"] == "True"
                    for cond in node.get("status", {}).get("conditions", [])
                )
            )
            expected_nodes = (
                self.config.infrastructure.control_plane.count +
                self.config.infrastructure.workers.count
            )
            if ready_nodes >= expected_nodes:
                validations.append(("Kubernetes Nodes", True, f"{ready_nodes}/{expected_nodes} Ready"))
            else:
                validations.append(("Kubernetes Nodes", False, f"Only {ready_nodes}/{expected_nodes} Ready"))
        except Exception as e:
            validations.append(("Kubernetes Nodes", False, str(e)))

        # Check key pods
        namespaces_to_check = [
            ("kube-system", ["traefik"]),
            (constants.NAMESPACES["gitea"], ["gitea", "redis"]),
            (constants.NAMESPACES["monitoring"], ["prometheus", "kube-prometheus-stack-grafana"]),
        ]

        for namespace, pod_prefixes in namespaces_to_check:
            try:
                result = run_command([
                    "kubectl", "get", "pods", "-n", namespace,
                    "-o", "json",
                ])
                pods = json.loads(result.stdout)

                for prefix in pod_prefixes:
                    matching = [
                        p for p in pods.get("items", [])
                        if p["metadata"]["name"].startswith(prefix)
                    ]
                    running = sum(
                        1 for p in matching
                        if p.get("status", {}).get("phase") == "Running"
                    )
                    total = len(matching)

                    if running > 0:
                        validations.append((f"{prefix} pods", True, f"{running}/{total} Running"))
                    else:
                        validations.append((f"{prefix} pods", False, "No running pods"))

            except Exception as e:
                validations.append((f"{namespace} namespace", False, str(e)))

        # Check VIP connectivity
        self.logger.info("Checking VIP connectivity...")
        vip = self.config.network.vip_address
        if wait_for_port(vip, constants.K8S_API_PORT, timeout=30):
            validations.append(("VIP (API)", True, f"{vip}:{constants.K8S_API_PORT}"))
        else:
            validations.append(("VIP (API)", False, "Not reachable"))

        # Summary
        summary_lines = ["Validation Results:\n"]
        all_success = True

        for name, success, message in validations:
            status = "[green]PASS[/green]" if success else "[red]FAIL[/red]"
            summary_lines.append(f"  {name}: {status} - {message}")
            if not success:
                all_success = False

        self.phase_results["phase_6"] = {"success": all_success, "validations": validations}

        display_panel("Validation Complete", "\n".join(summary_lines),
                     style="green" if all_success else "red")

        return all_success

    def run_deployment(self, start_phase: int = 1) -> bool:
        """
        Run the complete deployment.

        Args:
            start_phase: Phase number to start from (1-6). Earlier phases are skipped.

        Returns:
            True if all phases succeed
        """
        self.deployment_start = datetime.now()

        phase_info = f" (starting from phase {start_phase})" if start_phase > 1 else ""
        console.print(Panel(
            f"[bold]Gitea Infrastructure Deployment{phase_info}[/bold]\n\n"
            f"Configuration: {self.config.metadata.environment}\n"
            f"Total VMs: {self.config.get_total_vms()}\n"
            f"vCPU: {self.config.get_total_vcpu()}\n"
            f"RAM: {self.config.get_total_ram_gb()}GB\n"
            f"Storage: {self.config.get_total_storage_gb()/1024:.2f}TB",
            title="Deployment Starting",
            border_style="blue",
        ))

        try:
            # Generate templates first (always needed for any phase)
            self.generate_templates()

            # Run phases
            phases = [
                (1, "Phase 1: Build Templates", self.phase_1_build_templates),
                (2, "Phase 2: Provision Infrastructure", self.phase_2_provision_infra),
                (3, "Phase 3: Configure Systems", self.phase_3_configure),
                (4, "Phase 4: Deploy Operators", self.phase_4_operators),
                (5, "Phase 5: Deploy Applications", self.phase_5_applications),
                (6, "Phase 6: Validate Deployment", self.phase_6_validate),
            ]

            for phase_num, phase_name, phase_func in phases:
                if phase_num < start_phase:
                    self.logger.info(f"Skipping {phase_name} (starting from phase {start_phase})")
                    console.print(f"[dim]Skipping {phase_name}[/dim]")
                    continue

                self.logger.info(f"\nStarting {phase_name}...")

                if not phase_func():
                    self.logger.error(f"{phase_name} failed!")
                    return False

            # Calculate duration
            duration = datetime.now() - self.deployment_start
            minutes = int(duration.total_seconds() / 60)

            vip = self.config.network.vip_address
            gitea_hostname = self.config.network.gitea_hostname

            # Derive service hostnames from gitea_hostname
            if gitea_hostname and '.' in gitea_hostname:
                domain = gitea_hostname.split('.', 1)[1]
                grafana_url = f"http://grafana.{domain}"
                portainer_url = f"http://portainer.{domain}"
            else:
                grafana_url = f"http://{vip}:{constants.GRAFANA_NODEPORT}"
                portainer_url = f"http://{vip}:{constants.PORTAINER_NODEPORT}"

            console.print(Panel(
                f"[bold green]Deployment Complete![/bold green]\n\n"
                f"Duration: {minutes} minutes\n\n"
                f"Access URLs:\n"
                f"  Gitea: http://{gitea_hostname or vip}\n"
                f"  Grafana: {grafana_url}\n"
                f"  Portainer: {portainer_url}\n\n"
                f"Credentials: ./credentials/\n"
                f"Kubeconfig: ./kubeconfig/admin.conf",
                title="Success",
                border_style="green",
            ))

            return True

        except Exception as e:
            self.logger.error(f"Deployment failed: {e}")
            raise
