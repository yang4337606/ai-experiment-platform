"""
SQLAlchemy models for AI Automated Experiment Platform (AI自动化实验平台)
"""
import enum
import os
from datetime import datetime

from cryptography.fernet import Fernet
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ---------------------------------------------------------------------------
# Encryption helpers – derive a stable Fernet key from an env var or generate
# one and persist it beside the DB file.
# ---------------------------------------------------------------------------
_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".credential_key")


def _load_fernet_key() -> bytes:
    env_key = os.environ.get("CREDENTIAL_KEY")
    if env_key:
        return env_key.encode()
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    with open(_KEY_FILE, "wb") as f:
        f.write(key)
    return key


_fernet = Fernet(_load_fernet_key())


def encrypt_value(plain: str) -> str:
    """Encrypt a plaintext string and return a base64 token."""
    if not plain:
        return ""
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_value(token: str) -> str:
    """Decrypt a Fernet token back to plaintext."""
    if not token:
        return ""
    return _fernet.decrypt(token.encode()).decode()


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    INIT_VM = "init_vm"
    SNAPSHOT = "snapshot"
    CODE_PULL = "code_pull"
    UPLOAD = "upload"
    INSTALL = "install"
    VERIFY = "verify"
    AI_ANALYZE = "ai_analyze"
    AI_FIX = "ai_fix"
    ROLLBACK = "rollback"
    SUCCESS = "success"
    FAILED = "failed"


class FailureCode(str, enum.Enum):
    """设计文档 7.2 节 – 失败分类枚举"""
    ENV_ERROR = "ENV_ERROR"              # 环境问题（VM/磁盘/系统配置）
    SCRIPT_ERROR = "SCRIPT_ERROR"        # 脚本问题（语法/权限/路径）
    PACKAGE_ERROR = "PACKAGE_ERROR"      # 安装包问题（损坏/版本/依赖缺失）
    NETWORK_ERROR = "NETWORK_ERROR"      # 网络问题（下载超时/DNS）
    CONFIG_ERROR = "CONFIG_ERROR"        # 配置问题（配置文件/端口冲突/参数）
    SERVICE_ERROR = "SERVICE_ERROR"      # 服务启动失败
    AI_FIX_FAILED = "AI_FIX_FAILED"    # AI 修复失败（达到重试上限）
    UNKNOWN_ERROR = "UNKNOWN_ERROR"      # 未知错误

    @classmethod
    def from_scenario_type(cls, scenario_type: str):
        """Map workflow scenario type to FailureCode."""
        mapping = {
            "port_conflict": cls.CONFIG_ERROR,
            "missing_dependency": cls.PACKAGE_ERROR,
            "permission_error": cls.SCRIPT_ERROR,
            "config_error": cls.CONFIG_ERROR,
        }
        return mapping.get(scenario_type, cls.UNKNOWN_ERROR)


class LogType(str, enum.Enum):
    INSTALL = "install"
    VERIFY = "verify"
    AI_ANALYSIS = "ai_analysis"
    AI_FIX = "ai_fix"
    CREDIT = "credit"          # 积分相关日志
    ACCOUNT_SWITCH = "account_switch"  # 账户切换日志


