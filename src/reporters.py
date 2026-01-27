"""
Deployment Reporting module for Gitea Infrastructure.

Generates comprehensive deployment reports and summaries.
"""

from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import DeploymentConfig
from .utils import get_logger, write_file, timestamp_filename
from . import constants

console = Console()


class DeploymentReporter:
    """
    Generates deployment reports and summaries.
    """

    def __init__(
        self,
        config: DeploymentConfig,
        project_root: Optional[str] = None,
    ):
        """
        Initialize DeploymentReporter.

        Args:
            config: DeploymentConfig instance
            project_root: Project root directory
        """
        self.logger = get_logger()
        self.config = config
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.credentials_dir = self.project_root / "credentials"
        self.kubeconfig_dir = self.project_root / "kubeconfig"

    def generate_report(
        self,
        phase_results: Dict[str, Any],
        deployment_start: datetime,
        deployment_end: Optional[datetime] = None,
    ) -> str:
        """
        Generate comprehensive deployment report.

        Args:
            phase_results: Results from each phase
            deployment_start: Deployment start time
            deployment_end: Deployment end time (defaults to now)

        Returns:
            Path to generated report file
        """
        if deployment_end is None:
            deployment_end = datetime.now()

        duration = deployment_end - deployment_start
        duration_minutes = int(duration.total_seconds() / 60)

        vm_ips = self.config.get_vm_ips()
        vip = self.config.network.vip_address

        # Derive service hostnames from gitea_hostname
        gitea_hostname = self.config.network.gitea_hostname
        if gitea_hostname and '.' in gitea_hostname:
            domain = gitea_hostname.split('.', 1)[1]
            grafana_url = f"http://grafana.{domain}"
            portainer_url = f"http://portainer.{domain}"
        else:
            grafana_url = f"http://{vip}:{constants.GRAFANA_NODEPORT}"
            portainer_url = f"http://{vip}:{constants.PORTAINER_NODEPORT}"

        # Determine overall status
        all_success = all(
            result.get("success", False)
            for result in phase_results.values()
        )
        status = "SUCCESS" if all_success else "PARTIAL SUCCESS"

        report_lines = [
            "=" * 80,
            "GITEA PRODUCTION INFRASTRUCTURE - DEPLOYMENT REPORT",
            "=" * 80,
            f"Deployment Date: {deployment_start.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Completion Date: {deployment_end.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total Duration: {duration_minutes} minutes",
            f"Status: {status}",
            "",
            "INFRASTRUCTURE SUMMARY",
            "-" * 80,
            f"Virtual Machines: {self.config.get_total_vms()}",
        ]

        # VM breakdown
        report_lines.extend([
            f"  - HAProxy: {self.config.infrastructure.haproxy.count}",
            f"  - Masters: {self.config.infrastructure.control_plane.count}",
            f"  - Workers: {self.config.infrastructure.workers.count}",
            f"  - GlusterFS: {self.config.infrastructure.glusterfs.count}",
            "",
            "Total Resources:",
            f"  - vCPU: {self.config.get_total_vcpu()}",
            f"  - RAM: {self.config.get_total_ram_gb()}GB",
            f"  - Storage: {self.config.get_total_storage_gb() / 1024:.2f}TB",
            "",
            "Network Configuration:",
            f"  - Network: {self.config.network.subnet_mask}",
            f"  - IP Range: {self.config.network.ip_start} - {self.config.network.ip_end}",
            f"  - Virtual IP: {self.config.network.vip_address}",
            f"  - Gateway: {self.config.network.gateway}",
            "",
        ])

        # VM inventory
        report_lines.extend([
            "VM INVENTORY",
            "-" * 80,
        ])

        for i, ip in enumerate(vm_ips.get("haproxy", []), 1):
            role = "MASTER" if i == 1 else "BACKUP"
            report_lines.append(f"  haproxy-{i:<8} {ip:<16} {role}")

        for i, ip in enumerate(vm_ips.get("masters", []), 1):
            report_lines.append(f"  master-{i:<9} {ip:<16} control-plane")

        for i, ip in enumerate(vm_ips.get("workers", []), 1):
            report_lines.append(f"  worker-{i:<9} {ip:<16} worker")

        for i, ip in enumerate(vm_ips.get("glusterfs", []), 1):
            report_lines.append(f"  glusterfs-{i:<6} {ip:<16} storage")

        # Kubernetes cluster
        report_lines.extend([
            "",
            "KUBERNETES CLUSTER",
            "-" * 80,
            f"Distribution: K3s {constants.K3S_VERSION}",
            f"Nodes: {self.config.infrastructure.control_plane.count + self.config.infrastructure.workers.count}",
            f"  - Masters: {self.config.infrastructure.control_plane.count}",
            f"  - Workers: {self.config.infrastructure.workers.count}",
            f"API Endpoint: https://{self.config.network.vip_address}:{constants.K8S_API_PORT}",
            "",
        ])

        # Storage layer
        report_lines.extend([
            "STORAGE LAYER",
            "-" * 80,
            "GlusterFS Cluster:",
            f"  Volume: {constants.GLUSTER_VOLUME_NAME}",
            f"  Type: {constants.GLUSTER_VOLUME_TYPE}",
            f"  Capacity: {self.config.get_glusterfs_usable_gb()}GB usable",
            f"  Brick Path: {constants.GLUSTER_BRICK_PATH}",
            "",
            f"Kubernetes StorageClass: {constants.STORAGE_CLASS_NAME}",
            "",
        ])

        # Applications
        report_lines.extend([
            "APPLICATIONS",
            "-" * 80,
            "PostgreSQL (CloudNativePG):",
            f"  Instances: {self.config.applications.postgresql.instances}",
            f"  Storage: {self.config.applications.postgresql.instances * self.config.applications.postgresql.storage_per_instance_gb}GB total",
            "",
            "Redis:",
            f"  Replicas: {self.config.applications.redis.replicas}",
            f"  Storage: {self.config.applications.redis.replicas * self.config.applications.redis.storage_per_replica_gb}GB total",
            "",
            "Gitea:",
            f"  Replicas: {self.config.applications.gitea.replicas}",
            f"  Repository Storage: {self.config.applications.gitea.repository_storage_gb}GB",
            f"  Attachment Storage: {self.config.applications.gitea.attachment_storage_gb}GB",
            "",
            "Monitoring Stack:",
            f"  Prometheus: {self.config.applications.prometheus.replicas} replicas",
            f"  Grafana: {self.config.applications.grafana.replicas} replicas",
            f"  Retention: {self.config.applications.prometheus.retention_days} days",
            "",
            "Portainer:",
            f"  Replicas: {self.config.applications.portainer.replicas}",
            "",
        ])

        # Access information
        gitea_url = f"http://{self.config.network.gitea_hostname or self.config.network.vip_address}"
        report_lines.extend([
            "ACCESS INFORMATION",
            "-" * 80,
            "For Students/Teachers:",
            f"  Gitea Web: {gitea_url}",
            f"  Git Clone (HTTP): git clone {gitea_url}/username/repository.git",
            f"  Git Clone (SSH): git clone ssh://git@{vip}:{constants.GITEA_SSH_NODEPORT}/username/repository.git",
            "",
            "For Administrators:",
            f"  Grafana: {grafana_url}",
            f"  Portainer: {portainer_url}",
            f"  HAProxy Stats: http://{vip}:{constants.HAPROXY_STATS_PORT}/stats",
            "",
            "kubectl Access:",
            f"  export KUBECONFIG={self.kubeconfig_dir}/admin.conf",
            "  kubectl get pods -A",
            "",
        ])

        # Credentials
        report_lines.extend([
            "CREDENTIALS",
            "-" * 80,
            f"Credentials saved to: {self.credentials_dir}/",
            "  - ssh_key (SSH private key)",
            "  - root_password.txt",
            "  - postgresql_password.txt",
            "  - redis_password.txt",
            "  - gitea_admin_password.txt",
            "  - grafana_admin_password.txt",
            "",
        ])

        # Phase results
        report_lines.extend([
            "PHASE RESULTS",
            "-" * 80,
        ])

        phase_names = {
            "phase_1": "Build Templates (Packer)",
            "phase_2": "Provision Infrastructure (Terraform)",
            "phase_3": "Configure Systems (Ansible)",
            "phase_4": "Deploy Operators (Helm)",
            "phase_5": "Deploy Applications (Helm/kubectl)",
            "phase_6": "Validation",
        }

        for phase_key, phase_name in phase_names.items():
            result = phase_results.get(phase_key, {})
            success = result.get("success", False)
            status_str = "SUCCESS" if success else "FAILED"
            report_lines.append(f"  {phase_name}: {status_str}")

        # Next steps
        report_lines.extend([
            "",
            "NEXT STEPS",
            "-" * 80,
            "1. Create Gitea admin account:",
            f"   - Navigate to {gitea_url}",
            "   - First user becomes admin",
            "",
            "2. Configure Gitea:",
            "   - Set up LDAP authentication (LDAP server: 172.27.16.1)",
            "   - Create organizations for departments",
            "",
            "3. Access monitoring:",
            f"   - Grafana: {grafana_url}",
            "   - Default credentials in ./credentials/grafana_admin_password.txt",
            "",
            "4. Set up DNS (optional):",
            f"   - Create DNS record: {self.config.network.gitea_hostname} -> {self.config.network.vip_address}",
            "",
        ])

        # Maintenance commands
        report_lines.extend([
            "MAINTENANCE COMMANDS",
            "-" * 80,
            "Check cluster health:",
            "  kubectl get nodes",
            "  kubectl get pods -A",
            f"  gluster volume status {constants.GLUSTER_VOLUME_NAME}",
            "",
            "View Gitea logs:",
            f"  kubectl logs -f -n {constants.NAMESPACES['gitea']} -l app=gitea",
            "",
            "Scale Gitea:",
            f"  kubectl scale deployment gitea -n {constants.NAMESPACES['gitea']} --replicas=5",
            "",
            "Check storage:",
            "  kubectl get pvc -A",
            f"  gluster volume status {constants.GLUSTER_VOLUME_NAME} detail",
            "",
            "=" * 80,
            "END OF DEPLOYMENT REPORT",
            "=" * 80,
        ])

        # Write report
        report_content = "\n".join(report_lines)
        report_filename = f"deployment-report-{timestamp_filename()}.txt"
        reports_dir = self.project_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / report_filename

        write_file(report_path, report_content)
        self.logger.info(f"Deployment report saved to: {report_path}")

        return str(report_path)

    def display_summary(self, phase_results: Dict[str, Any]) -> None:
        """
        Display deployment summary using Rich formatting.

        Args:
            phase_results: Results from each phase
        """
        vip = self.config.network.vip_address

        # Derive service hostnames from gitea_hostname
        gitea_hostname = self.config.network.gitea_hostname
        if gitea_hostname and '.' in gitea_hostname:
            domain = gitea_hostname.split('.', 1)[1]
            grafana_url = f"http://grafana.{domain}"
            portainer_url = f"http://portainer.{domain}"
        else:
            grafana_url = f"http://{vip}:{constants.GRAFANA_NODEPORT}"
            portainer_url = f"http://{vip}:{constants.PORTAINER_NODEPORT}"

        # Overall status
        all_success = all(
            result.get("success", False)
            for result in phase_results.values()
        )

        # Create summary table
        table = Table(title="Deployment Summary")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="green" if all_success else "yellow")
        table.add_column("Details")

        # Infrastructure
        table.add_row(
            "Infrastructure",
            "Ready" if phase_results.get("phase_2", {}).get("success") else "Failed",
            f"{self.config.get_total_vms()} VMs deployed"
        )

        # Kubernetes
        table.add_row(
            "Kubernetes",
            "Ready" if phase_results.get("phase_3", {}).get("success") else "Failed",
            f"K3s {constants.K3S_VERSION}"
        )

        # Storage
        table.add_row(
            "Storage",
            "Ready" if phase_results.get("phase_3", {}).get("success") else "Failed",
            f"GlusterFS {self.config.get_glusterfs_usable_gb()}GB"
        )

        # Applications
        table.add_row(
            "Applications",
            "Ready" if phase_results.get("phase_5", {}).get("success") else "Failed",
            "Gitea, PostgreSQL, Redis, Monitoring"
        )

        console.print(table)

        # Access URLs
        gitea_url_display = f"http://{gitea_hostname or vip}"

        access_panel = Panel(
            f"[bold]Gitea:[/bold] {gitea_url_display}\n"
            f"[bold]Grafana:[/bold] {grafana_url}\n"
            f"[bold]Portainer:[/bold] {portainer_url}\n"
            f"\n[dim]Credentials saved to ./credentials/[/dim]",
            title="Access URLs",
            border_style="green" if all_success else "yellow",
        )
        console.print(access_panel)

    def display_config_summary(self) -> None:
        """Display configuration summary before deployment."""
        vm_ips = self.config.get_vm_ips()

        # Create infrastructure table
        infra_table = Table(title="Infrastructure Configuration")
        infra_table.add_column("Component", style="cyan")
        infra_table.add_column("Count")
        infra_table.add_column("vCPU")
        infra_table.add_column("RAM")
        infra_table.add_column("Disk")

        infra_table.add_row(
            "HAProxy",
            str(self.config.infrastructure.haproxy.count),
            str(self.config.infrastructure.haproxy.cpu),
            f"{self.config.infrastructure.haproxy.ram_gb}GB",
            f"{self.config.infrastructure.haproxy.disk_gb}GB",
        )

        infra_table.add_row(
            "Masters",
            str(self.config.infrastructure.control_plane.count),
            str(self.config.infrastructure.control_plane.cpu),
            f"{self.config.infrastructure.control_plane.ram_gb}GB",
            f"{self.config.infrastructure.control_plane.disk_gb}GB",
        )

        infra_table.add_row(
            "Workers",
            str(self.config.infrastructure.workers.count),
            str(self.config.infrastructure.workers.cpu),
            f"{self.config.infrastructure.workers.ram_gb}GB",
            f"{self.config.infrastructure.workers.disk_gb}GB",
        )

        infra_table.add_row(
            "GlusterFS",
            str(self.config.infrastructure.glusterfs.count),
            str(self.config.infrastructure.glusterfs.cpu),
            f"{self.config.infrastructure.glusterfs.ram_gb}GB",
            f"{self.config.infrastructure.glusterfs.os_disk_gb}+{self.config.infrastructure.glusterfs.data_disk_gb}GB",
        )

        console.print(infra_table)

        # Totals
        totals_panel = Panel(
            f"[bold]Total VMs:[/bold] {self.config.get_total_vms()}\n"
            f"[bold]Total vCPU:[/bold] {self.config.get_total_vcpu()}\n"
            f"[bold]Total RAM:[/bold] {self.config.get_total_ram_gb()}GB\n"
            f"[bold]Total Storage:[/bold] {self.config.get_total_storage_gb() / 1024:.2f}TB\n"
            f"[bold]App Storage Required:[/bold] {self.config.get_total_app_storage_gb()}GB\n"
            f"[bold]GlusterFS Usable:[/bold] {self.config.get_glusterfs_usable_gb()}GB",
            title="Resource Totals",
            border_style="blue",
        )
        console.print(totals_panel)

        # Network
        network_panel = Panel(
            f"[bold]IP Range:[/bold] {self.config.network.ip_start} - {self.config.network.ip_end}\n"
            f"[bold]Virtual IP:[/bold] {self.config.network.vip_address}\n"
            f"[bold]Gateway:[/bold] {self.config.network.gateway}\n"
            f"[bold]DNS:[/bold] {', '.join(self.config.network.dns_servers)}\n"
            f"[bold]Hostname:[/bold] {self.config.network.gitea_hostname or '(IP-only)'}",
            title="Network Configuration",
            border_style="blue",
        )
        console.print(network_panel)

    def write_credentials_summary(self) -> str:
        """
        Write a credentials summary file.

        Returns:
            Path to credentials summary
        """
        vip = self.config.network.vip_address
        gitea_hostname = self.config.network.gitea_hostname
        gitea_url = f"http://{gitea_hostname or vip}"

        # Derive service hostnames from gitea_hostname
        if gitea_hostname and '.' in gitea_hostname:
            domain = gitea_hostname.split('.', 1)[1]
            grafana_url = f"http://grafana.{domain}"
            portainer_url = f"http://portainer.{domain}"
        else:
            grafana_url = f"http://{vip}:{constants.GRAFANA_NODEPORT}"
            portainer_url = f"http://{vip}:{constants.PORTAINER_NODEPORT}"

        content = f"""# Gitea Infrastructure Credentials
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Access URLs

Gitea Web: {gitea_url}
Grafana: {grafana_url}
Portainer: {portainer_url}
HAProxy Stats: http://{vip}:{constants.HAPROXY_STATS_PORT}/stats

## Kubernetes Access

export KUBECONFIG={self.kubeconfig_dir}/admin.conf
kubectl get nodes

## SSH Access

ssh -i {self.credentials_dir}/ssh_key root@<vm-ip>

## Credential Files

- ssh_key: SSH private key for VM access
- root_password.txt: Root password for VMs
- postgresql_password.txt: PostgreSQL admin password
- redis_password.txt: Redis password
- gitea_admin_password.txt: Gitea admin password
- grafana_admin_password.txt: Grafana admin password

## Default Usernames

- Gitea: Create admin on first login
- Grafana: admin
- Portainer: Create admin on first access
- PostgreSQL: gitea
"""

        summary_path = self.credentials_dir / "README.md"
        write_file(summary_path, content)

        return str(summary_path)
