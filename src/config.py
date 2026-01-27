"""
Configuration management for Gitea Infrastructure Deployment.

Provides dataclasses for configuration and YAML handling.
"""

import os
import sys
import getpass
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

import yaml
from rich.markup import escape as rich_escape

from . import constants
from .utils import get_logger, console, generate_password, ip_range_to_list


@dataclass
class VSphereConfig:
    """vSphere connection and resource configuration."""
    vcenter_server: str = ""
    username: str = ""
    password: str = ""
    datacenter: str = ""
    cluster: str = ""
    resource_pool: str = ""
    datastore: str = ""
    template_folder: str = ""
    vm_folder: str = ""

    # Hardcoded values (included for reference but fixed)
    network_port_group: str = constants.VSPHERE_NETWORK_PORT_GROUP
    iso_datastore: str = constants.VSPHERE_ISO_DATASTORE
    iso_path: str = constants.VSPHERE_ISO_PATH


@dataclass
class NetworkConfig:
    """Network configuration for VMs."""
    # Default to 10.209.0.x network (NI_infra distributed port group)
    ip_start: str = "10.209.0.20"
    ip_end: str = "10.209.0.40"
    vip_address: str = "10.209.0.100"
    subnet_mask: str = "255.255.255.0"
    gateway: str = "10.209.0.1"
    dns_servers: List[str] = field(default_factory=lambda: ["8.8.8.8", "1.1.1.1"])
    gitea_hostname: str = ""

    # Hardcoded value
    domain: str = ""  # No domain suffix


@dataclass
class VMSizing:
    """VM resource sizing configuration."""
    count: int = 0
    cpu: int = 0
    ram_gb: int = 0
    disk_gb: int = 0


@dataclass
class GlusterFSSizing(VMSizing):
    """GlusterFS VM sizing with additional data disk."""
    os_disk_gb: int = 0
    data_disk_gb: int = 0


@dataclass
class BackupConfig:
    """Backup VM configuration."""
    enabled: bool = constants.DEFAULT_BACKUP_CONFIG["enabled"]
    count: int = constants.DEFAULT_BACKUP_CONFIG["count"]
    cpu: int = constants.DEFAULT_BACKUP_CONFIG["cpu"]
    ram_gb: int = constants.DEFAULT_BACKUP_CONFIG["ram_gb"]
    disk_gb: int = constants.DEFAULT_BACKUP_CONFIG["disk_gb"]
    retention_days: int = constants.DEFAULT_BACKUP_CONFIG["retention_days"]
    schedule: str = constants.DEFAULT_BACKUP_CONFIG["schedule"]
    datastore: str = ""  # Separate datastore for backup VM (empty = use default)


@dataclass
class InfrastructureConfig:
    """Infrastructure sizing configuration."""
    haproxy: VMSizing = field(default_factory=lambda: VMSizing(
        count=constants.DEFAULT_HAPROXY_CONFIG["count"],
        cpu=constants.DEFAULT_HAPROXY_CONFIG["cpu"],
        ram_gb=constants.DEFAULT_HAPROXY_CONFIG["ram_gb"],
        disk_gb=constants.DEFAULT_HAPROXY_CONFIG["disk_gb"],
    ))
    control_plane: VMSizing = field(default_factory=lambda: VMSizing(
        count=constants.DEFAULT_CONTROL_PLANE_CONFIG["count"],
        cpu=constants.DEFAULT_CONTROL_PLANE_CONFIG["cpu"],
        ram_gb=constants.DEFAULT_CONTROL_PLANE_CONFIG["ram_gb"],
        disk_gb=constants.DEFAULT_CONTROL_PLANE_CONFIG["disk_gb"],
    ))
    workers: VMSizing = field(default_factory=lambda: VMSizing(
        count=constants.DEFAULT_WORKERS_CONFIG["count"],
        cpu=constants.DEFAULT_WORKERS_CONFIG["cpu"],
        ram_gb=constants.DEFAULT_WORKERS_CONFIG["ram_gb"],
        disk_gb=constants.DEFAULT_WORKERS_CONFIG["disk_gb"],
    ))
    glusterfs: GlusterFSSizing = field(default_factory=lambda: GlusterFSSizing(
        count=constants.DEFAULT_GLUSTERFS_CONFIG["count"],
        cpu=constants.DEFAULT_GLUSTERFS_CONFIG["cpu"],
        ram_gb=constants.DEFAULT_GLUSTERFS_CONFIG["ram_gb"],
        os_disk_gb=constants.DEFAULT_GLUSTERFS_CONFIG["os_disk_gb"],
        data_disk_gb=constants.DEFAULT_GLUSTERFS_CONFIG["data_disk_gb"],
    ))
    backup: BackupConfig = field(default_factory=BackupConfig)


@dataclass
class GiteaAppConfig:
    """Gitea application configuration."""
    replicas: int = constants.DEFAULT_GITEA_CONFIG["replicas"]
    repository_storage_gb: int = constants.DEFAULT_GITEA_CONFIG["repository_storage_gb"]
    attachment_storage_gb: int = constants.DEFAULT_GITEA_CONFIG["attachment_storage_gb"]


@dataclass
class PostgreSQLConfig:
    """PostgreSQL configuration."""
    instances: int = constants.DEFAULT_POSTGRESQL_CONFIG["instances"]
    storage_per_instance_gb: int = constants.DEFAULT_POSTGRESQL_CONFIG["storage_per_instance_gb"]


@dataclass
class RedisConfig:
    """Redis configuration."""
    replicas: int = constants.DEFAULT_REDIS_CONFIG["replicas"]
    storage_per_replica_gb: int = constants.DEFAULT_REDIS_CONFIG["storage_per_replica_gb"]


@dataclass
class PrometheusConfig:
    """Prometheus configuration."""
    replicas: int = constants.DEFAULT_PROMETHEUS_CONFIG["replicas"]
    storage_per_replica_gb: int = constants.DEFAULT_PROMETHEUS_CONFIG["storage_per_replica_gb"]
    retention_days: int = constants.DEFAULT_PROMETHEUS_CONFIG["retention_days"]


@dataclass
class GrafanaConfig:
    """Grafana configuration."""
    replicas: int = constants.DEFAULT_GRAFANA_CONFIG["replicas"]
    storage_per_replica_gb: int = constants.DEFAULT_GRAFANA_CONFIG["storage_per_replica_gb"]


@dataclass
class PortainerConfig:
    """Portainer configuration."""
    replicas: int = constants.DEFAULT_PORTAINER_CONFIG["replicas"]
    storage_gb: int = constants.DEFAULT_PORTAINER_CONFIG["storage_gb"]


@dataclass
class GiteaRunnerConfig:
    """Gitea Act Runner configuration."""
    enabled: bool = True
    replicas: int = constants.DEFAULT_GITEA_RUNNER_CONFIG["replicas"]
    runner_labels: str = constants.DEFAULT_GITEA_RUNNER_CONFIG["runner_labels"]


