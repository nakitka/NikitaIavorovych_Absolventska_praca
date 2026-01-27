"""
Shared utilities for Gitea Infrastructure Deployment.

Provides common functions for subprocess execution, logging, SSH operations,
template rendering, and other shared functionality.
"""

import os
import sys
import time
import socket
import secrets
import string
import subprocess
import logging
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, Template
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.table import Table

from . import constants

# Global console instance for Rich output
console = Console()


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """
    Set up logging with Rich formatting for console output.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional path to log file

    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger("gitea-deploy")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    logger.handlers = []

    # Rich console handler
    console_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
    )
    console_handler.setLevel(getattr(logging, log_level.upper()))
    logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    """Get the gitea-deploy logger instance."""
    return logging.getLogger("gitea-deploy")


def run_command(
    command: str | List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    capture_output: bool = True,
    check: bool = True,
    shell: bool = False,
    log_output: bool = True,
) -> subprocess.CompletedProcess:
    """
    Execute a shell command with proper error handling and logging.

    Args:
        command: Command to execute (string or list)
        cwd: Working directory
        env: Environment variables (merged with current env)
        timeout: Timeout in seconds
        capture_output: Whether to capture stdout/stderr
        check: Raise exception on non-zero exit
        shell: Execute through shell
        log_output: Whether to log command output

    Returns:
        subprocess.CompletedProcess with result

    Raises:
        subprocess.CalledProcessError: If command fails and check=True
        subprocess.TimeoutExpired: If command times out
    """
    logger = get_logger()

    # Prepare command
    if isinstance(command, str) and not shell:
        cmd_list = command.split()
    else:
        cmd_list = command

    # Log command
    cmd_str = command if isinstance(command, str) else " ".join(command)
    logger.debug(f"Executing: {cmd_str}")

    # Merge environment
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    try:
        result = subprocess.run(
            cmd_list if not shell else command,
            cwd=cwd,
            env=full_env,
            timeout=timeout,
            capture_output=capture_output,
            check=check,
            shell=shell,
            text=True,
        )

        if log_output and capture_output:
            if result.stdout:
                logger.debug(f"stdout: {result.stdout[:500]}...")
            if result.stderr:
                logger.debug(f"stderr: {result.stderr[:500]}...")

        return result

    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}")
        if e.stdout:
            logger.error(f"stdout: {e.stdout}")
        if e.stderr:
            logger.error(f"stderr: {e.stderr}")
        raise

    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out after {timeout} seconds")
        raise


def generate_password(length: int = 24, special_chars: bool = True) -> str:
    """
    Generate a cryptographically secure random password.

    Args:
        length: Length of password
        special_chars: Include special characters

    Returns:
        Random password string
    """
    alphabet = string.ascii_letters + string.digits
    if special_chars:
        # Use safe special characters that work well in configs and URLs
        # Avoid: % (URL encoding), & (shell), ' " (quoting), \ (escape)
        alphabet += "!@#^*-_=+"

    # Ensure at least one of each type
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
    ]
    if special_chars:
        password.append(secrets.choice("!@#$%^&*()-_=+"))

    # Fill remaining length
    password.extend(secrets.choice(alphabet) for _ in range(length - len(password)))

    # Shuffle
    password_list = list(password)
    secrets.SystemRandom().shuffle(password_list)

    return "".join(password_list)


def generate_ssh_keypair(
    key_path: str,
    key_type: str = "ed25519",
    comment: str = "gitea-deploy",
) -> Tuple[str, str]:
    """
    Generate an SSH key pair.

    Args:
        key_path: Path to save private key (public key will be {key_path}.pub)
        key_type: Key type (ed25519, rsa)
        comment: Comment for the key

    Returns:
        Tuple of (private_key_path, public_key_path)
    """
    logger = get_logger()

    key_path = Path(key_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing keys
    if key_path.exists():
        key_path.unlink()
    pub_key_path = Path(f"{key_path}.pub")
    if pub_key_path.exists():
        pub_key_path.unlink()

    # Generate key
    cmd = [
        "ssh-keygen",
        "-t", key_type,
        "-f", str(key_path),
        "-N", "",  # No passphrase
        "-C", comment,
    ]

    if key_type == "rsa":
        cmd.extend(["-b", "4096"])

    run_command(cmd)

    # Set permissions
    os.chmod(key_path, 0o600)
    os.chmod(pub_key_path, 0o644)

    logger.info(f"Generated SSH key pair: {key_path}")

    return str(key_path), str(pub_key_path)


def ip_range_to_list(start_ip: str, end_ip: str) -> List[str]:
    """
    Convert an IP range to a list of IP addresses.

    Args:
        start_ip: Starting IP address (e.g., "10.209.0.10")
        end_ip: Ending IP address (e.g., "10.209.0.19")

    Returns:
        List of IP addresses in the range (inclusive)
    """
    def ip_to_int(ip: str) -> int:
        parts = ip.split(".")
        return sum(int(part) << (8 * (3 - i)) for i, part in enumerate(parts))

    def int_to_ip(num: int) -> str:
        return ".".join(str((num >> (8 * i)) & 0xFF) for i in range(3, -1, -1))

    start_int = ip_to_int(start_ip)
    end_int = ip_to_int(end_ip)

    return [int_to_ip(i) for i in range(start_int, end_int + 1)]


def subnet_mask_to_cidr(mask: str) -> int:
    """
    Convert a subnet mask to CIDR prefix length.

    Args:
        mask: Subnet mask (e.g., "255.255.255.0")

    Returns:
        CIDR prefix length (e.g., 24)
    """
    octets = mask.split(".")
    binary = "".join(format(int(octet), "08b") for octet in octets)
    return binary.count("1")


def vsphere_path_to_relative(path: str, datacenter: str = None) -> str:
    """
    Convert an absolute vSphere path to a relative path.

    For Packer/Terraform, folder paths should be relative to the datacenter's
    VM folder, not absolute vSphere inventory paths.

    Args:
        path: Full vSphere path (e.g., "/dualDC/vm/GiteaInfra/Templates")
        datacenter: Optional datacenter name to strip

    Returns:
        Relative path (e.g., "GiteaInfra/Templates")

    Examples:
        "/dualDC/vm/GiteaInfra/Templates" -> "GiteaInfra/Templates"
        "/dualDC/vm/folder" -> "folder"
        "already/relative" -> "already/relative"
    """
    # Remove leading slash
    path = path.lstrip("/")

    # Common patterns to strip
    parts = path.split("/")

    # If path starts with datacenter name, remove it
    if datacenter and parts and parts[0] == datacenter:
        parts = parts[1:]

    # If next part is "vm", remove it (this is the VM folder root)
    if parts and parts[0] == "vm":
        parts = parts[1:]

    return "/".join(parts) if parts else ""


def wait_for_port(
    host: str,
    port: int,
    timeout: int = 300,
    interval: int = 5,
) -> bool:
    """
    Wait for a port to become available.

    Args:
        host: Hostname or IP address
        port: Port number
        timeout: Maximum time to wait in seconds
        interval: Time between checks in seconds

    Returns:
        True if port became available, False if timeout
    """
    logger = get_logger()
    logger.info(f"Waiting for {host}:{port} to become available...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                logger.info(f"{host}:{port} is now available")
                return True

        except socket.error:
            pass

        time.sleep(interval)

    logger.warning(f"Timeout waiting for {host}:{port}")
    return False


def wait_for_ssh(
    host: str,
    port: int = 22,
    username: str = "root",
    key_file: Optional[str] = None,
    timeout: int = 300,
    interval: int = 10,
    jump_host: Optional[str] = None,
) -> bool:
    """
    Wait for SSH to become available on a host.

    Args:
        host: Hostname or IP address
        port: SSH port
        username: SSH username
        key_file: Path to SSH private key
        timeout: Maximum time to wait in seconds
        interval: Time between checks in seconds
        jump_host: Optional jump host (bastion) to proxy through

    Returns:
        True if SSH became available, False if timeout
    """
    logger = get_logger()
    jump_info = f" via {jump_host}" if jump_host else ""
    logger.info(f"Waiting for SSH on {host}:{port}{jump_info}...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            cmd = [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                "-p", str(port),
            ]

            if key_file:
                cmd.extend(["-i", key_file])

            # Add jump host (bastion) if specified - use ProxyCommand to ensure key is used for both hops
            if jump_host:
                proxy_cmd = f"ssh -i {key_file} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -W %h:%p {username}@{jump_host}"
                cmd.extend(["-o", f"ProxyCommand={proxy_cmd}"])

            cmd.extend([f"{username}@{host}", "echo", "SSH_OK"])

            result = run_command(cmd, check=False, log_output=False)

            if result.returncode == 0 and "SSH_OK" in result.stdout:
                logger.info(f"SSH available on {host}:{port}")
                return True

        except Exception:
            pass

        time.sleep(interval)

    logger.warning(f"Timeout waiting for SSH on {host}:{port}")
    return False


def render_template(
    template_path: str,
    output_path: str,
    context: Dict[str, Any],
    templates_dir: Optional[str] = None,
) -> str:
    """
    Render a Jinja2 template to a file.

    Args:
        template_path: Path to template file (relative to templates_dir)
        output_path: Path to output file
        context: Template context variables
        templates_dir: Base directory for templates

    Returns:
        Path to rendered file
    """
    logger = get_logger()

    if templates_dir is None:
        templates_dir = Path(__file__).parent.parent / "templates"

    templates_dir = Path(templates_dir)

    # Set up Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    # Add custom filters
    env.filters["to_yaml"] = lambda x: __import__("yaml").dump(x, default_flow_style=False)
    env.filters["hash"] = lambda x, algo="sha256": __import__("hashlib").new(algo, x.encode()).hexdigest()
    env.filters["subnet_to_cidr"] = subnet_mask_to_cidr
    env.filters["vsphere_relative"] = vsphere_path_to_relative

    # Load and render template
    template = env.get_template(template_path)
    rendered = template.render(**context)

    # Write output
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered)

    logger.debug(f"Rendered template: {template_path} -> {output_path}")

    return str(output_path)


def render_template_string(template_string: str, context: Dict[str, Any]) -> str:
    """
    Render a Jinja2 template string.

    Args:
        template_string: Template content as string
        context: Template context variables

    Returns:
        Rendered template string
    """
    template = Template(template_string)
    return template.render(**context)


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent


def ensure_directory(path: str | Path) -> Path:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path

    Returns:
        Path object for the directory
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_file(path: str | Path, content: str, mode: int = 0o644) -> Path:
    """
    Write content to a file with specified permissions.

    Args:
        path: File path
        content: File content
        mode: File permissions

    Returns:
        Path object for the file
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.chmod(path, mode)
    return path


