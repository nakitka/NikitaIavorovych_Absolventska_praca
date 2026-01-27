"""
Validation module for Gitea Infrastructure Deployment.

Provides configuration validation and preflight checks before deployment.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from .config import DeploymentConfig
from .discovery import VSphereDiscovery
from .utils import (
    get_logger,
    check_tool_installed,
    get_tool_version,
    ip_range_to_list,
)
from . import constants


@dataclass
class ValidationResult:
    """Result of a validation check."""
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Add an error and mark as invalid."""
        self.valid = False
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        """Add a warning (doesn't affect validity)."""
        self.warnings.append(message)

    def add_info(self, message: str) -> None:
        """Add an informational message."""
        self.info.append(message)

    def merge(self, other: 'ValidationResult') -> None:
        """Merge another result into this one."""
        if not other.valid:
            self.valid = False
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.info.extend(other.info)


class ConfigValidator:
    """
    Validates deployment configuration for correctness and completeness.
    """

    def __init__(self, config: DeploymentConfig):
        """
        Initialize ConfigValidator.

        Args:
            config: DeploymentConfig to validate
        """
        self.logger = get_logger()
        self.config = config

    def validate_all(self) -> ValidationResult:
        """
        Run all configuration validations.

        Returns:
            Combined ValidationResult
        """
        result = ValidationResult()

        # Run individual validations
        result.merge(self.validate_vsphere_config())
        result.merge(self.validate_network_config())
        result.merge(self.validate_infrastructure_config())
        result.merge(self.validate_applications_config())
        result.merge(self.validate_storage_capacity())

        return result

    def validate_vsphere_config(self) -> ValidationResult:
        """Validate vSphere configuration."""
        result = ValidationResult()
        vs = self.config.vsphere

        # Required fields
        required_fields = [
            ("vcenter_server", vs.vcenter_server),
            ("username", vs.username),
            ("datacenter", vs.datacenter),
            ("cluster", vs.cluster),
            ("datastore", vs.datastore),
        ]

        for field_name, value in required_fields:
            if not value:
                result.add_error(f"vSphere: {field_name} is required")

        # Password check
        if not vs.password:
            result.add_warning(
                "vSphere: password not in config, ensure VSPHERE_PASSWORD env var is set"
            )

        # Validate paths
        if vs.template_folder and not vs.template_folder.startswith("/"):
            result.add_warning(
                "vSphere: template_folder should be an absolute path (starting with /)"
            )

        if vs.vm_folder and not vs.vm_folder.startswith("/"):
            result.add_warning(
                "vSphere: vm_folder should be an absolute path (starting with /)"
            )

        return result

    def validate_network_config(self) -> ValidationResult:
        """Validate network configuration."""
        result = ValidationResult()
        net = self.config.network

        # Required fields
        required_fields = [
            ("ip_start", net.ip_start),
            ("ip_end", net.ip_end),
            ("vip_address", net.vip_address),
            ("subnet_mask", net.subnet_mask),
            ("gateway", net.gateway),
        ]

        for field_name, value in required_fields:
            if not value:
                result.add_error(f"Network: {field_name} is required")
                return result  # Can't continue without basic fields

        # Validate IP format
        def is_valid_ip(ip: str) -> bool:
            parts = ip.split(".")
            if len(parts) != 4:
                return False
            try:
                return all(0 <= int(p) <= 255 for p in parts)
            except ValueError:
                return False

        for ip_field, ip_value in [
            ("ip_start", net.ip_start),
            ("ip_end", net.ip_end),
            ("vip_address", net.vip_address),
            ("gateway", net.gateway),
        ]:
            if ip_value and not is_valid_ip(ip_value):
                result.add_error(f"Network: {ip_field} is not a valid IP address")

        # Validate IP range provides enough IPs
        try:
            ip_list = ip_range_to_list(net.ip_start, net.ip_end)
            required_ips = self.config.get_total_vms()

            if len(ip_list) < required_ips:
                result.add_error(
                    f"Network: IP range provides {len(ip_list)} IPs, "
                    f"but {required_ips} are required for {required_ips} VMs"
                )
            elif len(ip_list) < constants.MIN_IP_ADDRESSES:
                result.add_error(
                    f"Network: IP range must provide at least {constants.MIN_IP_ADDRESSES} IPs"
                )

            # Check VIP is not in VM range
            if net.vip_address in ip_list:
                result.add_error(
                    "Network: VIP address must not be within the VM IP range"
                )

            result.add_info(f"Network: IP range provides {len(ip_list)} addresses")

        except Exception as e:
            result.add_error(f"Network: Invalid IP range - {e}")

        # Validate subnet mask
        valid_masks = [
            "255.255.255.0",
            "255.255.255.128",
            "255.255.255.192",
            "255.255.255.224",
            "255.255.254.0",
            "255.255.252.0",
            "255.255.248.0",
            "255.255.240.0",
            "255.255.0.0",
        ]
        if net.subnet_mask not in valid_masks:
            result.add_warning(
                f"Network: Unusual subnet mask {net.subnet_mask}"
            )

        # Validate DNS servers
        if not net.dns_servers:
            result.add_error("Network: At least one DNS server is required")
        else:
            for dns in net.dns_servers:
                if not is_valid_ip(dns):
                    result.add_error(f"Network: Invalid DNS server IP: {dns}")

        return result

    def validate_infrastructure_config(self) -> ValidationResult:
        """Validate infrastructure sizing configuration."""
        result = ValidationResult()
        infra = self.config.infrastructure

        # HAProxy count
        if infra.haproxy.count not in [0, 1, 2]:
            result.add_error(
                "Infrastructure: HAProxy count must be 0, 1, or 2"
            )
        elif infra.haproxy.count == 1:
            result.add_warning(
                "Infrastructure: Single HAProxy node has no high availability"
            )
        elif infra.haproxy.count == 0:
            result.add_warning(
                "Infrastructure: No HAProxy nodes - direct access to masters required"
            )

        # Control plane count must be odd for etcd quorum
        if infra.control_plane.count not in [1, 3, 5]:
            result.add_error(
                "Infrastructure: Control plane count must be 1, 3, or 5 (odd number for quorum)"
            )
        elif infra.control_plane.count == 1:
            result.add_warning(
                "Infrastructure: Single control plane node has no high availability"
            )

        # Workers count
        if infra.workers.count < 1:
            result.add_error(
                "Infrastructure: At least 1 worker node is required"
            )
        elif infra.workers.count == 1:
            result.add_warning(
                "Infrastructure: Single worker node has no high availability for workloads"
            )

        # GlusterFS count must be 3 for replica 3
        if infra.glusterfs.count != 3:
            result.add_error(
                "Infrastructure: GlusterFS count must be 3 for replica 3 configuration"
            )

        # Validate resource sizing
        for name, sizing in [
            ("HAProxy", infra.haproxy),
            ("Control Plane", infra.control_plane),
            ("Workers", infra.workers),
        ]:
            if sizing.count > 0:
                if sizing.cpu < 1:
                    result.add_error(f"Infrastructure: {name} CPU must be at least 1")
                if sizing.ram_gb < 1:
                    result.add_error(f"Infrastructure: {name} RAM must be at least 1GB")
                if sizing.disk_gb < 10:
                    result.add_error(f"Infrastructure: {name} disk must be at least 10GB")

        # GlusterFS specific
        if infra.glusterfs.count > 0:
            if infra.glusterfs.os_disk_gb < 20:
                result.add_error(
                    "Infrastructure: GlusterFS OS disk must be at least 20GB"
                )
            if infra.glusterfs.data_disk_gb < 100:
                result.add_error(
                    "Infrastructure: GlusterFS data disk must be at least 100GB"
                )

        # Total resources check
        total_vcpu = self.config.get_total_vcpu()
        total_ram = self.config.get_total_ram_gb()
        total_storage = self.config.get_total_storage_gb()

        result.add_info(f"Infrastructure: Total vCPU: {total_vcpu}")
        result.add_info(f"Infrastructure: Total RAM: {total_ram}GB")
        result.add_info(f"Infrastructure: Total Storage: {total_storage}GB ({total_storage/1024:.2f}TB)")

        return result

    def validate_applications_config(self) -> ValidationResult:
        """Validate applications configuration."""
        result = ValidationResult()
        apps = self.config.applications

        # Gitea
        if apps.gitea.replicas < 1:
            result.add_error("Applications: Gitea requires at least 1 replica")
        if apps.gitea.repository_storage_gb < 10:
            result.add_error("Applications: Gitea repository storage must be at least 10GB")

        # PostgreSQL
        if apps.postgresql.instances not in [1, 3, 5]:
            result.add_error(
                "Applications: PostgreSQL instances should be 1, 3, or 5 for proper HA"
            )
        if apps.postgresql.storage_per_instance_gb < 5:
            result.add_error("Applications: PostgreSQL storage per instance must be at least 5GB")

        # Redis
        if apps.redis.replicas < 1:
            result.add_error("Applications: Redis requires at least 1 replica")

        # Prometheus
        if apps.prometheus.replicas < 1:
            result.add_error("Applications: Prometheus requires at least 1 replica")
        if apps.prometheus.retention_days < 1:
            result.add_error("Applications: Prometheus retention must be at least 1 day")

        # Grafana
        if apps.grafana.replicas < 1:
            result.add_error("Applications: Grafana requires at least 1 replica")

        return result

    def validate_storage_capacity(self) -> ValidationResult:
        """Validate that application storage fits in GlusterFS capacity."""
        result = ValidationResult()

        app_storage = self.config.get_total_app_storage_gb()
        gluster_capacity = self.config.get_glusterfs_usable_gb()

        result.add_info(f"Storage: Application storage required: {app_storage}GB")
        result.add_info(f"Storage: GlusterFS usable capacity: {gluster_capacity}GB")

        if app_storage > gluster_capacity:
            result.add_error(
                f"Storage: Application storage ({app_storage}GB) exceeds "
                f"GlusterFS capacity ({gluster_capacity}GB)"
            )
        elif app_storage > gluster_capacity * 0.8:
            result.add_warning(
                f"Storage: Application storage uses {app_storage/gluster_capacity*100:.0f}% "
                "of GlusterFS capacity - consider increasing data disk size"
            )

        return result


