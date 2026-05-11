"""
VMware Workstation Manager – wraps `vmrun` CLI for VM lifecycle management.

Provides:
  - VM clone from template
  - Start / stop / suspend
  - Snapshot create / revert / delete
  - Guest OS operations (run program, copy file, SSH)
  - Network configuration via guest scripts

Falls back to **simulation mode** when vmrun is not available or
VMWARE_SIMULATION=1 is set.  Simulation mode emits realistic logs but
does not touch any real VM.

Design doc references:
  - Section 2.1: 基础设施层 – VMware Workstation + vmrun
  - Section 4.1: 虚拟机管理
"""
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class VMwareConfig:
    """Global VMware Workstation configuration."""
    vmrun_path: str = ""                  # path to vmrun binary; auto-detected if empty
    vmware_host_type: str = "ws"          # ws (Workstation) | fusion | player
    default_template_dir: str = ""        # directory containing .vmx template files
    default_clone_dir: str = ""           # where cloned VMs are stored
    default_snapshot_name: str = "clean-snapshot"
    ssh_user: str = "root"
    ssh_password: str = ""                # password for SSH auth (used when no key)
    ssh_port: int = 22                    # SSH port on guest
    ssh_key_path: str = ""                # path to private key for guest SSH
    ssh_timeout: int = 120                # seconds to wait for SSH ready
    simulation: bool = False              # force simulation mode

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class VMState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


@dataclass
class VMInfo:
    """Runtime info for a managed VM instance."""
    name: str = ""
    vmx_path: str = ""              # full path to .vmx file
    template_vmx: str = ""          # template it was cloned from
    state: str = VMState.STOPPED.value
    ip: str = ""
    hostname: str = ""
    specs: dict = field(default_factory=lambda: {"cpu": 4, "memory": 8, "disk": 100})
    snapshot_name: str = ""
    port: int = 8080
    created_at: str = ""

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# vmrun wrapper
# ---------------------------------------------------------------------------

class VMRunError(Exception):
    """Raised when a vmrun command fails."""
    pass


