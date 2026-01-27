"""
Hardcoded constants for Gitea Infrastructure Deployment.

These values should NOT be in configuration files - they are fixed for this deployment.
"""

# =============================================================================
# VSPHERE NETWORK CONFIGURATION (HARDCODED)
# =============================================================================
# Note: Use just the network name, not the full vSphere path
VSPHERE_NETWORK_PORT_GROUP = "NI_infra"
VSPHERE_ISO_DATASTORE = "/dualDC/datastore/Students/ISOs"
VSPHERE_ISO_PATH = "[ISOs] debian-12.1.0-amd64-netinst.iso"

# =============================================================================
# OPERATING SYSTEM (HARDCODED)
# =============================================================================
OS_NAME = "Debian"
OS_VERSION = "12.1.0"
OS_ARCHITECTURE = "amd64"
# Note: Use debian11_64Guest for vSphere 7.x compatibility (works with Debian 12)
# Use debian12_64Guest only if vSphere 8.0+ is confirmed
OS_GUEST_ID = "debian10_64Guest"

# =============================================================================
# KUBERNETES / K3S CONFIGURATION (HARDCODED)
# =============================================================================
# Must match kubectl version v1.35.0
K3S_VERSION = "v1.35.0+k3s1"
K3S_CHANNEL = "stable"
K3S_DISABLE_COMPONENTS = ["traefik", "servicelb"]

# =============================================================================
# PORT ASSIGNMENTS (HARDCODED - Conflict Resolved)
# =============================================================================
# Kubernetes API
K8S_API_PORT = 6443

# Traefik Ingress NodePorts
TRAEFIK_HTTP_NODEPORT = 30080   # For Gitea web access
TRAEFIK_HTTPS_NODEPORT = 30443  # Unused, no TLS

# Gitea SSH
GITEA_SSH_NODEPORT = 30022

# Admin Services NodePorts
PORTAINER_NODEPORT = 30090
PROMETHEUS_NODEPORT = 30091
GRAFANA_NODEPORT = 30092  # Moved from 30080 to avoid conflict with Traefik

# HAProxy
HAPROXY_STATS_PORT = 8404
HAPROXY_WEB_PORT = 80

# etcd
ETCD_CLIENT_PORT = 2379
ETCD_PEER_PORT = 2380

# Kubelet
KUBELET_PORT = 10250

# GlusterFS
GLUSTERFS_MANAGEMENT_PORT_START = 24007
GLUSTERFS_MANAGEMENT_PORT_END = 24008
GLUSTERFS_BRICK_PORT_START = 49152
GLUSTERFS_BRICK_PORT_END = 49664

# =============================================================================
# GLUSTERFS CONFIGURATION (HARDCODED)
# =============================================================================
GLUSTERFS_VERSION = "11"
GLUSTER_VOLUME_NAME = "storage-pool"
GLUSTER_VOLUME_TYPE = "replica 3"
GLUSTER_BRICK_PATH = "/data/brick1"
GLUSTER_REPLICA_COUNT = 3

# =============================================================================
# HAPROXY / KEEPALIVED CONFIGURATION (HARDCODED)
# =============================================================================
KEEPALIVED_VRRP_ID = 51
KEEPALIVED_MASTER_PRIORITY = 100
KEEPALIVED_BACKUP_PRIORITY = 90
KEEPALIVED_AUTH_PASS = "k33p@l1v3d"  # Default, should be overridden

# =============================================================================
# SSL/TLS CONFIGURATION (HARDCODED)
# =============================================================================
TLS_ENABLED = False  # No SSL for internal network

# =============================================================================
# CONTAINER REGISTRY (HARDCODED)
# =============================================================================
CONTAINER_REGISTRY = "docker.io"

# =============================================================================
# STORAGE CLASS (HARDCODED)
# =============================================================================
STORAGE_CLASS_NAME = "local-path"

# =============================================================================
# KUBERNETES NAMESPACES (HARDCODED)
# =============================================================================
NAMESPACES = {
    "gitea": "gitea",
    "postgresql": "gitea",  # Same namespace as Gitea
    "redis": "gitea",       # Same namespace as Gitea
    "monitoring": "monitoring",
    "cnpg": "cnpg-system",
    "ingress": "kube-system",
    "portainer": "portainer",
}

# =============================================================================
# HELM REPOSITORY URLS (HARDCODED)
# =============================================================================
HELM_REPOS = {
    "traefik": "https://traefik.github.io/charts",
    "gitea": "https://dl.gitea.com/charts/",
    "bitnami": "https://charts.bitnami.com/bitnami",
    "prometheus": "https://prometheus-community.github.io/helm-charts",
    "grafana": "https://grafana.github.io/helm-charts",
    "portainer": "https://portainer.github.io/k8s/",
    "cloudnative_pg": "https://cloudnative-pg.github.io/charts",
}

# =============================================================================
# LDAP CONFIGURATION (HARDCODED)
# =============================================================================
LDAP_SERVER = "172.27.16.1"

# =============================================================================
# VM NAMING CONVENTIONS (HARDCODED)
# =============================================================================
VM_NAME_PREFIX = {
    "haproxy": "haproxy",
    "master": "master",
    "worker": "worker",
    "glusterfs": "glusterfs",
    "backup": "backup",
}