class PreflightValidator:
    """
    Performs preflight checks before deployment.

    Verifies tools are installed, credentials work, and resources are available.
    """

    def __init__(self, config: DeploymentConfig):
        """
        Initialize PreflightValidator.

        Args:
            config: DeploymentConfig for deployment
        """
        self.logger = get_logger()
        self.config = config

    def validate_all(self, check_vsphere: bool = True) -> ValidationResult:
        """
        Run all preflight validations.

        Args:
            check_vsphere: Whether to check vSphere connectivity

        Returns:
            Combined ValidationResult
        """
        result = ValidationResult()

        result.merge(self.validate_tools())
        result.merge(self.validate_python_dependencies())

        if check_vsphere and self.config.vsphere.vcenter_server:
            result.merge(self.validate_vsphere_connectivity())

        return result

    def validate_tools(self) -> ValidationResult:
        """Validate required tools are installed."""
        result = ValidationResult()

        required_tools = [
            ("python3", "--version"),
            ("packer", "--version"),
            ("terraform", "--version"),
            ("ansible", "--version"),
            ("ansible-playbook", "--version"),
            ("helm", "version"),
            ("kubectl", "version --client"),
        ]

        optional_tools = [
            ("govc", "version"),
        ]

        for tool, version_cmd in required_tools:
            if check_tool_installed(tool):
                version = get_tool_version(tool, version_cmd.split()[0])
                result.add_info(f"Tool: {tool} installed - {version or 'version unknown'}")
            else:
                result.add_error(f"Tool: {tool} is required but not installed")

        for tool, version_cmd in optional_tools:
            if check_tool_installed(tool):
                version = get_tool_version(tool, version_cmd.split()[0])
                result.add_info(f"Tool: {tool} installed (optional) - {version or 'version unknown'}")
            else:
                result.add_warning(f"Tool: {tool} not installed (optional)")

        return result

    def validate_python_dependencies(self) -> ValidationResult:
        """Validate Python dependencies are installed."""
        result = ValidationResult()

        required_packages = [
            "yaml",
            "jinja2",
            "rich",
        ]

        optional_packages = [
            ("pyVmomi", "pyvmomi - required for vSphere discovery"),
            ("paramiko", "paramiko - useful for SSH operations"),
            ("requests", "requests - useful for HTTP operations"),
        ]

        for package in required_packages:
            try:
                __import__(package)
                result.add_info(f"Python: {package} available")
            except ImportError:
                result.add_error(f"Python: {package} is required but not installed")

        for package, description in optional_packages:
            try:
                __import__(package)
                result.add_info(f"Python: {package} available")
            except ImportError:
                result.add_warning(f"Python: {description} not installed")

        return result

    def validate_vsphere_connectivity(self) -> ValidationResult:
        """Validate vSphere connectivity and credentials."""
        result = ValidationResult()

        if not self.config.vsphere.password:
            result.add_warning(
                "vSphere: Password not set, skipping connectivity check"
            )
            return result

        try:
            discovery = VSphereDiscovery()
            discovery.connect(
                host=self.config.vsphere.vcenter_server,
                user=self.config.vsphere.username,
                password=self.config.vsphere.password,
            )

            # Verify datacenter exists
            datacenters = discovery.list_datacenters()
            dc_names = [dc["name"] for dc in datacenters]

            if self.config.vsphere.datacenter not in dc_names:
                result.add_error(
                    f"vSphere: Datacenter '{self.config.vsphere.datacenter}' not found. "
                    f"Available: {', '.join(dc_names)}"
                )
            else:
                result.add_info(
                    f"vSphere: Datacenter '{self.config.vsphere.datacenter}' found"
                )

                # Verify cluster exists
                clusters = discovery.list_clusters(self.config.vsphere.datacenter)
                cluster_names = [c["name"] for c in clusters]

                if self.config.vsphere.cluster not in cluster_names:
                    result.add_error(
                        f"vSphere: Cluster '{self.config.vsphere.cluster}' not found. "
                        f"Available: {', '.join(cluster_names)}"
                    )
                else:
                    result.add_info(
                        f"vSphere: Cluster '{self.config.vsphere.cluster}' found"
                    )

                # Verify datastore exists
                datastores = discovery.list_datastores(self.config.vsphere.datacenter)
                ds_names = [d["name"] for d in datastores]

                if self.config.vsphere.datastore not in ds_names:
                    result.add_error(
                        f"vSphere: Datastore '{self.config.vsphere.datastore}' not found. "
                        f"Available: {', '.join(ds_names)}"
                    )
                else:
                    result.add_info(
                        f"vSphere: Datastore '{self.config.vsphere.datastore}' found"
                    )

                # Validate resources
                resource_check = discovery.validate_resources(
                    datacenter_name=self.config.vsphere.datacenter,
                    cluster_name=self.config.vsphere.cluster,
                    datastore_name=self.config.vsphere.datastore,
                    required_vcpu=self.config.get_total_vcpu(),
                    required_ram_gb=self.config.get_total_ram_gb(),
                    required_storage_gb=self.config.get_total_storage_gb(),
                )

                for error in resource_check.get("errors", []):
                    result.add_error(f"vSphere: {error}")
                for warning in resource_check.get("warnings", []):
                    result.add_warning(f"vSphere: {warning}")

            discovery.disconnect()
            result.add_info("vSphere: Connectivity validated successfully")

        except Exception as e:
            result.add_error(f"vSphere: Connection failed - {e}")

        return result

    def validate_network_connectivity(self) -> ValidationResult:
        """
        Validate network connectivity to required endpoints.

        Note: This is a basic check, actual VM network access
        depends on vSphere network configuration.
        """
        result = ValidationResult()

        import socket

        # Check connectivity to key endpoints
        endpoints = [
            (self.config.vsphere.vcenter_server, 443, "vCenter API"),
            ("get.k3s.io", 443, "K3s installer"),
        ]

        for host, port, description in endpoints:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((host, port))
                sock.close()
                result.add_info(f"Network: {description} ({host}:{port}) reachable")
            except Exception as e:
                result.add_warning(f"Network: {description} ({host}:{port}) not reachable - {e}")

        return result


def validate_deployment(
    config: DeploymentConfig,
    check_vsphere: bool = True,
) -> ValidationResult:
    """
    Convenience function to run all validations.

    Args:
        config: DeploymentConfig to validate
        check_vsphere: Whether to check vSphere connectivity

    Returns:
        Combined ValidationResult
    """
    result = ValidationResult()

    # Configuration validation
    config_validator = ConfigValidator(config)
    result.merge(config_validator.validate_all())

    # Preflight validation
    preflight_validator = PreflightValidator(config)
    result.merge(preflight_validator.validate_all(check_vsphere=check_vsphere))

    return result
