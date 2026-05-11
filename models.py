"""
SQLAlchemy models for AI Automated Experiment Platform (AI自动化实验平台)
"""
import enum
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


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


class LogType(str, enum.Enum):
    INSTALL = "install"
    VERIFY = "verify"
    AI_ANALYSIS = "ai_analysis"
    AI_FIX = "ai_fix"


class Project(db.Model):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    repo_url = db.Column(db.String(512), nullable=False)
    install_script = db.Column(db.Text, nullable=False)
    verify_script = db.Column(db.Text, nullable=False)
    config = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    runs = db.relationship("TestRun", backref="project", lazy="dynamic")

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