TEMPLATE_NAMES = {
    "haproxy": "haproxy-template",
    "kubernetes": "kubernetes-template",
    "glusterfs": "glusterfs-template",
}

# =============================================================================
# DEFAULT RESOURCE SIZING (CONFIGURABLE DEFAULTS)
# =============================================================================
DEFAULT_HAPROXY_CONFIG = {
    "count": 2,
    "cpu": 2,
    "ram_gb": 4,
    "disk_gb": 20,
}

DEFAULT_CONTROL_PLANE_CONFIG = {
    "count": 3,
    "cpu": 4,
    "ram_gb": 8,
    "disk_gb": 100,
}

DEFAULT_WORKERS_CONFIG = {
    "count": 3,
    "cpu": 8,
    "ram_gb": 16,
    "disk_gb": 100,
}

DEFAULT_GLUSTERFS_CONFIG = {
    "count": 3,
    "cpu": 4,
    "ram_gb": 8,
    "os_disk_gb": 50,
    "data_disk_gb": 500,
}

# =============================================================================
# DEFAULT APPLICATION SIZING (CONFIGURABLE DEFAULTS)
# =============================================================================
DEFAULT_GITEA_CONFIG = {
    "replicas": 3,
    "repository_storage_gb": 200,
    "attachment_storage_gb": 50,
}

DEFAULT_POSTGRESQL_CONFIG = {
    "instances": 3,
    "storage_per_instance_gb": 50,
}

DEFAULT_REDIS_CONFIG = {
    "replicas": 3,
    "storage_per_replica_gb": 5,
}

DEFAULT_PROMETHEUS_CONFIG = {
    "replicas": 2,
    "storage_per_replica_gb": 30,
    "retention_days": 15,
}

DEFAULT_GRAFANA_CONFIG = {
    "replicas": 2,
    "storage_per_replica_gb": 10,
}

DEFAULT_PORTAINER_CONFIG = {
    "replicas": 1,
    "storage_gb": 5,
}

DEFAULT_GITEA_RUNNER_CONFIG = {
    "replicas": 3,
    "runner_labels": "ubuntu-latest,linux",
}

DEFAULT_BACKUP_CONFIG = {
    "enabled": True,
    "count": 1,
    "cpu": 2,
    "ram_gb": 4,
    "disk_gb": 200,
    "retention_days": 7,
    "schedule": "0 2 * * *",  # Daily at 2 AM
}

# =============================================================================
# DEFAULT NETWORK CONFIGURATION
# =============================================================================
DEFAULT_NETWORK_CONFIG = {
    "subnet_mask": "255.255.255.0",
    "dns_servers": ["8.8.8.8", "1.1.1.1"],
    "domain": "",  # HARDCODED: No domain suffix
}

# =============================================================================
# DEFAULT DEPLOYMENT OPTIONS
# =============================================================================
DEFAULT_DEPLOYMENT_OPTIONS = {
    "interactive_mode": True,
    "cleanup_on_failure": False,
    "parallel_vm_creation": True,
    "max_parallel_tasks": 5,
}

# =============================================================================
# TIMEOUTS (SECONDS)
# =============================================================================
SSH_TIMEOUT = 300        # 5 minutes for SSH connection
VM_BOOT_TIMEOUT = 600    # 10 minutes for VM to boot
PACKER_TIMEOUT = 2700    # 45 minutes for Packer build (allow time for vSphere template conversion)
TERRAFORM_TIMEOUT = 3600  # 60 minutes for Terraform apply (11 VMs with parallelism=3)
ANSIBLE_TIMEOUT = 1200   # 20 minutes for Ansible playbook
HELM_TIMEOUT = 600       # 10 minutes for Helm install

# =============================================================================
# RETRY CONFIGURATION
# =============================================================================
MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds

# =============================================================================
# FILE PATHS (RELATIVE TO PROJECT ROOT)
# =============================================================================
PATHS = {
    "configs": "configs",
    "templates": "templates",
    "packer": "packer",
    "terraform": "terraform",
    "ansible": "ansible",
    "helm": "helm",
    "manifests": "manifests",
    "kubeconfig": "kubeconfig",
    "credentials": "credentials",
    "logs": "logs",
}

# =============================================================================
# MINIMUM REQUIREMENTS
# =============================================================================
MIN_IP_ADDRESSES = 11      # Minimum IPs needed for deployment
MIN_VCPU_TOTAL = 56        # Minimum total vCPU for deployment
MIN_RAM_GB_TOTAL = 140     # Minimum total RAM for deployment
MIN_STORAGE_TB = 2.0       # Minimum storage for deployment

# =============================================================================
# ALERTMANAGER SILENCED ALERTS (Noisy/irrelevant for K3s)
# =============================================================================
SILENCED_ALERTS = [
    "Watchdog",                         # Test alert, always fires
    "KubeProxyDown",                    # K3s doesn't use kube-proxy
    "etcd.*",                           # K3s embedded etcd, false positives
    "Alertmanager.*",                   # Internal alertmanager alerts
    "PrometheusTSDBCompactionsFailing", # GlusterFS latency issues
    "InfoInhibitor",                    # Internal alert
]