@dataclass
class ApplicationsConfig:
    """Application sizing configuration."""
    gitea: GiteaAppConfig = field(default_factory=GiteaAppConfig)
    postgresql: PostgreSQLConfig = field(default_factory=PostgreSQLConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    grafana: GrafanaConfig = field(default_factory=GrafanaConfig)
    portainer: PortainerConfig = field(default_factory=PortainerConfig)
    gitea_runner: GiteaRunnerConfig = field(default_factory=GiteaRunnerConfig)


@dataclass
class DeploymentPhases:
    """Deployment phases configuration."""
    build_templates: bool = True
    provision_vms: bool = True
    configure_infrastructure: bool = True
    deploy_operators: bool = True
    deploy_applications: bool = True
    run_validation: bool = True


@dataclass
class DeploymentOptions:
    """Deployment options configuration."""
    phases: DeploymentPhases = field(default_factory=DeploymentPhases)
    interactive_mode: bool = True
    cleanup_on_failure: bool = False
    parallel_vm_creation: bool = True
    max_parallel_tasks: int = 5
    # Preseed server configuration (VM in the same network to serve preseed.cfg)
    preseed_server_ip: str = "10.209.0.19"
    preseed_server_port: int = 8100
    preseed_server_user: str = "root"
    preseed_server_ssh_key: str = ""  # Empty = use default ~/.ssh/id_rsa


@dataclass
class Metadata:
    """Configuration metadata."""
    config_version: str = "1.0"
    created_at: str = ""
    created_by: str = ""
    environment: str = "production"
    description: str = "Gitea Production Infrastructure"


@dataclass
class DeploymentConfig:
    """Complete deployment configuration."""
    metadata: Metadata = field(default_factory=Metadata)
    vsphere: VSphereConfig = field(default_factory=VSphereConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    infrastructure: InfrastructureConfig = field(default_factory=InfrastructureConfig)
    applications: ApplicationsConfig = field(default_factory=ApplicationsConfig)
    deployment: DeploymentOptions = field(default_factory=DeploymentOptions)

    # Generated credentials (not in YAML, generated at runtime)
    ssh_private_key: str = field(default="", repr=False)
    ssh_public_key: str = field(default="", repr=False)
    generated_passwords: Dict[str, str] = field(default_factory=dict, repr=False)

    def get_vm_ips(self) -> Dict[str, List[str]]:
        """Get IP addresses for all VMs based on configuration."""
        all_ips = ip_range_to_list(self.network.ip_start, self.network.ip_end)

        idx = 0
        result = {}

        # HAProxy
        result["haproxy"] = all_ips[idx:idx + self.infrastructure.haproxy.count]
        idx += self.infrastructure.haproxy.count

        # Masters
        result["masters"] = all_ips[idx:idx + self.infrastructure.control_plane.count]
        idx += self.infrastructure.control_plane.count

        # Workers
        result["workers"] = all_ips[idx:idx + self.infrastructure.workers.count]
        idx += self.infrastructure.workers.count

        # GlusterFS
        result["glusterfs"] = all_ips[idx:idx + self.infrastructure.glusterfs.count]
        idx += self.infrastructure.glusterfs.count

        # Backup (if enabled)
        if self.infrastructure.backup.enabled:
            result["backup"] = all_ips[idx:idx + self.infrastructure.backup.count]
        else:
            result["backup"] = []

        return result

    def get_total_vcpu(self) -> int:
        """Calculate total vCPU requirement."""
        total = (
            self.infrastructure.haproxy.count * self.infrastructure.haproxy.cpu +
            self.infrastructure.control_plane.count * self.infrastructure.control_plane.cpu +
            self.infrastructure.workers.count * self.infrastructure.workers.cpu +
            self.infrastructure.glusterfs.count * self.infrastructure.glusterfs.cpu
        )
        if self.infrastructure.backup.enabled:
            total += self.infrastructure.backup.count * self.infrastructure.backup.cpu
        return total

    def get_total_ram_gb(self) -> int:
        """Calculate total RAM requirement in GB."""
        total = (
            self.infrastructure.haproxy.count * self.infrastructure.haproxy.ram_gb +
            self.infrastructure.control_plane.count * self.infrastructure.control_plane.ram_gb +
            self.infrastructure.workers.count * self.infrastructure.workers.ram_gb +
            self.infrastructure.glusterfs.count * self.infrastructure.glusterfs.ram_gb
        )
        if self.infrastructure.backup.enabled:
            total += self.infrastructure.backup.count * self.infrastructure.backup.ram_gb
        return total

    def get_total_storage_gb(self) -> int:
        """Calculate total storage requirement in GB."""
        total = (
            self.infrastructure.haproxy.count * self.infrastructure.haproxy.disk_gb +
            self.infrastructure.control_plane.count * self.infrastructure.control_plane.disk_gb +
            self.infrastructure.workers.count * self.infrastructure.workers.disk_gb +
            self.infrastructure.glusterfs.count * (
                self.infrastructure.glusterfs.os_disk_gb +
                self.infrastructure.glusterfs.data_disk_gb
            )
        )
        if self.infrastructure.backup.enabled:
            total += self.infrastructure.backup.count * self.infrastructure.backup.disk_gb
        return total

    def get_total_app_storage_gb(self) -> int:
        """Calculate total application storage requirement in GB."""
        return (
            self.applications.gitea.repository_storage_gb +
            self.applications.gitea.attachment_storage_gb +
            self.applications.postgresql.instances * self.applications.postgresql.storage_per_instance_gb +
            self.applications.redis.replicas * self.applications.redis.storage_per_replica_gb +
            self.applications.prometheus.replicas * self.applications.prometheus.storage_per_replica_gb +
            self.applications.grafana.replicas * self.applications.grafana.storage_per_replica_gb +
            self.applications.portainer.storage_gb
        )

    def get_glusterfs_usable_gb(self) -> int:
        """Calculate usable GlusterFS storage (after 3-way replication)."""
        total_data = self.infrastructure.glusterfs.count * self.infrastructure.glusterfs.data_disk_gb
        # With replica 3, usable = total / 3
        return total_data // 3

    def get_total_vms(self) -> int:
        """Get total number of VMs."""
        total = (
            self.infrastructure.haproxy.count +
            self.infrastructure.control_plane.count +
            self.infrastructure.workers.count +
            self.infrastructure.glusterfs.count
        )
        if self.infrastructure.backup.enabled:
            total += self.infrastructure.backup.count
        return total


class ConfigManager:
    """Manager for configuration file operations."""

    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize ConfigManager.

        Args:
            config_dir: Directory for configuration files
        """
        self.logger = get_logger()
        self.config_dir = Path(config_dir) if config_dir else Path.cwd() / "configs"
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _dataclass_to_dict(self, obj: Any) -> Any:
        """Convert dataclass to dictionary, handling nested dataclasses."""
        if hasattr(obj, "__dataclass_fields__"):
            result = {}
            for key, value in asdict(obj).items():
                # Skip runtime-generated fields
                if key in ("ssh_private_key", "ssh_public_key", "generated_passwords"):
                    continue
                result[key] = self._dataclass_to_dict(value)
            return result
        elif isinstance(obj, list):
            return [self._dataclass_to_dict(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: self._dataclass_to_dict(v) for k, v in obj.items()}
        else:
            return obj

    def _dict_to_dataclass(self, data: Dict, cls: type) -> Any:
        """Convert dictionary to dataclass, handling nested dataclasses."""
        if not hasattr(cls, "__dataclass_fields__"):
            return data

        field_types = {f.name: f.type for f in cls.__dataclass_fields__.values()}
        kwargs = {}

        for key, value in data.items():
            if key in field_types:
                field_type = field_types[key]
                # Handle Optional types
                if hasattr(field_type, "__origin__") and field_type.__origin__ is type(None):
                    field_type = field_type.__args__[0]

                if hasattr(field_type, "__dataclass_fields__"):
                    kwargs[key] = self._dict_to_dataclass(value, field_type)
                else:
                    kwargs[key] = value

        return cls(**kwargs)

    def load(self, config_path: str) -> DeploymentConfig:
        """
        Load configuration from YAML file.

        Args:
            config_path: Path to configuration file

        Returns:
            DeploymentConfig instance
        """
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path) as f:
            data = yaml.safe_load(f)

        # Convert nested dictionaries to dataclasses
        config = DeploymentConfig()

        if "metadata" in data:
            config.metadata = self._dict_to_dataclass(data["metadata"], Metadata)

        if "vsphere" in data:
            config.vsphere = self._dict_to_dataclass(data["vsphere"], VSphereConfig)

        if "network" in data:
            config.network = self._dict_to_dataclass(data["network"], NetworkConfig)

        if "infrastructure" in data:
            infra_data = data["infrastructure"]
            config.infrastructure = InfrastructureConfig()

            if "haproxy" in infra_data:
                config.infrastructure.haproxy = self._dict_to_dataclass(
                    infra_data["haproxy"], VMSizing
                )
            if "control_plane" in infra_data:
                config.infrastructure.control_plane = self._dict_to_dataclass(
                    infra_data["control_plane"], VMSizing
                )
            if "workers" in infra_data:
                config.infrastructure.workers = self._dict_to_dataclass(
                    infra_data["workers"], VMSizing
                )
            if "glusterfs" in infra_data:
                config.infrastructure.glusterfs = self._dict_to_dataclass(
                    infra_data["glusterfs"], GlusterFSSizing
                )
            if "backup" in infra_data:
                config.infrastructure.backup = self._dict_to_dataclass(
                    infra_data["backup"], BackupConfig
                )

        if "applications" in data:
            apps_data = data["applications"]
            config.applications = ApplicationsConfig()

            if "gitea" in apps_data:
                config.applications.gitea = self._dict_to_dataclass(
                    apps_data["gitea"], GiteaAppConfig
                )
            if "postgresql" in apps_data:
                config.applications.postgresql = self._dict_to_dataclass(
                    apps_data["postgresql"], PostgreSQLConfig
                )
            if "redis" in apps_data:
                config.applications.redis = self._dict_to_dataclass(
                    apps_data["redis"], RedisConfig
                )
            if "prometheus" in apps_data:
                config.applications.prometheus = self._dict_to_dataclass(
                    apps_data["prometheus"], PrometheusConfig
                )
            if "grafana" in apps_data:
                config.applications.grafana = self._dict_to_dataclass(
                    apps_data["grafana"], GrafanaConfig
                )
            if "portainer" in apps_data:
                config.applications.portainer = self._dict_to_dataclass(
                    apps_data["portainer"], PortainerConfig
                )
            if "gitea_runner" in apps_data:
                config.applications.gitea_runner = self._dict_to_dataclass(
                    apps_data["gitea_runner"], GiteaRunnerConfig
                )

        if "deployment" in data:
            deploy_data = data["deployment"]
            config.deployment = DeploymentOptions()

            if "phases" in deploy_data:
                config.deployment.phases = self._dict_to_dataclass(
                    deploy_data["phases"], DeploymentPhases
                )
            if "interactive_mode" in deploy_data:
                config.deployment.interactive_mode = deploy_data["interactive_mode"]
            if "cleanup_on_failure" in deploy_data:
                config.deployment.cleanup_on_failure = deploy_data["cleanup_on_failure"]
            if "parallel_vm_creation" in deploy_data:
                config.deployment.parallel_vm_creation = deploy_data["parallel_vm_creation"]
            if "max_parallel_tasks" in deploy_data:
                config.deployment.max_parallel_tasks = deploy_data["max_parallel_tasks"]
            if "preseed_server_ip" in deploy_data:
                config.deployment.preseed_server_ip = deploy_data["preseed_server_ip"]
            if "preseed_server_port" in deploy_data:
                config.deployment.preseed_server_port = deploy_data["preseed_server_port"]
            if "preseed_server_user" in deploy_data:
                config.deployment.preseed_server_user = deploy_data["preseed_server_user"]

        # Check for password in environment variable
        if not config.vsphere.password:
            config.vsphere.password = os.environ.get("VSPHERE_PASSWORD", "")

        self.logger.info(f"Loaded configuration from {config_path}")
        return config

    def save(self, config: DeploymentConfig, config_path: str) -> str:
        """
        Save configuration to YAML file.

        Args:
            config: DeploymentConfig instance
            config_path: Path to save configuration

        Returns:
            Path to saved file
        """
        config_path = Path(config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dictionary
        data = self._dataclass_to_dict(config)

        # Add comments for hardcoded values
        yaml_content = self._generate_yaml_with_comments(data)

        config_path.write_text(yaml_content)

        self.logger.info(f"Saved configuration to {config_path}")
        return str(config_path)

    def _generate_yaml_with_comments(self, data: Dict) -> str:
        """Generate YAML content with comments for hardcoded values."""
        lines = [
            "#" + "=" * 78,
            "# GITEA PRODUCTION INFRASTRUCTURE CONFIGURATION",
            "#" + "=" * 78,
            "",
        ]

        # Metadata section
        lines.extend([
            "metadata:",
            f"  config_version: \"{data['metadata']['config_version']}\"",
            f"  created_at: \"{data['metadata']['created_at']}\"",
            f"  created_by: \"{data['metadata']['created_by']}\"",
            f"  environment: \"{data['metadata']['environment']}\"",
            f"  description: \"{data['metadata']['description']}\"",
            "",
        ])

        # vSphere section
        lines.extend([
            "#" + "-" * 78,
            "# VSPHERE ENVIRONMENT",
            "#" + "-" * 78,
            "vsphere:",
            f"  vcenter_server: \"{data['vsphere']['vcenter_server']}\"",
            f"  username: \"{data['vsphere']['username']}\"",
            f"  password: \"\"  # Use VSPHERE_PASSWORD environment variable",
            f"  datacenter: \"{data['vsphere']['datacenter']}\"",
            f"  cluster: \"{data['vsphere']['cluster']}\"",
            f"  resource_pool: \"{data['vsphere']['resource_pool']}\"",
            f"  datastore: \"{data['vsphere']['datastore']}\"",
            f"  template_folder: \"{data['vsphere']['template_folder']}\"",
            f"  vm_folder: \"{data['vsphere']['vm_folder']}\"",
            "",
            "  # HARDCODED - DO NOT MODIFY",
            f"  network_port_group: \"{constants.VSPHERE_NETWORK_PORT_GROUP}\"",
            f"  iso_datastore: \"{constants.VSPHERE_ISO_DATASTORE}\"",
            f"  iso_path: \"{constants.VSPHERE_ISO_PATH}\"",
            "",
        ])

        # Network section
        lines.extend([
            "#" + "-" * 78,
            "# NETWORK CONFIGURATION",
            "#" + "-" * 78,
            "network:",
            f"  ip_start: \"{data['network']['ip_start']}\"",
            f"  ip_end: \"{data['network']['ip_end']}\"",
            f"  vip_address: \"{data['network']['vip_address']}\"",
            f"  subnet_mask: \"{data['network']['subnet_mask']}\"",
            f"  gateway: \"{data['network']['gateway']}\"",
            "  dns_servers:",
        ])
        for dns in data['network']['dns_servers']:
            lines.append(f"    - \"{dns}\"")
        lines.extend([
            f"  gitea_hostname: \"{data['network']['gitea_hostname']}\"",
            "",
            "  # HARDCODED - DO NOT MODIFY",
            f"  domain: \"\"  # No domain suffix",
            "",
        ])

        # Infrastructure section
        lines.extend([
            "#" + "-" * 78,
            "# INFRASTRUCTURE SIZING",
            "#" + "-" * 78,
            "infrastructure:",
            "  haproxy:",
            f"    count: {data['infrastructure']['haproxy']['count']}",
            f"    cpu: {data['infrastructure']['haproxy']['cpu']}",
            f"    ram_gb: {data['infrastructure']['haproxy']['ram_gb']}",
            f"    disk_gb: {data['infrastructure']['haproxy']['disk_gb']}",
            "",
            "  control_plane:",
            f"    count: {data['infrastructure']['control_plane']['count']}",
            f"    cpu: {data['infrastructure']['control_plane']['cpu']}",
            f"    ram_gb: {data['infrastructure']['control_plane']['ram_gb']}",
            f"    disk_gb: {data['infrastructure']['control_plane']['disk_gb']}",
            "",
            "  workers:",
            f"    count: {data['infrastructure']['workers']['count']}",
            f"    cpu: {data['infrastructure']['workers']['cpu']}",
            f"    ram_gb: {data['infrastructure']['workers']['ram_gb']}",
            f"    disk_gb: {data['infrastructure']['workers']['disk_gb']}",
            "",
            "  glusterfs:",
            f"    count: {data['infrastructure']['glusterfs']['count']}",
            f"    cpu: {data['infrastructure']['glusterfs']['cpu']}",
            f"    ram_gb: {data['infrastructure']['glusterfs']['ram_gb']}",
            f"    os_disk_gb: {data['infrastructure']['glusterfs']['os_disk_gb']}",
            f"    data_disk_gb: {data['infrastructure']['glusterfs']['data_disk_gb']}",
            "",
            "  backup:",
            f"    enabled: {str(data['infrastructure']['backup']['enabled']).lower()}",
            f"    count: {data['infrastructure']['backup']['count']}",
            f"    cpu: {data['infrastructure']['backup']['cpu']}",
            f"    ram_gb: {data['infrastructure']['backup']['ram_gb']}",
            f"    disk_gb: {data['infrastructure']['backup']['disk_gb']}",
            f"    retention_days: {data['infrastructure']['backup']['retention_days']}",
            f"    schedule: \"{data['infrastructure']['backup']['schedule']}\"",
            f"    datastore: \"{data['infrastructure']['backup'].get('datastore', '')}\"  # Separate datastore for backup (empty = use default)",
            "",
        ])

        # Applications section
        lines.extend([
            "#" + "-" * 78,
            "# APPLICATION SIZING",
            "#" + "-" * 78,
            "applications:",
            "  gitea:",
            f"    replicas: {data['applications']['gitea']['replicas']}",
            f"    repository_storage_gb: {data['applications']['gitea']['repository_storage_gb']}",
            f"    attachment_storage_gb: {data['applications']['gitea']['attachment_storage_gb']}",
            "",
            "  postgresql:",
            f"    instances: {data['applications']['postgresql']['instances']}",
            f"    storage_per_instance_gb: {data['applications']['postgresql']['storage_per_instance_gb']}",
            "",
            "  redis:",
            f"    replicas: {data['applications']['redis']['replicas']}",
            f"    storage_per_replica_gb: {data['applications']['redis']['storage_per_replica_gb']}",
            "",
            "  prometheus:",
            f"    replicas: {data['applications']['prometheus']['replicas']}",
            f"    storage_per_replica_gb: {data['applications']['prometheus']['storage_per_replica_gb']}",
            f"    retention_days: {data['applications']['prometheus']['retention_days']}",
            "",
            "  grafana:",
            f"    replicas: {data['applications']['grafana']['replicas']}",
            f"    storage_per_replica_gb: {data['applications']['grafana']['storage_per_replica_gb']}",
            "",
            "  portainer:",
            f"    replicas: {data['applications']['portainer']['replicas']}",
            f"    storage_gb: {data['applications']['portainer']['storage_gb']}",
            "",
        ])

        # Deployment section
        lines.extend([
            "#" + "-" * 78,
            "# DEPLOYMENT OPTIONS",
            "#" + "-" * 78,
            "deployment:",
            "  phases:",
            f"    build_templates: {str(data['deployment']['phases']['build_templates']).lower()}",
            f"    provision_vms: {str(data['deployment']['phases']['provision_vms']).lower()}",
            f"    configure_infrastructure: {str(data['deployment']['phases']['configure_infrastructure']).lower()}",
            f"    deploy_operators: {str(data['deployment']['phases']['deploy_operators']).lower()}",
            f"    deploy_applications: {str(data['deployment']['phases']['deploy_applications']).lower()}",
            f"    run_validation: {str(data['deployment']['phases']['run_validation']).lower()}",
            "",
            f"  interactive_mode: {str(data['deployment']['interactive_mode']).lower()}",
            f"  cleanup_on_failure: {str(data['deployment']['cleanup_on_failure']).lower()}",
            f"  parallel_vm_creation: {str(data['deployment']['parallel_vm_creation']).lower()}",
            f"  max_parallel_tasks: {data['deployment']['max_parallel_tasks']}",
            f"  preseed_server_ip: \"{data['deployment'].get('preseed_server_ip', '10.209.0.19')}\"",
            f"  preseed_server_port: {data['deployment'].get('preseed_server_port', 8100)}",
            f"  preseed_server_user: \"{data['deployment'].get('preseed_server_user', 'root')}\"",
            "",
        ])

        return "\n".join(lines)

    def list_configs(self) -> List[Dict[str, Any]]:
        """
        List available configuration files.

        Returns:
            List of config file info dictionaries
        """
        configs = []

        for config_file in self.config_dir.glob("*.yaml"):
            try:
                config = self.load(str(config_file))
                configs.append({
                    "name": config_file.name,
                    "path": str(config_file),
                    "created_at": config.metadata.created_at,
                    "environment": config.metadata.environment,
                    "total_vms": config.get_total_vms(),
                    "total_vcpu": config.get_total_vcpu(),
                    "total_ram_gb": config.get_total_ram_gb(),
                })
            except Exception as e:
                self.logger.warning(f"Could not read {config_file}: {e}")
                configs.append({
                    "name": config_file.name,
                    "path": str(config_file),
                    "error": str(e),
                })

        return configs

    def create_interactive(self, discovery=None) -> DeploymentConfig:
        """
        Create configuration interactively with user prompts.

        Args:
            discovery: Optional VSphereDiscovery instance for resource discovery

        Returns:
            DeploymentConfig instance
        """
        config = DeploymentConfig()
        config.metadata.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config.metadata.created_by = getpass.getuser()

        console.print("\n")
        console.print("=" * 60, style="bold blue")
        console.print("[bold blue]  GITEA INFRASTRUCTURE CONFIGURATION WIZARD  [/bold blue]")
        console.print("=" * 60, style="bold blue")
        console.print("\n[dim]This wizard will guide you through configuring the 10-VM[/dim]")
        console.print("[dim]Gitea production infrastructure deployment.[/dim]\n")

        # ========================================
        # STEP 1: VSPHERE CONNECTION
        # ========================================
        console.print("=" * 60, style="cyan")
        console.print("[bold cyan]STEP 1: VSPHERE CONNECTION[/bold cyan]")
        console.print("=" * 60, style="cyan")
        console.print("\n[dim]Connect to vCenter to discover available resources.[/dim]\n")
        config.vsphere.vcenter_server = console.input(
            "vCenter server address [vlab.dual.edu]: "
        ).strip() or "vlab.dual.edu"

        config.vsphere.username = console.input(
            f"Username [{getpass.getuser()}]: "
        ).strip() or getpass.getuser()

        config.vsphere.password = getpass.getpass("Password: ")

        # Track discovery connection state
        discovery_connected = False

        # If discovery is available, use it
        if discovery:
            try:
                discovery.connect(
                    config.vsphere.vcenter_server,
                    config.vsphere.username,
                    config.vsphere.password,
                )
                discovery_connected = True

                # Datacenter selection
                console.print("\n[bold]Available Datacenters:[/bold]")
                datacenters = discovery.list_datacenters()
                for i, dc in enumerate(datacenters, 1):
                    console.print(f"  {i}. {dc['name']}")
                dc_choice = int(console.input("Select datacenter [1]: ") or "1")
                config.vsphere.datacenter = datacenters[dc_choice - 1]["name"]

                # Cluster selection
                console.print("\n[bold]Available Clusters:[/bold]")
                clusters = discovery.list_clusters(config.vsphere.datacenter)
                for i, cl in enumerate(clusters, 1):
                    console.print(f"  {i}. {cl['name']} (CPU: {cl['cpu_available']}, RAM: {cl['memory_available_gb']:.1f}GB)")
                cl_choice = int(console.input("Select cluster [1]: ") or "1")
                config.vsphere.cluster = clusters[cl_choice - 1]["name"]

                # Datastore folder selection (two-step process)
                console.print("\n[bold]Datastore Folders:[/bold]")
                ds_folders = discovery.list_datastore_folders(config.vsphere.datacenter)

                if ds_folders and len(ds_folders) > 1:
                    # Show folder options
                    for i, folder in enumerate(ds_folders, 1):
                        ds_count = folder['datastore_count']
                        sub_count = folder['subfolder_count']
                        info_parts = []
                        if ds_count > 0:
                            info_parts.append(f"{ds_count} datastores")
                        if sub_count > 0:
                            info_parts.append(f"{sub_count} subfolders")
                        info = f" ({', '.join(info_parts)})" if info_parts else ""
                        console.print(f"  {i}. {folder['name']}{info}")

                    folder_choice = int(console.input("Select datastore folder [1]: ") or "1")
                    selected_folder = ds_folders[folder_choice - 1]

                    # Now list datastores in that folder
                    console.print(f"\n[bold]Datastores in '{selected_folder['name']}':[/bold]")
                    datastores = discovery.list_datastores_in_folder(
                        config.vsphere.datacenter,
                        selected_folder['path']
                    )

                    if not datastores:
                        console.print("[yellow]No datastores in this folder, showing all datastores[/yellow]")
                        datastores = discovery.list_datastores(config.vsphere.datacenter)
                else:
                    # No folder structure, show all datastores
                    console.print("\n[bold]Available Datastores:[/bold]")
                    datastores = discovery.list_datastores(config.vsphere.datacenter)

                for i, ds in enumerate(datastores, 1):
                    console.print(f"  {i}. {ds['name']} (Free: {ds['free_gb']:.1f}GB)")
                ds_choice = int(console.input("Select datastore [1]: ") or "1")
                config.vsphere.datastore = datastores[ds_choice - 1]["name"]

                # Backup datastore selection (optional, for backup VM)
                console.print("\n[bold]Backup VM Datastore:[/bold]")
                console.print("[dim]Select a different datastore for backup VM (recommended for redundancy)[/dim]")
                for i, ds in enumerate(datastores, 1):
                    marker = " [current]" if ds['name'] == config.vsphere.datastore else ""
                    console.print(f"  {i}. {ds['name']} (Free: {ds['free_gb']:.1f}GB){marker}")
                console.print(f"  0. Same as main datastore ({config.vsphere.datastore})")
                backup_ds_choice = console.input("Select backup datastore [0]: ").strip()
                if backup_ds_choice and backup_ds_choice != "0":
                    config.infrastructure.backup.datastore = datastores[int(backup_ds_choice) - 1]["name"]
                else:
                    config.infrastructure.backup.datastore = ""  # Use same as main

                # Resource pool selection
                console.print("\n[bold]Resource Pools:[/bold]")
                resource_pools = discovery.list_resource_pools(
                    config.vsphere.datacenter,
                    config.vsphere.cluster
                )
                for i, rp in enumerate(resource_pools, 1):
                    cpu_res = rp.get('cpu_reservation', 0)
                    mem_res = rp.get('memory_reservation_mb', 0)
                    res_info = ""
                    if cpu_res > 0 or mem_res > 0:
                        res_info = f" (CPU: {cpu_res}MHz, RAM: {mem_res}MB reserved)"
                    console.print(f"  {i}. {rp['path']}{res_info}")

                rp_choice = int(console.input("Select resource pool [1]: ") or "1")
                config.vsphere.resource_pool = resource_pools[rp_choice - 1]["path"]

            except Exception as e:
                console.print(f"[yellow]Warning: Could not connect to vSphere: {e}[/yellow]")
                console.print("Continuing with manual entry...\n")
                if discovery_connected:
                    try:
                        discovery.disconnect()
                    except Exception:
                        pass
                    discovery_connected = False

        # Manual entry if discovery not used or failed
        if not config.vsphere.datacenter:
            config.vsphere.datacenter = console.input("Datacenter name: ").strip()
        if not config.vsphere.cluster:
            config.vsphere.cluster = console.input("Cluster name: ").strip()
        if not config.vsphere.datastore:
            config.vsphere.datastore = console.input("Datastore name: ").strip()

        # Backup datastore (manual entry if not set via discovery)
        if not config.infrastructure.backup.datastore:
            backup_ds = console.input(
                f"Backup VM datastore (Enter for same as main [{config.vsphere.datastore}]): "
            ).strip()
            config.infrastructure.backup.datastore = backup_ds  # Empty = use main

        # Resource pool - only ask if not already set via discovery
        if not config.vsphere.resource_pool:
            default_rp = f"{config.vsphere.cluster}/Resources/Gitea-Production"
            config.vsphere.resource_pool = console.input(
                f"Resource pool [{rich_escape(default_rp)}]: ", markup=False
            ).strip() or default_rp

        # Disconnect discovery if still connected
        if discovery_connected:
            try:
                discovery.disconnect()
            except Exception:
                pass

        # Template and VM folder paths
        default_template_folder = f"/{config.vsphere.datacenter}/vm/GiteaInfra/Templates"
        config.vsphere.template_folder = console.input(
            f"Template folder [{rich_escape(default_template_folder)}]: ", markup=False
        ).strip() or default_template_folder

        default_vm_folder = f"/{config.vsphere.datacenter}/vm/GiteaInfra/Infra"
        config.vsphere.vm_folder = console.input(
            f"VM folder [{rich_escape(default_vm_folder)}]: ", markup=False
        ).strip() or default_vm_folder

        # ========================================
        # STEP 2: VSPHERE RESOURCES
        # ========================================
        console.print("\n")
        console.print("=" * 60, style="cyan")
        console.print("[bold cyan]STEP 2: VSPHERE RESOURCES (COMPLETE)[/bold cyan]")
        console.print("=" * 60, style="cyan")
        console.print(f"\n[green]Datacenter:[/green] {config.vsphere.datacenter}")
        console.print(f"[green]Cluster:[/green] {config.vsphere.cluster}")
        console.print(f"[green]Datastore:[/green] {config.vsphere.datastore}")
        console.print(f"[green]Resource Pool:[/green] {config.vsphere.resource_pool}")

        # ========================================
        # STEP 3: NETWORK CONFIGURATION
        # ========================================
        console.print("\n")
        console.print("=" * 60, style="cyan")
        console.print("[bold cyan]STEP 3: NETWORK CONFIGURATION[/bold cyan]")
        console.print("=" * 60, style="cyan")
        console.print("\n[dim]Configure IP addresses for the VMs and virtual IP for HA.[/dim]")
        console.print("[dim]You need 10 consecutive IPs for VMs plus 1 VIP address.[/dim]\n")
        config.network.ip_start = console.input(
            "Starting IP [10.209.0.10]: "
        ).strip() or "10.209.0.10"

        config.network.ip_end = console.input(
            "Ending IP [10.209.0.19]: "
        ).strip() or "10.209.0.19"

        config.network.vip_address = console.input(
            "Virtual IP (VIP) [10.209.0.20]: "
        ).strip() or "10.209.0.20"

        config.network.gateway = console.input(
            "Gateway [10.209.0.1]: "
        ).strip() or "10.209.0.1"

        dns_input = console.input(
            "DNS servers (comma-separated) [8.8.8.8,1.1.1.1]: "
        ).strip() or "8.8.8.8,1.1.1.1"
        config.network.dns_servers = [d.strip() for d in dns_input.split(",")]

        config.network.gitea_hostname = console.input(
            "Gitea hostname (optional, leave empty for IP-only) [gitea.dual]: "
        ).strip() or "gitea.dual"

        # ========================================
        # INFRASTRUCTURE SIZING
        # ========================================
        console.print("\n")
        console.print("=" * 60, style="cyan")
        console.print("[bold cyan]STEP 4: INFRASTRUCTURE SIZING[/bold cyan]")
        console.print("=" * 60, style="cyan")
        console.print("\n[dim]Configure VM resources for the 10-node infrastructure.[/dim]")
        console.print("[dim]This includes HAProxy, Kubernetes control plane, workers, and GlusterFS nodes.[/dim]\n")

        sys.stdout.flush()
        use_infra_defaults = console.input(
            "Use default infrastructure sizing? [Y/n]: "
        ).strip().lower() != "n"

        if use_infra_defaults:
            # Show the defaults that will be used
            console.print("\n[green]Using default infrastructure configuration:[/green]\n")

            from rich.table import Table
            infra_table = Table(title="Infrastructure VMs", show_header=True)
            infra_table.add_column("Node Type", style="cyan")
            infra_table.add_column("Count", justify="center")
            infra_table.add_column("vCPU", justify="center")
            infra_table.add_column("RAM (GB)", justify="center")
            infra_table.add_column("Disk (GB)", justify="center")

            infra_table.add_row(
                "HAProxy (Load Balancer)",
                str(constants.DEFAULT_HAPROXY_CONFIG['count']),
                str(constants.DEFAULT_HAPROXY_CONFIG['cpu']),
                str(constants.DEFAULT_HAPROXY_CONFIG['ram_gb']),
                str(constants.DEFAULT_HAPROXY_CONFIG['disk_gb'])
            )
            infra_table.add_row(
                "K3s Control Plane",
                str(constants.DEFAULT_CONTROL_PLANE_CONFIG['count']),
                str(constants.DEFAULT_CONTROL_PLANE_CONFIG['cpu']),
                str(constants.DEFAULT_CONTROL_PLANE_CONFIG['ram_gb']),
                str(constants.DEFAULT_CONTROL_PLANE_CONFIG['disk_gb'])
            )
            infra_table.add_row(
                "K3s Workers",
                str(constants.DEFAULT_WORKERS_CONFIG['count']),
                str(constants.DEFAULT_WORKERS_CONFIG['cpu']),
                str(constants.DEFAULT_WORKERS_CONFIG['ram_gb']),
                str(constants.DEFAULT_WORKERS_CONFIG['disk_gb'])
            )
            infra_table.add_row(
                "GlusterFS Storage",
                str(constants.DEFAULT_GLUSTERFS_CONFIG['count']),
                str(constants.DEFAULT_GLUSTERFS_CONFIG['cpu']),
                str(constants.DEFAULT_GLUSTERFS_CONFIG['ram_gb']),
                f"{constants.DEFAULT_GLUSTERFS_CONFIG['os_disk_gb']} + {constants.DEFAULT_GLUSTERFS_CONFIG['data_disk_gb']} data"
            )
            console.print(infra_table)

            # Calculate totals
            total_vms = (
                constants.DEFAULT_HAPROXY_CONFIG['count'] +
                constants.DEFAULT_CONTROL_PLANE_CONFIG['count'] +
                constants.DEFAULT_WORKERS_CONFIG['count'] +
                constants.DEFAULT_GLUSTERFS_CONFIG['count']
            )
            total_vcpu = (
                constants.DEFAULT_HAPROXY_CONFIG['count'] * constants.DEFAULT_HAPROXY_CONFIG['cpu'] +
                constants.DEFAULT_CONTROL_PLANE_CONFIG['count'] * constants.DEFAULT_CONTROL_PLANE_CONFIG['cpu'] +
                constants.DEFAULT_WORKERS_CONFIG['count'] * constants.DEFAULT_WORKERS_CONFIG['cpu'] +
                constants.DEFAULT_GLUSTERFS_CONFIG['count'] * constants.DEFAULT_GLUSTERFS_CONFIG['cpu']
            )
            total_ram = (
                constants.DEFAULT_HAPROXY_CONFIG['count'] * constants.DEFAULT_HAPROXY_CONFIG['ram_gb'] +
                constants.DEFAULT_CONTROL_PLANE_CONFIG['count'] * constants.DEFAULT_CONTROL_PLANE_CONFIG['ram_gb'] +
                constants.DEFAULT_WORKERS_CONFIG['count'] * constants.DEFAULT_WORKERS_CONFIG['ram_gb'] +
                constants.DEFAULT_GLUSTERFS_CONFIG['count'] * constants.DEFAULT_GLUSTERFS_CONFIG['ram_gb']
            )
            console.print(f"\n[dim]Total: {total_vms} VMs, {total_vcpu} vCPU, {total_ram} GB RAM[/dim]")

        else:
            # Custom infrastructure sizing
            console.print("\n[yellow]Custom infrastructure sizing[/yellow]")
            console.print("[dim]Press Enter to accept default value shown in brackets.[/dim]\n")

            # HAProxy nodes
            console.print("[bold]HAProxy Load Balancers:[/bold]")
            console.print("[dim]  Provides high-availability load balancing with Keepalived VIP[/dim]")
            config.infrastructure.haproxy.count = int(
                console.input(f"  Count [{constants.DEFAULT_HAPROXY_CONFIG['count']}]: ") or
                str(constants.DEFAULT_HAPROXY_CONFIG['count'])
            )
            config.infrastructure.haproxy.cpu = int(
                console.input(f"  vCPU per node [{constants.DEFAULT_HAPROXY_CONFIG['cpu']}]: ") or
                str(constants.DEFAULT_HAPROXY_CONFIG['cpu'])
            )
            config.infrastructure.haproxy.ram_gb = int(
                console.input(f"  RAM GB per node [{constants.DEFAULT_HAPROXY_CONFIG['ram_gb']}]: ") or
                str(constants.DEFAULT_HAPROXY_CONFIG['ram_gb'])
            )
            config.infrastructure.haproxy.disk_gb = int(
                console.input(f"  Disk GB per node [{constants.DEFAULT_HAPROXY_CONFIG['disk_gb']}]: ") or
                str(constants.DEFAULT_HAPROXY_CONFIG['disk_gb'])
            )

            # Control plane nodes
            console.print("\n[bold]K3s Control Plane Nodes:[/bold]")
            console.print("[dim]  Kubernetes master nodes running etcd, API server, scheduler[/dim]")
            config.infrastructure.control_plane.count = int(
                console.input(f"  Count (must be odd) [{constants.DEFAULT_CONTROL_PLANE_CONFIG['count']}]: ") or
                str(constants.DEFAULT_CONTROL_PLANE_CONFIG['count'])
            )
            config.infrastructure.control_plane.cpu = int(
                console.input(f"  vCPU per node [{constants.DEFAULT_CONTROL_PLANE_CONFIG['cpu']}]: ") or
                str(constants.DEFAULT_CONTROL_PLANE_CONFIG['cpu'])
            )
            config.infrastructure.control_plane.ram_gb = int(
                console.input(f"  RAM GB per node [{constants.DEFAULT_CONTROL_PLANE_CONFIG['ram_gb']}]: ") or
                str(constants.DEFAULT_CONTROL_PLANE_CONFIG['ram_gb'])
            )
            config.infrastructure.control_plane.disk_gb = int(
                console.input(f"  Disk GB per node [{constants.DEFAULT_CONTROL_PLANE_CONFIG['disk_gb']}]: ") or
                str(constants.DEFAULT_CONTROL_PLANE_CONFIG['disk_gb'])
            )

            # Worker nodes
            console.print("\n[bold]K3s Worker Nodes:[/bold]")
            console.print("[dim]  Kubernetes worker nodes running application workloads[/dim]")
            config.infrastructure.workers.count = int(
                console.input(f"  Count [{constants.DEFAULT_WORKERS_CONFIG['count']}]: ") or
                str(constants.DEFAULT_WORKERS_CONFIG['count'])
            )
            config.infrastructure.workers.cpu = int(
                console.input(f"  vCPU per node [{constants.DEFAULT_WORKERS_CONFIG['cpu']}]: ") or
                str(constants.DEFAULT_WORKERS_CONFIG['cpu'])
            )
            config.infrastructure.workers.ram_gb = int(
                console.input(f"  RAM GB per node [{constants.DEFAULT_WORKERS_CONFIG['ram_gb']}]: ") or
                str(constants.DEFAULT_WORKERS_CONFIG['ram_gb'])
            )
            config.infrastructure.workers.disk_gb = int(
                console.input(f"  Disk GB per node [{constants.DEFAULT_WORKERS_CONFIG['disk_gb']}]: ") or
                str(constants.DEFAULT_WORKERS_CONFIG['disk_gb'])
            )

            # GlusterFS nodes
            console.print("\n[bold]GlusterFS Storage Nodes:[/bold]")
            console.print("[dim]  Distributed storage cluster with 3-way replication[/dim]")
            config.infrastructure.glusterfs.count = int(
                console.input(f"  Count (must be 3 for replica 3) [{constants.DEFAULT_GLUSTERFS_CONFIG['count']}]: ") or
                str(constants.DEFAULT_GLUSTERFS_CONFIG['count'])
            )
            config.infrastructure.glusterfs.cpu = int(
                console.input(f"  vCPU per node [{constants.DEFAULT_GLUSTERFS_CONFIG['cpu']}]: ") or
                str(constants.DEFAULT_GLUSTERFS_CONFIG['cpu'])
            )
            config.infrastructure.glusterfs.ram_gb = int(
                console.input(f"  RAM GB per node [{constants.DEFAULT_GLUSTERFS_CONFIG['ram_gb']}]: ") or
                str(constants.DEFAULT_GLUSTERFS_CONFIG['ram_gb'])
            )
            config.infrastructure.glusterfs.os_disk_gb = int(
                console.input(f"  OS Disk GB per node [{constants.DEFAULT_GLUSTERFS_CONFIG['os_disk_gb']}]: ") or
                str(constants.DEFAULT_GLUSTERFS_CONFIG['os_disk_gb'])
            )
            config.infrastructure.glusterfs.data_disk_gb = int(
                console.input(f"  Data Disk GB per node [{constants.DEFAULT_GLUSTERFS_CONFIG['data_disk_gb']}]: ") or
                str(constants.DEFAULT_GLUSTERFS_CONFIG['data_disk_gb'])
            )

        # ========================================
        # APPLICATION SIZING
        # ========================================
        console.print("\n")
        console.print("=" * 60, style="cyan")
        console.print("[bold cyan]STEP 5: APPLICATION SIZING[/bold cyan]")
        console.print("=" * 60, style="cyan")
        console.print("\n[dim]Configure resources for Gitea and supporting applications.[/dim]")
        console.print("[dim]Storage is provided by GlusterFS with 3-way replication.[/dim]\n")

        sys.stdout.flush()
        use_app_defaults = console.input(
            "Use default application sizing? [Y/n]: "
        ).strip().lower() != "n"

        if use_app_defaults:
            # Show the defaults that will be used
            console.print("\n[green]Using default application configuration:[/green]\n")

            app_table = Table(title="Application Configuration", show_header=True)
            app_table.add_column("Application", style="cyan")
            app_table.add_column("Replicas", justify="center")
            app_table.add_column("Storage", justify="right")

            app_table.add_row(
                "Gitea (Git Server)",
                str(constants.DEFAULT_GITEA_CONFIG['replicas']),
                f"{constants.DEFAULT_GITEA_CONFIG['repository_storage_gb']}GB repos + {constants.DEFAULT_GITEA_CONFIG['attachment_storage_gb']}GB attachments"
            )
            app_table.add_row(
                "PostgreSQL (Database)",
                str(constants.DEFAULT_POSTGRESQL_CONFIG['instances']),
                f"{constants.DEFAULT_POSTGRESQL_CONFIG['storage_per_instance_gb']}GB per instance"
            )
            app_table.add_row(
                "Redis (Cache/Queue)",
                str(constants.DEFAULT_REDIS_CONFIG['replicas']),
                f"{constants.DEFAULT_REDIS_CONFIG['storage_per_replica_gb']}GB per replica"
            )
            app_table.add_row(
                "Prometheus (Monitoring)",
                str(constants.DEFAULT_PROMETHEUS_CONFIG['replicas']),
                f"{constants.DEFAULT_PROMETHEUS_CONFIG['storage_per_replica_gb']}GB ({constants.DEFAULT_PROMETHEUS_CONFIG['retention_days']}d retention)"
            )
            app_table.add_row(
                "Grafana (Dashboards)",
                str(constants.DEFAULT_GRAFANA_CONFIG['replicas']),
                f"{constants.DEFAULT_GRAFANA_CONFIG['storage_per_replica_gb']}GB per replica"
            )
            app_table.add_row(
                "Portainer (Container Mgmt)",
                str(constants.DEFAULT_PORTAINER_CONFIG['replicas']),
                f"{constants.DEFAULT_PORTAINER_CONFIG['storage_gb']}GB"
            )
            console.print(app_table)

        else:
            # Custom application sizing
            console.print("\n[yellow]Custom application sizing[/yellow]")
            console.print("[dim]Press Enter to accept default value shown in brackets.[/dim]\n")

            # Gitea
            console.print("[bold]Gitea (Git Server):[/bold]")
            console.print("[dim]  Self-hosted Git service with web interface[/dim]")
            config.applications.gitea.replicas = int(
                console.input(f"  Replicas [{constants.DEFAULT_GITEA_CONFIG['replicas']}]: ") or
                str(constants.DEFAULT_GITEA_CONFIG['replicas'])
            )
            config.applications.gitea.repository_storage_gb = int(
                console.input(f"  Repository storage GB [{constants.DEFAULT_GITEA_CONFIG['repository_storage_gb']}]: ") or
                str(constants.DEFAULT_GITEA_CONFIG['repository_storage_gb'])
            )
            config.applications.gitea.attachment_storage_gb = int(
                console.input(f"  Attachment storage GB [{constants.DEFAULT_GITEA_CONFIG['attachment_storage_gb']}]: ") or
                str(constants.DEFAULT_GITEA_CONFIG['attachment_storage_gb'])
            )

            # PostgreSQL
            console.print("\n[bold]PostgreSQL (Database):[/bold]")
            console.print("[dim]  CloudNativePG managed PostgreSQL cluster[/dim]")
            config.applications.postgresql.instances = int(
                console.input(f"  Instances [{constants.DEFAULT_POSTGRESQL_CONFIG['instances']}]: ") or
                str(constants.DEFAULT_POSTGRESQL_CONFIG['instances'])
            )
            config.applications.postgresql.storage_per_instance_gb = int(
                console.input(f"  Storage GB per instance [{constants.DEFAULT_POSTGRESQL_CONFIG['storage_per_instance_gb']}]: ") or
                str(constants.DEFAULT_POSTGRESQL_CONFIG['storage_per_instance_gb'])
            )

            # Redis
            console.print("\n[bold]Redis (Cache/Session/Queue):[/bold]")
            console.print("[dim]  In-memory data store for caching and queues[/dim]")
            config.applications.redis.replicas = int(
                console.input(f"  Replicas [{constants.DEFAULT_REDIS_CONFIG['replicas']}]: ") or
                str(constants.DEFAULT_REDIS_CONFIG['replicas'])
            )
            config.applications.redis.storage_per_replica_gb = int(
                console.input(f"  Storage GB per replica [{constants.DEFAULT_REDIS_CONFIG['storage_per_replica_gb']}]: ") or
                str(constants.DEFAULT_REDIS_CONFIG['storage_per_replica_gb'])
            )

            # Prometheus
            console.print("\n[bold]Prometheus (Monitoring):[/bold]")
            console.print("[dim]  Metrics collection and alerting system[/dim]")
            config.applications.prometheus.replicas = int(
                console.input(f"  Replicas [{constants.DEFAULT_PROMETHEUS_CONFIG['replicas']}]: ") or
                str(constants.DEFAULT_PROMETHEUS_CONFIG['replicas'])
            )
            config.applications.prometheus.storage_per_replica_gb = int(
                console.input(f"  Storage GB per replica [{constants.DEFAULT_PROMETHEUS_CONFIG['storage_per_replica_gb']}]: ") or
                str(constants.DEFAULT_PROMETHEUS_CONFIG['storage_per_replica_gb'])
            )
            config.applications.prometheus.retention_days = int(
                console.input(f"  Retention days [{constants.DEFAULT_PROMETHEUS_CONFIG['retention_days']}]: ") or
                str(constants.DEFAULT_PROMETHEUS_CONFIG['retention_days'])
            )

            # Grafana
            console.print("\n[bold]Grafana (Dashboards):[/bold]")
            console.print("[dim]  Visualization and dashboard platform[/dim]")
            config.applications.grafana.replicas = int(
                console.input(f"  Replicas [{constants.DEFAULT_GRAFANA_CONFIG['replicas']}]: ") or
                str(constants.DEFAULT_GRAFANA_CONFIG['replicas'])
            )
            config.applications.grafana.storage_per_replica_gb = int(
                console.input(f"  Storage GB per replica [{constants.DEFAULT_GRAFANA_CONFIG['storage_per_replica_gb']}]: ") or
                str(constants.DEFAULT_GRAFANA_CONFIG['storage_per_replica_gb'])
            )

            # Portainer
            console.print("\n[bold]Portainer (Container Management):[/bold]")
            console.print("[dim]  Web-based container/Kubernetes management UI[/dim]")
            config.applications.portainer.replicas = int(
                console.input(f"  Replicas [{constants.DEFAULT_PORTAINER_CONFIG['replicas']}]: ") or
                str(constants.DEFAULT_PORTAINER_CONFIG['replicas'])
            )
            config.applications.portainer.storage_gb = int(
                console.input(f"  Storage GB [{constants.DEFAULT_PORTAINER_CONFIG['storage_gb']}]: ") or
                str(constants.DEFAULT_PORTAINER_CONFIG['storage_gb'])
            )

        # ========================================
        # DEPLOYMENT OPTIONS
        # ========================================
        console.print("\n")
        console.print("=" * 60, style="cyan")
        console.print("[bold cyan]STEP 6: DEPLOYMENT OPTIONS[/bold cyan]")
        console.print("=" * 60, style="cyan")
        console.print("\n[dim]Configure how the deployment will be executed.[/dim]\n")

        console.print("[bold]Interactive Mode:[/bold]")
        console.print("[dim]  Pause between each deployment phase for verification[/dim]")
        sys.stdout.flush()
        config.deployment.interactive_mode = console.input(
            "Enable interactive mode? [Y/n]: "
        ).strip().lower() != "n"

        console.print("\n[bold]Cleanup on Failure:[/bold]")
        console.print("[dim]  Automatically destroy created resources if deployment fails[/dim]")
        sys.stdout.flush()
        config.deployment.cleanup_on_failure = console.input(
            "Enable cleanup on failure? [y/N]: "
        ).strip().lower() == "y"

        # ========================================
        # METADATA
        # ========================================
        console.print("\n")
        console.print("=" * 60, style="cyan")
        console.print("[bold cyan]STEP 7: CONFIGURATION METADATA[/bold cyan]")
        console.print("=" * 60, style="cyan")
        console.print("\n[dim]Provide descriptive information for this configuration.[/dim]\n")

        config.metadata.environment = console.input(
            "Environment name [production]: "
        ).strip() or "production"

        config.metadata.description = console.input(
            "Description [Gitea Production Infrastructure]: "
        ).strip() or "Gitea Production Infrastructure"

        # ========================================
        # SUMMARY
        # ========================================
        console.print("\n")
        console.print("=" * 60, style="green")
        console.print("[bold green]CONFIGURATION COMPLETE[/bold green]")
        console.print("=" * 60, style="green")

        # Show summary
        console.print(f"\n[bold]Environment:[/bold] {config.metadata.environment}")
        console.print(f"[bold]vCenter:[/bold] {config.vsphere.vcenter_server}")
        console.print(f"[bold]Datacenter:[/bold] {config.vsphere.datacenter}")
        console.print(f"[bold]Cluster:[/bold] {config.vsphere.cluster}")
        console.print(f"[bold]Datastore:[/bold] {config.vsphere.datastore}")
        console.print(f"[bold]IP Range:[/bold] {config.network.ip_start} - {config.network.ip_end}")
        console.print(f"[bold]VIP Address:[/bold] {config.network.vip_address}")
        console.print(f"[bold]Total VMs:[/bold] {config.get_total_vms()}")
        console.print(f"[bold]Total vCPU:[/bold] {config.get_total_vcpu()}")
        console.print(f"[bold]Total RAM:[/bold] {config.get_total_ram_gb()} GB")
        console.print(f"[bold]Total Storage:[/bold] {config.get_total_storage_gb()} GB")

        return config
