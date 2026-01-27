"""
vSphere Discovery module for Gitea Infrastructure Deployment.

Uses pyvmomi to discover vSphere resources and validate deployment requirements.
"""

import ssl
import atexit
from typing import Optional, List, Dict, Any

from pyVim import connect
from pyVmomi import vim, vmodl

from .utils import get_logger


class VSphereDiscovery:
    """
    vSphere resource discovery using pyvmomi.

    Provides methods to discover datacenters, clusters, datastores,
    and resource pools for infrastructure deployment.
    """

    def __init__(self):
        """Initialize VSphereDiscovery."""
        self.logger = get_logger()
        self.service_instance = None
        self.content = None

    def connect(
        self,
        host: str,
        user: str,
        password: str,
        port: int = 443,
        disable_ssl_verification: bool = True,
    ) -> None:
        """
        Connect to vCenter server.

        Args:
            host: vCenter hostname or IP
            user: Username
            password: Password
            port: Port number (default 443)
            disable_ssl_verification: Skip SSL certificate verification
        """
        try:
            if disable_ssl_verification:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            else:
                context = None

            self.service_instance = connect.SmartConnect(
                host=host,
                user=user,
                pwd=password,
                port=port,
                sslContext=context,
            )

            atexit.register(connect.Disconnect, self.service_instance)
            self.content = self.service_instance.RetrieveContent()

            self.logger.info(f"Connected to vCenter: {host}")

        except vmodl.MethodFault as e:
            self.logger.error(f"vSphere API error: {e.msg}")
            raise
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            raise

    def disconnect(self) -> None:
        """Disconnect from vCenter server."""
        if self.service_instance:
            connect.Disconnect(self.service_instance)
            self.service_instance = None
            self.content = None
            self.logger.info("Disconnected from vCenter")

    def _get_obj(self, vimtype: list, name: str, folder: vim.Folder = None) -> Any:
        """
        Get vSphere object by name and type.

        Args:
            vimtype: List of vSphere object types to search
            name: Object name
            folder: Starting folder (defaults to rootFolder)

        Returns:
            vSphere object or None
        """
        if folder is None:
            folder = self.content.rootFolder

        container = self.content.viewManager.CreateContainerView(
            folder, vimtype, True
        )

        obj = None
        for item in container.view:
            if item.name == name:
                obj = item
                break

        container.Destroy()
        return obj

    def _get_all_objs(self, vimtype: list, folder: vim.Folder = None) -> List[Any]:
        """
        Get all vSphere objects of a type.

        Args:
            vimtype: List of vSphere object types
            folder: Starting folder (defaults to rootFolder)

        Returns:
            List of vSphere objects
        """
        if folder is None:
            folder = self.content.rootFolder

        container = self.content.viewManager.CreateContainerView(
            folder, vimtype, True
        )

        objs = list(container.view)
        container.Destroy()
        return objs

    def list_datacenters(self) -> List[Dict[str, Any]]:
        """
        List all datacenters with resource counts.

        Returns:
            List of datacenter information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        datacenters = self._get_all_objs([vim.Datacenter])
        result = []

        for dc in datacenters:
            # Count resources
            clusters = self._get_all_objs([vim.ClusterComputeResource], dc.hostFolder)
            datastores = self._get_all_objs([vim.Datastore], dc.datastoreFolder)
            networks = self._get_all_objs([vim.Network], dc.networkFolder)

            result.append({
                "name": dc.name,
                "cluster_count": len(clusters),
                "datastore_count": len(datastores),
                "network_count": len(networks),
            })

        return result

    def list_clusters(self, datacenter_name: str) -> List[Dict[str, Any]]:
        """
        List all clusters in a datacenter with resource availability.

        Args:
            datacenter_name: Name of the datacenter

        Returns:
            List of cluster information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        clusters = self._get_all_objs([vim.ClusterComputeResource], dc.hostFolder)
        result = []

        for cluster in clusters:
            # Get resource summary
            summary = cluster.GetResourceUsage()

            # Calculate available resources
            total_cpu = cluster.summary.totalCpu  # MHz
            used_cpu = summary.cpuUsedMHz if hasattr(summary, 'cpuUsedMHz') else 0
            available_cpu = total_cpu - used_cpu

            total_memory = cluster.summary.totalMemory  # Bytes
            used_memory = summary.memUsedMB * 1024 * 1024 if hasattr(summary, 'memUsedMB') else 0
            available_memory = total_memory - used_memory

            # Get host count
            hosts = cluster.host if cluster.host else []

            result.append({
                "name": cluster.name,
                "host_count": len(hosts),
                "cpu_total_mhz": total_cpu,
                "cpu_available_mhz": available_cpu,
                "cpu_available": f"{available_cpu // 1000}GHz",
                "memory_total_gb": total_memory / (1024**3),
                "memory_available_gb": available_memory / (1024**3),
            })

        return result

    def list_datastore_folders(
        self,
        datacenter_name: str,
    ) -> List[Dict[str, Any]]:
        """
        List all datastore folders in a datacenter.

        Args:
            datacenter_name: Name of the datacenter

        Returns:
            List of datastore folder information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        def get_folders_with_datastores(folder: vim.Folder, path: str = "") -> List[Dict]:
            """Recursively get folders and count their datastores."""
            folders = []
            current_path = f"{path}/{folder.name}" if path else folder.name

            # Count datastores directly in this folder
            datastore_count = 0
            subfolder_count = 0
            for child in folder.childEntity:
                if isinstance(child, vim.Datastore):
                    datastore_count += 1
                elif isinstance(child, vim.Folder):
                    subfolder_count += 1

            # Only include folders that have datastores or subfolders
            if datastore_count > 0 or subfolder_count > 0:
                folders.append({
                    "name": folder.name,
                    "path": current_path,
                    "datastore_count": datastore_count,
                    "subfolder_count": subfolder_count,
                })

            # Recurse into subfolders
            for child in folder.childEntity:
                if isinstance(child, vim.Folder):
                    folders.extend(get_folders_with_datastores(child, current_path))

            return folders

        return get_folders_with_datastores(dc.datastoreFolder)

    def list_datastores_in_folder(
        self,
        datacenter_name: str,
        folder_path: str,
        min_free_gb: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        List datastores within a specific folder.

        Args:
            datacenter_name: Name of the datacenter
            folder_path: Path to the folder (e.g., "datastore/Management")
            min_free_gb: Minimum free space filter in GB

        Returns:
            List of datastore information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        # Navigate to the specified folder
        def find_folder(folder: vim.Folder, path_parts: List[str]) -> Optional[vim.Folder]:
            if not path_parts:
                return folder
            target = path_parts[0]
            remaining = path_parts[1:]
            for child in folder.childEntity:
                if isinstance(child, vim.Folder) and child.name == target:
                    return find_folder(child, remaining)
            return None

        # Parse folder path
        path_parts = [p for p in folder_path.split("/") if p and p != "datastore"]
        target_folder = find_folder(dc.datastoreFolder, path_parts)

        if not target_folder:
            # Fall back to root datastore folder
            target_folder = dc.datastoreFolder

        result = []
        for child in target_folder.childEntity:
            if isinstance(child, vim.Datastore):
                summary = child.summary
                capacity_gb = summary.capacity / (1024**3)
                free_gb = summary.freeSpace / (1024**3)
                used_gb = capacity_gb - free_gb

                if min_free_gb and free_gb < min_free_gb:
                    continue

                result.append({
                    "name": child.name,
                    "type": summary.type,
                    "capacity_gb": round(capacity_gb, 2),
                    "free_gb": round(free_gb, 2),
                    "used_gb": round(used_gb, 2),
                    "accessible": summary.accessible,
                })

        # Sort by free space descending
        result.sort(key=lambda x: x["free_gb"], reverse=True)
        return result

    def list_datastores(
        self,
        datacenter_name: str,
        min_free_gb: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        List all datastores in a datacenter with capacity information.

        Args:
            datacenter_name: Name of the datacenter
            min_free_gb: Minimum free space filter in GB

        Returns:
            List of datastore information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        datastores = self._get_all_objs([vim.Datastore], dc.datastoreFolder)
        result = []

        for ds in datastores:
            summary = ds.summary

            capacity_gb = summary.capacity / (1024**3)
            free_gb = summary.freeSpace / (1024**3)
            used_gb = capacity_gb - free_gb

            if min_free_gb and free_gb < min_free_gb:
                continue

            result.append({
                "name": ds.name,
                "type": summary.type,
                "capacity_gb": round(capacity_gb, 2),
                "free_gb": round(free_gb, 2),
                "used_gb": round(used_gb, 2),
                "accessible": summary.accessible,
            })

        # Sort by free space descending
        result.sort(key=lambda x: x["free_gb"], reverse=True)
        return result

    def list_resource_pools(
        self,
        datacenter_name: str,
        cluster_name: str,
    ) -> List[Dict[str, Any]]:
        """
        List all resource pools in a cluster.

        Args:
            datacenter_name: Name of the datacenter
            cluster_name: Name of the cluster

        Returns:
            List of resource pool information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        cluster = self._get_obj([vim.ClusterComputeResource], cluster_name, dc.hostFolder)
        if not cluster:
            raise ValueError(f"Cluster not found: {cluster_name}")

        def get_pools(pool: vim.ResourcePool, path: str = "") -> List[Dict]:
            pools = []
            current_path = f"{path}/{pool.name}" if path else pool.name

            pools.append({
                "name": pool.name,
                "path": current_path,
                "cpu_reservation": pool.summary.config.cpuAllocation.reservation,
                "memory_reservation_mb": pool.summary.config.memoryAllocation.reservation,
            })

            for child in pool.resourcePool:
                pools.extend(get_pools(child, current_path))

            return pools

        return get_pools(cluster.resourcePool)

    def list_networks(self, datacenter_name: str) -> List[Dict[str, Any]]:
        """
        List all networks in a datacenter.

        Args:
            datacenter_name: Name of the datacenter

        Returns:
            List of network information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        networks = self._get_all_objs([vim.Network], dc.networkFolder)
        result = []

        for net in networks:
            result.append({
                "name": net.name,
                "type": type(net).__name__,
                "accessible": net.summary.accessible if hasattr(net.summary, 'accessible') else True,
            })

        return result

    def list_folders(self, datacenter_name: str) -> List[Dict[str, Any]]:
        """
        List all VM folders in a datacenter.

        Args:
            datacenter_name: Name of the datacenter

        Returns:
            List of folder information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        def get_folders(folder: vim.Folder, path: str = "") -> List[Dict]:
            folders = []
            current_path = f"{path}/{folder.name}" if path else folder.name

            folders.append({
                "name": folder.name,
                "path": current_path,
            })

            for child in folder.childEntity:
                if isinstance(child, vim.Folder):
                    folders.extend(get_folders(child, current_path))

            return folders

        return get_folders(dc.vmFolder)

    def validate_resources(
        self,
        datacenter_name: str,
        cluster_name: str,
        datastore_name: str,
        required_vcpu: int,
        required_ram_gb: int,
        required_storage_gb: int,
    ) -> Dict[str, Any]:
        """
        Validate that the cluster and datastore have sufficient resources.

        Args:
            datacenter_name: Name of the datacenter
            cluster_name: Name of the cluster
            datastore_name: Name of the datastore
            required_vcpu: Required vCPU count
            required_ram_gb: Required RAM in GB
            required_storage_gb: Required storage in GB

        Returns:
            Validation result dictionary
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "cluster": {},
            "datastore": {},
        }

        # Check cluster resources
        clusters = self.list_clusters(datacenter_name)
        cluster_info = next((c for c in clusters if c["name"] == cluster_name), None)

        if not cluster_info:
            result["valid"] = False
            result["errors"].append(f"Cluster not found: {cluster_name}")
        else:
            result["cluster"] = cluster_info

            # Estimate vCPU from MHz (assume ~2GHz per core)
            available_vcpu = cluster_info["cpu_available_mhz"] / 2000

            if available_vcpu < required_vcpu:
                result["valid"] = False
                result["errors"].append(
                    f"Insufficient CPU: need {required_vcpu} vCPU, "
                    f"available ~{available_vcpu:.0f} vCPU"
                )

            if cluster_info["memory_available_gb"] < required_ram_gb:
                result["valid"] = False
                result["errors"].append(
                    f"Insufficient RAM: need {required_ram_gb}GB, "
                    f"available {cluster_info['memory_available_gb']:.1f}GB"
                )

        # Check datastore
        datastores = self.list_datastores(datacenter_name)
        datastore_info = next((d for d in datastores if d["name"] == datastore_name), None)

        if not datastore_info:
            result["valid"] = False
            result["errors"].append(f"Datastore not found: {datastore_name}")
        else:
            result["datastore"] = datastore_info

            if not datastore_info["accessible"]:
                result["valid"] = False
                result["errors"].append(f"Datastore not accessible: {datastore_name}")

            if datastore_info["free_gb"] < required_storage_gb:
                result["valid"] = False
                result["errors"].append(
                    f"Insufficient storage: need {required_storage_gb}GB, "
                    f"available {datastore_info['free_gb']:.1f}GB"
                )

            # Warning if less than 20% headroom
            headroom = (datastore_info["free_gb"] - required_storage_gb) / datastore_info["capacity_gb"]
            if headroom < 0.2:
                result["warnings"].append(
                    f"Low storage headroom: only {headroom*100:.1f}% free after deployment"
                )

        return result

    def get_vm_templates(self, datacenter_name: str) -> List[Dict[str, Any]]:
        """
        List all VM templates in a datacenter.

        Args:
            datacenter_name: Name of the datacenter

        Returns:
            List of template information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        vms = self._get_all_objs([vim.VirtualMachine], dc.vmFolder)
        templates = []

        for vm in vms:
            if vm.config and vm.config.template:
                templates.append({
                    "name": vm.name,
                    "guest_os": vm.config.guestFullName,
                    "vcpu": vm.config.hardware.numCPU,
                    "memory_mb": vm.config.hardware.memoryMB,
                })

        return templates

    def check_template_exists(
        self,
        datacenter_name: str,
        template_name: str,
    ) -> bool:
        """
        Check if a VM template exists.

        Args:
            datacenter_name: Name of the datacenter
            template_name: Name of the template

        Returns:
            True if template exists
        """
        templates = self.get_vm_templates(datacenter_name)
        return any(t["name"] == template_name for t in templates)

    def check_folder_exists(
        self,
        datacenter_name: str,
        folder_path: str,
    ) -> bool:
        """
        Check if a VM folder exists.

        Args:
            datacenter_name: Name of the datacenter
            folder_path: Full path to the folder

        Returns:
            True if folder exists
        """
        folders = self.list_folders(datacenter_name)
        return any(f["path"] == folder_path or f["name"] == folder_path for f in folders)

    def get_datacenter_summary(self, datacenter_name: str) -> Dict[str, Any]:
        """
        Get comprehensive summary of datacenter resources.

        Args:
            datacenter_name: Name of the datacenter

        Returns:
            Summary dictionary
        """
        clusters = self.list_clusters(datacenter_name)
        datastores = self.list_datastores(datacenter_name)
        networks = self.list_networks(datacenter_name)
        templates = self.get_vm_templates(datacenter_name)

        # Aggregate totals
        total_cpu_mhz = sum(c["cpu_total_mhz"] for c in clusters)
        available_cpu_mhz = sum(c["cpu_available_mhz"] for c in clusters)
        total_memory_gb = sum(c["memory_total_gb"] for c in clusters)
        available_memory_gb = sum(c["memory_available_gb"] for c in clusters)
        total_storage_gb = sum(d["capacity_gb"] for d in datastores)
        available_storage_gb = sum(d["free_gb"] for d in datastores)

        return {
            "datacenter": datacenter_name,
            "clusters": len(clusters),
            "datastores": len(datastores),
            "networks": len(networks),
            "templates": len(templates),
            "cpu_total_ghz": total_cpu_mhz / 1000,
            "cpu_available_ghz": available_cpu_mhz / 1000,
            "memory_total_gb": total_memory_gb,
            "memory_available_gb": available_memory_gb,
            "storage_total_tb": total_storage_gb / 1024,
            "storage_available_tb": available_storage_gb / 1024,
        }

    def template_exists(self, datacenter_name: str, template_path: str) -> bool:
        """
        Check if a VM template exists in vSphere.

        Args:
            datacenter_name: Name of the datacenter
            template_path: Full path to template (e.g., "Templates/gitea/haproxy-template")

        Returns:
            True if template exists, False otherwise
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        try:
            dc = self._get_obj([vim.Datacenter], datacenter_name)
            if not dc:
                return False

            # Search for VMs (templates are VMs with config.template=True)
            vms = self._get_all_objs([vim.VirtualMachine], dc.vmFolder)

            # Build full path for comparison
            for vm in vms:
                if vm.config and vm.config.template:
                    # Get VM's folder path
                    vm_path_parts = []
                    parent = vm.parent
                    while parent and parent != dc.vmFolder:
                        if isinstance(parent, vim.Folder):
                            vm_path_parts.insert(0, parent.name)
                        parent = parent.parent if hasattr(parent, 'parent') else None

                    vm_path_parts.append(vm.name)
                    vm_full_path = "/".join(vm_path_parts)

                    # Compare with requested path (handle different path formats)
                    if vm.name == template_path or vm_full_path == template_path or vm_full_path.endswith(template_path):
                        return True

            return False

        except Exception as e:
            self.logger.error(f"Error checking template existence: {e}")
            return False

    def list_vms_by_prefix(
        self,
        datacenter_name: str,
        prefix: str,
        folder_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List VMs matching a name prefix.

        Args:
            datacenter_name: Name of the datacenter
            prefix: VM name prefix to match
            folder_path: Optional folder path to search in

        Returns:
            List of VM information dictionaries
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        # Get all VMs (not templates)
        vms = self._get_all_objs([vim.VirtualMachine], dc.vmFolder)
        result = []

        for vm in vms:
            if vm.config and not vm.config.template and vm.name.startswith(prefix):
                # Get power state
                power_state = str(vm.runtime.powerState)
                # Get IP address if available
                ip_address = vm.guest.ipAddress if vm.guest else None

                result.append({
                    "name": vm.name,
                    "power_state": power_state,
                    "ip_address": ip_address,
                    "guest_os": vm.config.guestFullName if vm.config else None,
                    "vcpu": vm.config.hardware.numCPU if vm.config else None,
                    "memory_mb": vm.config.hardware.memoryMB if vm.config else None,
                })

        return result

    def check_vms_exist(
        self,
        datacenter_name: str,
        vm_prefixes: List[str],
        folder_path: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        """
        Check which VMs already exist for given prefixes.

        Args:
            datacenter_name: Name of the datacenter
            vm_prefixes: List of VM name prefixes to check (e.g., ["haproxy", "master", "worker"])
            folder_path: Optional folder path to search in (e.g., "/Datacenter/vm/GiteaInfra/Infra")

        Returns:
            Dictionary mapping prefixes to list of existing VM names
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        # Find the target folder if specified
        search_folder = dc.vmFolder
        if folder_path:
            # Navigate to the specified folder
            path_parts = [p for p in folder_path.split("/") if p and p != "vm" and p != datacenter_name]
            current = dc.vmFolder
            for part in path_parts:
                found = False
                for child in current.childEntity:
                    if isinstance(child, vim.Folder) and child.name == part:
                        current = child
                        found = True
                        break
                if not found:
                    # Folder not found, return empty results
                    return {prefix: [] for prefix in vm_prefixes}
            search_folder = current

        # Get VMs only from target folder (not recursive)
        result = {prefix: [] for prefix in vm_prefixes}
        for child in search_folder.childEntity:
            if isinstance(child, vim.VirtualMachine):
                if child.config and not child.config.template:
                    for prefix in vm_prefixes:
                        if child.name.startswith(prefix):
                            result[prefix].append(child.name)
                            break

        return result

    def get_vm_by_name(
        self,
        datacenter_name: str,
        vm_name: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get VM information by exact name.

        Args:
            datacenter_name: Name of the datacenter
            vm_name: Exact VM name

        Returns:
            VM information dictionary or None if not found
        """
        if not self.content:
            raise RuntimeError("Not connected to vCenter")

        dc = self._get_obj([vim.Datacenter], datacenter_name)
        if not dc:
            raise ValueError(f"Datacenter not found: {datacenter_name}")

        vm = self._get_obj([vim.VirtualMachine], vm_name, dc.vmFolder)
        if not vm or (vm.config and vm.config.template):
            return None

        return {
            "name": vm.name,
            "power_state": str(vm.runtime.powerState),
            "ip_address": vm.guest.ipAddress if vm.guest else None,
            "guest_os": vm.config.guestFullName if vm.config else None,
            "vcpu": vm.config.hardware.numCPU if vm.config else None,
            "memory_mb": vm.config.hardware.memoryMB if vm.config else None,
        }