def _detect_vmrun() -> str:
    """Try to find vmrun on the system."""
    # Common locations
    candidates = [
        shutil.which("vmrun"),
        "/usr/bin/vmrun",
        "C:\\Program Files (x86)\\VMware\\VMware Workstation\\vmrun.exe",
        "C:\\Program Files\\VMware\\VMware Workstation\\vmrun.exe",
        "/Applications/VMware Fusion.app/Contents/Library/vmrun",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


class VMRunCLI:
    """
    Low-level wrapper around the `vmrun` command-line tool.

    All methods raise VMRunError on failure.
    """

    def __init__(self, vmrun_path: str = "", host_type: str = "ws"):
        self.vmrun_path = vmrun_path or _detect_vmrun()
        self.host_type = host_type

    @property
    def available(self) -> bool:
        return bool(self.vmrun_path) and os.path.isfile(self.vmrun_path)

    def _run(self, *args, timeout: int = 300) -> str:
        """Execute a vmrun command and return stdout."""
        if not self.available:
            raise VMRunError("vmrun not found")
        cmd = [self.vmrun_path, "-T", self.host_type] + list(args)
        log.info("vmrun: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                raise VMRunError(
                    f"vmrun failed (rc={result.returncode}): {result.stderr.strip()}"
                )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise VMRunError(f"vmrun timed out after {timeout}s")
        except FileNotFoundError:
            raise VMRunError(f"vmrun binary not found: {self.vmrun_path}")

    # --- VM lifecycle ---

    def list_running(self) -> list[str]:
        """Return list of .vmx paths for running VMs."""
        out = self._run("list")
        lines = out.splitlines()
        # First line is "Total running VMs: N", rest are paths
        return [l.strip() for l in lines[1:] if l.strip()]

    def start(self, vmx_path: str, gui: bool = False):
        mode = "gui" if gui else "nogui"
        self._run("start", vmx_path, mode)

    def stop(self, vmx_path: str, hard: bool = False):
        mode = "hard" if hard else "soft"
        self._run("stop", vmx_path, mode)

    def suspend(self, vmx_path: str):
        self._run("suspend", vmx_path)

    def reset(self, vmx_path: str, hard: bool = False):
        mode = "hard" if hard else "soft"
        self._run("reset", vmx_path, mode)

    def delete(self, vmx_path: str):
        self._run("deleteVM", vmx_path)

    # --- Cloning ---

    def clone(self, template_vmx: str, dest_vmx: str, clone_type: str = "linked",
              snapshot_name: str = ""):
        """Clone a VM.  clone_type: linked | full"""
        args = ["clone", template_vmx, dest_vmx, clone_type]
        if snapshot_name:
            args.append(f"-cloneName={snapshot_name}")
        self._run(*args, timeout=600)

    # --- Snapshots ---

    def snapshot_create(self, vmx_path: str, snap_name: str):
        self._run("snapshot", vmx_path, snap_name)

    def snapshot_revert(self, vmx_path: str, snap_name: str):
        self._run("revertToSnapshot", vmx_path, snap_name)

    def snapshot_delete(self, vmx_path: str, snap_name: str):
        self._run("deleteSnapshot", vmx_path, snap_name)

    def snapshot_list(self, vmx_path: str) -> list[str]:
        out = self._run("listSnapshots", vmx_path)
        lines = out.splitlines()
        return [l.strip() for l in lines[1:] if l.strip()]

    # --- Guest operations ---

    def get_guest_ip(self, vmx_path: str) -> str:
        """Get the IP address of the guest OS."""
        return self._run("getGuestIPAddress", vmx_path, "-wait")

    def run_program_in_guest(self, vmx_path: str, program: str,
                             args: str = "", interactive: bool = False,
                             user: str = "", password: str = ""):
        """Run a program inside the guest OS."""
        cmd_args = ["runProgramInGuest", vmx_path]
        if user:
            cmd_args.extend(["-gu", user])
        if password:
            cmd_args.extend(["-gp", password])
        if not interactive:
            cmd_args.append("-noWait")
        cmd_args.extend([program, args])
        return self._run(*cmd_args, timeout=600)

    def run_script_in_guest(self, vmx_path: str, interpreter: str, script: str,
                            user: str = "", password: str = ""):
        """Run a script inside the guest OS."""
        cmd_args = ["runScriptInGuest", vmx_path]
        if user:
            cmd_args.extend(["-gu", user])
        if password:
            cmd_args.extend(["-gp", password])
        cmd_args.extend([interpreter, script])
        return self._run(*cmd_args, timeout=600)

    def copy_file_to_guest(self, vmx_path: str, host_path: str, guest_path: str,
                           user: str = "", password: str = ""):
        cmd_args = ["copyFileFromHostToGuest", vmx_path]
        if user:
            cmd_args.extend(["-gu", user])
        if password:
            cmd_args.extend(["-gp", password])
        cmd_args.extend([host_path, guest_path])
        self._run(*cmd_args, timeout=300)

    def copy_file_from_guest(self, vmx_path: str, guest_path: str, host_path: str,
                             user: str = "", password: str = ""):
        cmd_args = ["copyFileFromGuestToHost", vmx_path]
        if user:
            cmd_args.extend(["-gu", user])
        if password:
            cmd_args.extend(["-gp", password])
        cmd_args.extend([guest_path, host_path])
        self._run(*cmd_args, timeout=300)

    def file_exists_in_guest(self, vmx_path: str, guest_path: str,
                             user: str = "", password: str = "") -> bool:
        try:
            cmd_args = ["fileExistsInGuest", vmx_path]
            if user:
                cmd_args.extend(["-gu", user])
            if password:
                cmd_args.extend(["-gp", password])
            cmd_args.append(guest_path)
            self._run(*cmd_args)
            return True
        except VMRunError:
            return False


# ---------------------------------------------------------------------------
# High-level VM Manager
# ---------------------------------------------------------------------------

class VMwareManager:
    """
    High-level VM lifecycle manager.

    Automatically falls back to simulation mode if vmrun is not available.
    """

    def __init__(self, config: Optional[VMwareConfig] = None):
        self.config = config or VMwareConfig()
        self._cli = VMRunCLI(self.config.vmrun_path, self.config.vmware_host_type)
        self._simulation = self.config.simulation or not self._cli.available

        if self._simulation:
            log.warning("VMware Manager running in SIMULATION mode")
        else:
            log.info("VMware Manager using vmrun at: %s", self._cli.vmrun_path)

    @property
    def is_simulation(self) -> bool:
        return self._simulation

    # --- Template management ---

    def list_templates(self) -> list[dict]:
        """List available VM templates (.vmx files in template dir)."""
        tpl_dir = self.config.default_template_dir
        if not tpl_dir or not os.path.isdir(tpl_dir):
            if self._simulation:
                return [
                    {"name": "Ubuntu-22.04-Template", "vmx_path": "/vmware/templates/ubuntu-22.04/ubuntu.vmx",
                     "os": "Ubuntu 22.04 LTS", "cpu": 4, "memory": 8, "disk": 100},
                    {"name": "CentOS-7-Template", "vmx_path": "/vmware/templates/centos-7/centos.vmx",
                     "os": "CentOS 7.9", "cpu": 2, "memory": 4, "disk": 50},
                    {"name": "Windows-Server-2022", "vmx_path": "/vmware/templates/win2022/win2022.vmx",
                     "os": "Windows Server 2022", "cpu": 4, "memory": 16, "disk": 200},
                ]
            return []

        templates = []
        for vmx in Path(tpl_dir).rglob("*.vmx"):
            name = vmx.parent.name or vmx.stem
            templates.append({
                "name": name,
                "vmx_path": str(vmx),
                "os": "Unknown",
                "cpu": 0, "memory": 0, "disk": 0,
            })
        return templates

    # --- VM lifecycle ---

    def clone_vm(self, template_vmx: str, vm_name: str,
                 clone_type: str = "linked") -> VMInfo:
        """Clone a VM from a template and return VMInfo."""
        clone_dir = self.config.default_clone_dir or "/tmp/vmware-clones"
        dest_dir = os.path.join(clone_dir, vm_name)
        dest_vmx = os.path.join(dest_dir, f"{vm_name}.vmx")

        info = VMInfo(
            name=vm_name,
            vmx_path=dest_vmx,
            template_vmx=template_vmx,
            state=VMState.STOPPED.value,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        if self._simulation:
            import random
            info.ip = f"10.0.1.{random.randint(10, 250)}"
            info.hostname = vm_name
            info.state = VMState.STOPPED.value
            log.info("[SIM] Cloned VM %s from %s", vm_name, template_vmx)
            return info

        os.makedirs(dest_dir, exist_ok=True)
        self._cli.clone(template_vmx, dest_vmx, clone_type)
        return info

    def start_vm(self, info: VMInfo, gui: bool = False) -> VMInfo:
        """Start a VM and wait for IP."""
        if self._simulation:
            import random
            info.state = VMState.RUNNING.value
            if not info.ip:
                info.ip = f"10.0.1.{random.randint(10, 250)}"
            log.info("[SIM] Started VM %s -> %s", info.name, info.ip)
            return info

        self._cli.start(info.vmx_path, gui)
        info.state = VMState.RUNNING.value
        # Wait for guest IP
        try:
            info.ip = self._cli.get_guest_ip(info.vmx_path)
        except VMRunError:
            log.warning("Could not get guest IP for %s", info.name)
        return info

    def stop_vm(self, info: VMInfo, hard: bool = False) -> VMInfo:
        if self._simulation:
            info.state = VMState.STOPPED.value
            log.info("[SIM] Stopped VM %s", info.name)
            return info

        self._cli.stop(info.vmx_path, hard)
        info.state = VMState.STOPPED.value
        return info

    def delete_vm(self, info: VMInfo):
        if self._simulation:
            log.info("[SIM] Deleted VM %s", info.name)
            return

        try:
            self._cli.stop(info.vmx_path, hard=True)
        except VMRunError:
            pass
        self._cli.delete(info.vmx_path)

    # --- Snapshot ---

    def create_snapshot(self, info: VMInfo,
                        snap_name: str = "") -> str:
        snap_name = snap_name or self.config.default_snapshot_name
        if self._simulation:
            import random
            info.snapshot_name = snap_name
            snap_id = f"snap-{random.randint(100000, 999999)}"
            log.info("[SIM] Created snapshot %s (%s) on %s", snap_name, snap_id, info.name)
            return snap_id

        self._cli.snapshot_create(info.vmx_path, snap_name)
        info.snapshot_name = snap_name
        return snap_name

    def revert_snapshot(self, info: VMInfo,
                        snap_name: str = ""):
        snap_name = snap_name or self.config.default_snapshot_name
        if self._simulation:
            log.info("[SIM] Reverted VM %s to snapshot %s", info.name, snap_name)
            return

        self._cli.snapshot_revert(info.vmx_path, snap_name)

    def list_snapshots(self, info: VMInfo) -> list[str]:
        if self._simulation:
            return [self.config.default_snapshot_name]
        return self._cli.snapshot_list(info.vmx_path)

    # --- Guest operations ---

    def wait_for_ssh(self, info: VMInfo, timeout: int = 0) -> bool:
        """Wait until SSH is available on the guest."""
        timeout = timeout or self.config.ssh_timeout
        if self._simulation:
            log.info("[SIM] SSH ready on %s (%s)", info.name, info.ip)
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ip = self._cli.get_guest_ip(info.vmx_path)
                if ip:
                    info.ip = ip
                    return True
            except VMRunError:
                pass
            time.sleep(5)
        return False

    def run_guest_script(self, info: VMInfo, script: str,
                         interpreter: str = "/bin/bash") -> str:
        """Run a script inside the guest via vmrun."""
        if self._simulation:
            log.info("[SIM] Running script on %s: %s...", info.name, script[:80])
            return "[SIM] Script executed successfully"

        return self._cli.run_script_in_guest(
            info.vmx_path, interpreter, script,
            user=self.config.ssh_user
        )

    def upload_to_guest(self, info: VMInfo, host_path: str, guest_path: str):
        if self._simulation:
            log.info("[SIM] Upload %s -> %s:%s", host_path, info.name, guest_path)
            return

        self._cli.copy_file_to_guest(
            info.vmx_path, host_path, guest_path,
            user=self.config.ssh_user
        )

    def download_from_guest(self, info: VMInfo, guest_path: str, host_path: str):
        if self._simulation:
            log.info("[SIM] Download %s:%s -> %s", info.name, guest_path, host_path)
            return

        self._cli.copy_file_from_guest(
            info.vmx_path, guest_path, host_path,
            user=self.config.ssh_user
        )

    # --- SSH remote execution (alternative to vmrun guest ops) ---

    def _ssh_common_opts(self) -> list:
        """Build common SSH options (port, host-key, timeout)."""
        opts = [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
        ]
        port = self.config.ssh_port or 22
        if port != 22:
            opts.extend(["-p", str(port)])
        if self.config.ssh_key_path:
            opts.extend(["-i", self.config.ssh_key_path])
        return opts

    def _wrap_sshpass(self, cmd: list) -> list:
        """Prepend sshpass if password auth is configured and no key is set."""
        if self.config.ssh_password and not self.config.ssh_key_path:
            return ["sshpass", "-p", self.config.ssh_password] + cmd
        return cmd

    def ssh_exec(self, info: VMInfo, command: str, timeout: int = 300) -> tuple:
        """Execute a command via SSH. Returns (returncode, stdout, stderr)."""
        if self._simulation:
            log.info("[SIM] SSH exec on %s: %s", info.name, command[:80])
            return (0, "[SIM] OK", "")

        ssh_cmd = self._wrap_sshpass(
            ["ssh"] + self._ssh_common_opts() + [
                f"{self.config.ssh_user}@{info.ip}",
                command,
            ]
        )
        try:
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return (result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired:
            return (-1, "", f"SSH command timed out after {timeout}s")

    def scp_upload(self, info: VMInfo, local_path: str, remote_path: str):
        """Upload a file/directory via SCP."""
        if self._simulation:
            log.info("[SIM] SCP upload %s -> %s:%s", local_path, info.ip, remote_path)
            return

        port = self.config.ssh_port or 22
        scp_opts = [
            "-r",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
        ]
        if port != 22:
            scp_opts.extend(["-P", str(port)])
        if self.config.ssh_key_path:
            scp_opts.extend(["-i", self.config.ssh_key_path])

        scp_cmd = self._wrap_sshpass(["scp"] + scp_opts + [
            local_path, f"{self.config.ssh_user}@{info.ip}:{remote_path}"
        ])
        subprocess.run(scp_cmd, capture_output=True, text=True, timeout=120, check=True,
                       encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Singleton / factory
# ---------------------------------------------------------------------------

_manager: Optional[VMwareManager] = None


def get_vmware_manager(config: Optional[VMwareConfig] = None) -> VMwareManager:
    global _manager
    if _manager is None:
        _manager = VMwareManager(config)
    return _manager