def read_file(path: str | Path) -> str:
    """
    Read content from a file.

    Args:
        path: File path

    Returns:
        File content
    """
    return Path(path).read_text()


def timestamp() -> str:
    """Get current timestamp in standard format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def timestamp_filename() -> str:
    """Get current timestamp suitable for filenames."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def display_panel(title: str, content: str, style: str = "green") -> None:
    """
    Display a Rich panel with content.

    Args:
        title: Panel title
        content: Panel content
        style: Panel border style/color
    """
    console.print(Panel(content, title=title, border_style=style))


def display_table(title: str, columns: List[str], rows: List[List[str]]) -> None:
    """
    Display a Rich table.

    Args:
        title: Table title
        columns: Column headers
        rows: Table rows
    """
    table = Table(title=title)

    for col in columns:
        table.add_column(col)

    for row in rows:
        table.add_row(*[str(cell) for cell in row])

    console.print(table)


def confirm_action(prompt: str, default: bool = False) -> bool:
    """
    Ask user to confirm an action.

    Args:
        prompt: Confirmation prompt
        default: Default value if user just presses Enter

    Returns:
        True if user confirms, False otherwise
    """
    sys.stdout.flush()
    # Strip leading/trailing whitespace from prompt
    prompt = prompt.strip()
    default_str = "[Y/n]" if default else "[y/N]"
    response = console.input(f"{prompt} {default_str}: ").strip().lower()

    if not response:
        return default

    return response in ("y", "yes")


def progress_bar(description: str = "Processing"):
    """
    Create a Rich progress bar context manager.

    Args:
        description: Progress bar description

    Returns:
        Progress context manager
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    )


def check_tool_installed(tool: str) -> bool:
    """
    Check if a command-line tool is installed.

    Args:
        tool: Tool name

    Returns:
        True if installed, False otherwise
    """
    try:
        result = run_command(["which", tool], check=False, log_output=False)
        return result.returncode == 0
    except Exception:
        return False


def get_tool_version(tool: str, version_flag: str = "--version") -> Optional[str]:
    """
    Get the version of an installed tool.

    Args:
        tool: Tool name
        version_flag: Flag to get version

    Returns:
        Version string or None if not found
    """
    try:
        result = run_command([tool, version_flag], check=False, log_output=False)
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0]
    except Exception:
        pass
    return None


def interactive_pause(message: str = "Press Enter to continue...") -> str:
    """
    Pause execution and wait for user input.

    Args:
        message: Message to display

    Returns:
        User input (typically empty string or command)
    """
    sys.stdout.flush()
    return console.input(f"\n{message}")