class Project(db.Model):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    repo_url = db.Column(db.String(512), nullable=False)
    install_script = db.Column(db.Text, nullable=False)
    verify_script = db.Column(db.Text, nullable=False)
    config = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    runs = db.relationship("TestRun", backref="project", lazy="dynamic", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "repo_url": self.repo_url,
            "install_script": self.install_script,
            "verify_script": self.verify_script,
            "config": self.config or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TestRun(db.Model):
    __tablename__ = "test_run"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    status = db.Column(db.String(32), default=RunStatus.PENDING.value)
    branch_name = db.Column(db.String(256))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    retry_count = db.Column(db.Integer, default=10)       # max retries allowed
    current_retry = db.Column(db.Integer, default=0)      # retries used so far
    failure_code = db.Column(db.String(32), default="")    # FailureCode enum value
    vm_info = db.Column(db.JSON, default=dict)            # ip, hostname, specs
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    logs = db.relationship("RunLog", backref="run", lazy="dynamic", cascade="all, delete-orphan")
    analyses = db.relationship("AIAnalysis", backref="run", lazy="dynamic", cascade="all, delete-orphan")
    verify_results = db.relationship("VerifyResult", backref="run", lazy="dynamic", cascade="all, delete-orphan")
    report = db.relationship("TestReport", backref="run", uselist=False, cascade="all, delete-orphan")

    def to_dict(self, include_related=False):
        data = {
            "id": self.id,
            "project_id": self.project_id,
            "project_name": self.project.name if self.project else None,
            "status": self.status,
            "failure_code": self.failure_code or "",
            "branch_name": self.branch_name,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "retry_count": self.retry_count,
            "current_retry": self.current_retry,
            "vm_info": self.vm_info or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_related:
            data["logs"] = [l.to_dict() for l in self.logs.order_by(RunLog.created_at)]
            data["analyses"] = [a.to_dict() for a in self.analyses.order_by(AIAnalysis.round_number)]
            data["verify_results"] = [v.to_dict() for v in self.verify_results.order_by(VerifyResult.round_number)]
            data["report"] = self.report.to_dict() if self.report else None
        return data


class RunLog(db.Model):
    __tablename__ = "run_log"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("test_run.id"), nullable=False)
    round_number = db.Column(db.Integer, default=1)
    phase = db.Column(db.String(64))
    content = db.Column(db.Text)
    log_type = db.Column(db.String(32), default=LogType.INSTALL.value)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "run_id": self.run_id,
            "round_number": self.round_number,
            "phase": self.phase,
            "content": self.content,
            "log_type": self.log_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AIAnalysis(db.Model):
    __tablename__ = "ai_analysis"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("test_run.id"), nullable=False)
    round_number = db.Column(db.Integer, default=1)
    root_cause = db.Column(db.Text)
    fix_plan = db.Column(db.Text)
    files_modified = db.Column(db.JSON, default=list)
    commit_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "run_id": self.run_id,
            "round_number": self.round_number,
            "root_cause": self.root_cause,
            "fix_plan": self.fix_plan,
            "files_modified": self.files_modified or [],
            "commit_message": self.commit_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class VerifyResult(db.Model):
    __tablename__ = "verify_result"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("test_run.id"), nullable=False)
    round_number = db.Column(db.Integer, default=1)
    check_name = db.Column(db.String(64))   # exit_code / log_keywords / service_status / port_listen / api_health
    passed = db.Column(db.Boolean, default=False)
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "run_id": self.run_id,
            "round_number": self.round_number,
            "check_name": self.check_name,
            "passed": self.passed,
            "detail": self.detail,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TestReport(db.Model):
    __tablename__ = "test_report"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("test_run.id"), nullable=False, unique=True)
    summary = db.Column(db.Text)
    ai_fixes = db.Column(db.JSON, default=list)       # list of fix summaries
    verify_results = db.Column(db.JSON, default=dict) # snapshot of final verify results
    branch_url = db.Column(db.String(512))
    commits = db.Column(db.JSON, default=list)        # list of commit records
    final_status = db.Column(db.String(32))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "run_id": self.run_id,
            "summary": self.summary,
            "ai_fixes": self.ai_fixes or [],
            "verify_results": self.verify_results or {},
            "branch_url": self.branch_url,
            "commits": self.commits or [],
            "final_status": self.final_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ServiceCredential(db.Model):
    """Account pool – multiple accounts per service type with priority & credits."""
    __tablename__ = "service_credential"

    id = db.Column(db.Integer, primary_key=True)
    service_type = db.Column(db.String(32), nullable=False, index=True)    # github | mulerun | chatgpt | qwen
    account = db.Column(db.String(256), default="")                        # username / email
    encrypted_secret = db.Column(db.Text, default="")                      # password / token (Fernet encrypted)
    priority = db.Column(db.Integer, default=0)                            # lower = higher priority
    credit_balance = db.Column(db.Float, default=0.0)                      # current credit balance
    credit_threshold = db.Column(db.Float, default=10.0)                   # switch when balance < threshold
    extra = db.Column(db.JSON, default=dict)                               # api_base, model, etc.
    enabled = db.Column(db.Boolean, default=True)
    last_used_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    SERVICE_TYPES = ("github", "mulerun", "chatgpt", "qwen")
    # Failover chain for AI providers (设计文档 4.4.4)
    AI_FAILOVER_CHAIN = ("mulerun", "chatgpt", "qwen")

    def _mask_secret(self) -> str:
        if not self.encrypted_secret:
            return ""
        try:
            raw = decrypt_value(self.encrypted_secret)
            return "••••••••" + raw[-4:] if len(raw) > 4 else "••••"
        except Exception:
            return "••••"

    def to_dict(self, unmask=False):
        secret = ""
        if self.encrypted_secret:
            if unmask:
                try:
                    secret = decrypt_value(self.encrypted_secret)
                except Exception:
                    secret = ""
            else:
                secret = self._mask_secret()
        return {
            "id": self.id,
            "service_type": self.service_type,
            "account": self.account or "",
            "secret_masked": secret,
            "priority": self.priority,
            "credit_balance": self.credit_balance,
            "credit_threshold": self.credit_threshold,
            "extra": self.extra or {},
            "enabled": self.enabled,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CreditLog(db.Model):
    """Tracks credit consumption and account switching events."""
    __tablename__ = "credit_log"

    id = db.Column(db.Integer, primary_key=True)
    credential_id = db.Column(db.Integer, db.ForeignKey("service_credential.id"), nullable=False)
    run_id = db.Column(db.Integer, db.ForeignKey("test_run.id"), nullable=True)
    event_type = db.Column(db.String(32))   # deduct | switch | alert | recharge
    amount = db.Column(db.Float, default=0.0)
    balance_after = db.Column(db.Float, default=0.0)
    detail = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    credential = db.relationship("ServiceCredential", backref="credit_logs")

    def to_dict(self):
        return {
            "id": self.id,
            "credential_id": self.credential_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "amount": self.amount,
            "balance_after": self.balance_after,
            "detail": self.detail,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ProgressContext(db.Model):
    """Stores AI conversation context for progress sync when switching accounts."""
    __tablename__ = "progress_context"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("test_run.id"), nullable=False)
    round_number = db.Column(db.Integer, default=1)
    from_credential_id = db.Column(db.Integer)    # account switched FROM
    to_credential_id = db.Column(db.Integer)      # account switched TO
    task_summary = db.Column(db.Text, default="") # current task description
    completed_steps = db.Column(db.JSON, default=list)  # steps done so far
    pending_issues = db.Column(db.JSON, default=list)   # issues remaining
    context_prompt = db.Column(db.Text, default="")     # generated prompt for new session
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "run_id": self.run_id,
            "round_number": self.round_number,
            "from_credential_id": self.from_credential_id,
            "to_credential_id": self.to_credential_id,
            "task_summary": self.task_summary,
            "completed_steps": self.completed_steps or [],
            "pending_issues": self.pending_issues or [],
            "context_prompt": self.context_prompt,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class VMwareConfigModel(db.Model):
    """Stores VMware Workstation configuration in the database."""
    __tablename__ = "vmware_config"

    id = db.Column(db.Integer, primary_key=True)
    vmrun_path = db.Column(db.String(512), default="")
    vmware_host_type = db.Column(db.String(16), default="ws")        # ws | fusion | player
    default_template_dir = db.Column(db.String(512), default="")
    default_clone_dir = db.Column(db.String(512), default="")
    default_snapshot_name = db.Column(db.String(128), default="clean-snapshot")
    ssh_user = db.Column(db.String(64), default="root")
    ssh_key_path = db.Column(db.String(512), default="")
    ssh_timeout = db.Column(db.Integer, default=120)
    simulation = db.Column(db.Boolean, default=True)                  # default to simulation
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "vmrun_path": self.vmrun_path,
            "vmware_host_type": self.vmware_host_type,
            "default_template_dir": self.default_template_dir,
            "default_clone_dir": self.default_clone_dir,
            "default_snapshot_name": self.default_snapshot_name,
            "ssh_user": self.ssh_user,
            "ssh_key_path": self.ssh_key_path,
            "ssh_timeout": self.ssh_timeout,
            "simulation": self.simulation,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_vmware_config(self):
        """Convert to vmware_manager.VMwareConfig dataclass."""
        from vmware_manager import VMwareConfig
        return VMwareConfig(
            vmrun_path=self.vmrun_path or "",
            vmware_host_type=self.vmware_host_type or "ws",
            default_template_dir=self.default_template_dir or "",
            default_clone_dir=self.default_clone_dir or "",
            default_snapshot_name=self.default_snapshot_name or "clean-snapshot",
            ssh_user=self.ssh_user or "root",
            ssh_key_path=self.ssh_key_path or "",
            ssh_timeout=self.ssh_timeout or 120,
            simulation=self.simulation if self.simulation is not None else True,
        )


class NotifyConfig(db.Model):
    __tablename__ = "notify_config"

    id = db.Column(db.Integer, primary_key=True)
    webhook_url = db.Column(db.String(512), default="")
    enabled = db.Column(db.Boolean, default=False)
    notify_on_success = db.Column(db.Boolean, default=True)
    notify_on_failure = db.Column(db.Boolean, default=True)
    notify_on_error = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            "id": self.id,
            "webhook_url": self.webhook_url,
            "enabled": self.enabled,
            "notify_on_success": self.notify_on_success,
            "notify_on_failure": self.notify_on_failure,
            "notify_on_error": self.notify_on_error,
        }
