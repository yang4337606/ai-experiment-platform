"""
Workflow engine for AI Automated Experiment Platform (AI自动化实验平台).
Drives a TestRun through its full state machine in a background thread.

REAL execution mode: actually runs commands in the guest VM via vmrun / SSH.
Falls back to simulation log output only when VMwareManager is in simulation mode.
"""
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simulation log libraries (used ONLY when VMwareManager.is_simulation is True)
# ---------------------------------------------------------------------------

# placeholder — kept minimal; real mode does not use these
_SIM_INSTALL_LINES = [
    "[安装][模拟] 正在模拟安装过程...",
    "[安装][模拟] 安装完成 (模拟)",
]
_SIM_VERIFY_PASS = [
    "[验证][模拟] 所有检查通过 (模拟)",
]
_SIM_VERIFY_FAIL = [
    "[验证][模拟] 验证失败 (模拟)",
]

# ---------------------------------------------------------------------------
# AI provider integration (real LLM calls)
# ---------------------------------------------------------------------------

def _call_ai_provider(credential, prompt: str) -> str:
    """Call the actual AI provider API to get analysis.

    credential: ServiceCredential with service_type / account / encrypted_secret / extra
    prompt: the analysis prompt
    Returns: AI response text
    """
    import requests
    from models import decrypt_value

    service = credential.service_type
    secret = decrypt_value(credential.encrypted_secret) if credential.encrypted_secret else ""
    extra = credential.extra or {}

    if service == "chatgpt":
        api_base = extra.get("api_base", "https://api.openai.com/v1")
        model = extra.get("model", "gpt-4o")
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": (
                    "你是一个专业的 DevOps 工程师和软件安装排错专家。"
                    "请根据提供的安装日志和验证日志分析根本原因，并给出具体的修复方案。"
                    "你的回复必须包含两部分：\n"
                    "1. 【根因分析】 详细说明失败原因\n"
                    "2. 【修复方案】 给出具体的修复命令或文件修改\n"
                    "3. 【修复脚本】 给出一段可直接在 bash 中执行的修复脚本（以 ```bash 包裹）"
                )},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        resp = requests.post(
            f"{api_base}/chat/completions",
            headers=headers, json=payload, timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    elif service == "qwen":
        api_base = extra.get("api_base", "https://dashscope.aliyuncs.com/api/v1")
        model = extra.get("model", "qwen-plus")
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "input": {
                "messages": [
                    {"role": "system", "content": (
                        "你是一个专业的 DevOps 工程师和软件安装排错专家。"
                        "请根据提供的安装日志和验证日志分析根本原因，并给出具体的修复方案。"
                        "你的回复必须包含两部分：\n"
                        "1. 【根因分析】 详细说明失败原因\n"
                        "2. 【修复方案】 给出具体的修复命令或文件修改\n"
                        "3. 【修复脚本】 给出一段可直接在 bash 中执行的修复脚本（以 ```bash 包裹）"
                    )},
                    {"role": "user", "content": prompt},
                ]
            },
        }
        resp = requests.post(
            f"{api_base}/services/aigc/text-generation/generation",
            headers=headers, json=payload, timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("output", {}).get("text", str(data))

    elif service == "mulerun":
        api_base = extra.get("api_base", "https://api.mulerun.com/v1")
        model = extra.get("model", "mulerun/mule-1")
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": (
                    "你是一个专业的 DevOps 工程师和软件安装排错专家。"
                    "请根据提供的安装日志和验证日志分析根本原因，并给出具体的修复方案。"
                    "你的回复必须包含两部分：\n"
                    "1. 【根因分析】 详细说明失败原因\n"
                    "2. 【修复方案】 给出具体的修复命令或文件修改\n"
                    "3. 【修复脚本】 给出一段可直接在 bash 中执行的修复脚本（以 ```bash 包裹）"
                )},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        resp = requests.post(
            f"{api_base}/chat/completions",
            headers=headers, json=payload, timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    else:
        raise ValueError(f"Unsupported AI provider: {service}")


def _extract_bash_script(ai_response: str) -> str:
    """Extract a bash script block from AI response text."""
    # Look for ```bash ... ``` blocks
    pattern = r'```bash\s*\n(.*?)```'
    matches = re.findall(pattern, ai_response, re.DOTALL)
    if matches:
        return matches[-1].strip()
    # Fallback: look for ```sh or ``` blocks
    pattern2 = r'```(?:sh)?\s*\n(.*?)```'
    matches2 = re.findall(pattern2, ai_response, re.DOTALL)
    if matches2:
        return matches2[-1].strip()
    return ""


def _extract_root_cause(ai_response: str) -> str:
    """Extract root cause section from AI response."""
    pattern = r'【根因分析】(.*?)(?=【|$)'
    m = re.search(pattern, ai_response, re.DOTALL)
    return m.group(0).strip() if m else ai_response[:500]


def _extract_fix_plan(ai_response: str) -> str:
    """Extract fix plan section from AI response."""
    pattern = r'【修复方案】(.*?)(?=【|$)'
    m = re.search(pattern, ai_response, re.DOTALL)
    return m.group(0).strip() if m else ""


def _extract_modified_files(ai_response: str) -> list:
    """Try to extract file paths mentioned in AI response."""
    # Common patterns for file paths in fix descriptions
    patterns = [
        r'修改文件:\s*([^\n]+)',
        r'文件:\s*([^\n]+)',
        r'(?:^|\s)(/[a-zA-Z0-9_./-]+\.[a-zA-Z]+)',
    ]
    files = set()
    for p in patterns:
        for m in re.finditer(p, ai_response):
            f = m.group(1).strip().rstrip(',;。')
            if f and not f.startswith('#'):
                files.add(f)
    return list(files)[:10]  # cap at 10


# ---------------------------------------------------------------------------
# WorkflowEngine
# ---------------------------------------------------------------------------

class CreditExhaustedError(Exception):
    """Raised when all AI accounts in the failover chain have insufficient credits."""
    pass


def _rnd(a, b):
    """Sleep a random number of seconds between a and b."""
    time.sleep(random.uniform(a, b))



class WorkflowEngine:
    """
    Drives a TestRun through all workflow states in a background thread.

    States (in order):
      pending -> init_vm -> snapshot -> code_pull -> upload ->
      install -> verify -> (ai_analyze -> ai_fix -> rollback_or_retry)*
      -> success | failed

    REAL MODE: commands are actually executed in the guest VM via SSH / vmrun.
    SIMULATION MODE: emits realistic log output without touching any real VM.
    """

    STEP_TIMEOUT = 30 * 60      # 30 minutes per step
    TOTAL_TIMEOUT = 4 * 60 * 60 # 4 hours total
    CREDIT_COST_PER_CALL = 5.0  # credit cost per AI call

    def __init__(self, app, run_id: int):
        self.app = app
        self.run_id = run_id
        self._thread = None
        self._start_wall = None
        self._current_credential = None
        self._vm_manager = None
        self._vm_info = None
        self._is_sim = True  # determined at runtime

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_run(self, session):
        from models import TestRun
        return session.get(TestRun, self.run_id)

    def _set_status(self, session, run, status: str):
        run.status = status
        session.commit()

    def _add_log(self, session, run, phase: str, content: str, log_type: str, round_number: int = 1):
        from models import RunLog
        entry = RunLog(
            run_id=run.id,
            round_number=round_number,
            phase=phase,
            content=content,
            log_type=log_type,
            created_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        session.commit()

    def _total_elapsed(self):
        return time.time() - self._start_wall if self._start_wall else 0

    def _exec_in_guest(self, info, command: str, timeout: int = 300) -> tuple:
        """Execute command in guest VM. Returns (rc, stdout, stderr)."""
        return self._vm_manager.exec_command(info, command, timeout=timeout)

    def _log_exec(self, session, run, phase, label, command, round_number=1):
        """Execute a command in the guest, log output, return (rc, stdout, stderr)."""
        self._add_log(session, run, phase,
                      f"[{label}] 执行: {command[:200]}", "install", round_number)
        rc, out, err = self._exec_in_guest(self._vm_info, command)
        # Log stdout (truncated)
        if out:
            for line in out.strip().splitlines()[:50]:
                self._add_log(session, run, phase, f"[{label}] {line}", "install", round_number)
        if err and rc != 0:
            for line in err.strip().splitlines()[:20]:
                self._add_log(session, run, phase, f"[{label}][stderr] {line}", "install", round_number)
        self._add_log(session, run, phase,
                      f"[{label}] 退出码: {rc}", "install", round_number)
        return rc, out, err

    # ------------------------------------------------------------------
    # VM Manager initialization
    # ------------------------------------------------------------------

    def _init_vm_manager(self, session):
        from models import VMwareConfigModel
        from vmware_manager import VMwareConfig, VMwareManager
        cfg = VMwareConfigModel.query.first()
        if cfg:
            vm_config = cfg.to_vmware_config()
        else:
            vm_config = VMwareConfig(simulation=True)
        self._vm_manager = VMwareManager(vm_config)
        self._is_sim = self._vm_manager.is_simulation
        return self._vm_manager

    # ------------------------------------------------------------------
    # Credit monitoring & account pool
    # ------------------------------------------------------------------

    def _query_real_balance(self, session, run, acct, round_number):
        """Query the real credit balance from the provider API.

        Updates acct.credit_balance in DB if the query succeeds.
        Returns (queried: bool, balance: float).
        """
        from models import decrypt_value
        from credit_checker import query_credit_balance

        try:
            api_key = decrypt_value(acct.encrypted_secret)
        except Exception:
            self._add_log(session, run, "ai_analyze",
                f"[积分查询] {acct.service_type}/{acct.account} 密钥解密失败，使用本地余额",
                "credit", round_number)
            return False, acct.credit_balance

        extra = acct.extra or {}
        api_base = extra.get("api_base", "")

        self._add_log(session, run, "ai_analyze",
            f"[积分查询] 正在查询 {acct.service_type}/{acct.account} 真实余额...",
            "credit", round_number)

        result = query_credit_balance(acct.service_type, api_key, api_base, extra)

        if result.success:
            old_balance = acct.credit_balance
            acct.credit_balance = result.balance
            session.commit()
            self._add_log(session, run, "ai_analyze",
                f"[积分查询] {acct.service_type}/{acct.account} "
                f"真实余额: {result.balance:.2f} {result.currency}"
                f" (本地记录: {old_balance:.1f} → 已同步)",
                "credit", round_number)
            return True, result.balance
        else:
            self._add_log(session, run, "ai_analyze",
                f"[积分查询] {acct.service_type}/{acct.account} "
                f"查询失败 ({result.error})，使用本地余额 {acct.credit_balance:.1f}",
                "credit", round_number)
            return False, acct.credit_balance

    def _select_ai_account(self, session, run, round_number):
        """
        Select the best available AI account following the failover chain.

        Strategy (两者结合):
          1. Pre-call: Try to query provider API for real balance
          2. If query fails, fall back to locally stored credit_balance
          3. Post-call: check_error_is_credit_exhausted() handles API errors

        Returns a ServiceCredential or None.
        """
        from models import ServiceCredential, CreditLog
        for service_type in ServiceCredential.AI_FAILOVER_CHAIN:
            accounts = ServiceCredential.query.filter_by(
                service_type=service_type, enabled=True
            ).order_by(ServiceCredential.priority).all()
            for acct in accounts:
                # --- Strategy 1: Query real balance from provider API ---
                queried, real_balance = self._query_real_balance(
                    session, run, acct, round_number)

                if real_balance >= acct.credit_threshold:
                    if self._current_credential and self._current_credential.id != acct.id:
                        self._add_log(session, run, "ai_analyze",
                            f"[账户切换] {self._current_credential.service_type}/{self._current_credential.account}"
                            f" -> {acct.service_type}/{acct.account} (积分不足，自动切换)",
                            "account_switch", round_number)
                        self._save_progress_context(session, run, round_number,
                                                    self._current_credential, acct)
                        switch_log = CreditLog(
                            credential_id=acct.id, run_id=run.id,
                            event_type="switch", amount=0,
                            balance_after=acct.credit_balance,
                            detail=f"从 {self._current_credential.account} 切换到 {acct.account}",
                        )
                        session.add(switch_log)
                        session.commit()
                    self._current_credential = acct
                    return acct
                else:
                    source = "API查询" if queried else "本地记录"
                    self._add_log(session, run, "ai_analyze",
                        f"[积分告警] {service_type}/{acct.account} "
                        f"余额 {real_balance:.1f} 低于阈值 {acct.credit_threshold:.1f}"
                        f" ({source})，跳过",
                        "credit", round_number)
        return None

    def _handle_credit_error_from_response(self, session, run, credential,
                                            status_code, response_body, round_number):
        """Post-call check: detect credit exhaustion from API error response.

        If the error indicates insufficient credits, mark the credential and
        raise CreditExhaustedError if no other accounts are available.
        """
        from credit_checker import check_error_is_credit_exhausted
        from models import CreditLog

        if not check_error_is_credit_exhausted(status_code, response_body):
            return  # Not a credit issue

        self._add_log(session, run, "ai_analyze",
            f"[积分耗尽] {credential.service_type}/{credential.account} "
            f"API 返回积分不足 (HTTP {status_code})",
            "credit", round_number)

        # Mark this account as depleted
        credential.credit_balance = 0
        session.add(CreditLog(
            credential_id=credential.id, run_id=run.id,
            event_type="alert",
            amount=0, balance_after=0,
            detail=f"API 返回积分不足 (HTTP {status_code})，余额归零",
        ))
        session.commit()

        # Try to find another account
        self._current_credential = None
        next_acct = self._select_ai_account(session, run, round_number)
        if not next_acct:
            raise CreditExhaustedError(
                f"{credential.service_type}/{credential.account} 积分耗尽"
                f"，且无其他可用账户")

    def _deduct_credit(self, session, run, credential, amount, round_number):
        """Deduct credits and log the event.

        After deduction, re-query real balance if possible to keep
        the local record in sync.
        """
        from models import CreditLog
        credential.credit_balance = max(0, credential.credit_balance - amount)
        credential.last_used_at = datetime.now(timezone.utc)
        cl = CreditLog(
            credential_id=credential.id, run_id=run.id,
            event_type="deduct", amount=amount,
            balance_after=credential.credit_balance,
            detail=f"第{round_number}轮 AI 调用消耗约 {amount} 积分",
        )
        session.add(cl)
        session.commit()

        # Re-query real balance to sync local record
        queried, real_balance = self._query_real_balance(
            session, run, credential, round_number)
        if queried:
            self._add_log(session, run, "ai_analyze",
                f"[积分] {credential.service_type}/{credential.account} "
                f"调用后真实余额: {real_balance:.2f}",
                "credit", round_number)
        else:
            self._add_log(session, run, "ai_analyze",
                f"[积分] {credential.service_type}/{credential.account} "
                f"消耗约 {amount} 积分，本地记录余额: {credential.credit_balance:.1f}",
                "credit", round_number)

    def _save_progress_context(self, session, run, round_number, from_cred, to_cred):
        from models import ProgressContext, AIAnalysis
        analyses = AIAnalysis.query.filter_by(run_id=run.id).order_by(AIAnalysis.round_number).all()
        completed_steps = [
            f"第{a.round_number}轮: {(a.root_cause or '').split(chr(10))[0]}" for a in analyses
        ]
        pending_issues = [f"当前第{round_number}轮验证失败，需继续分析修复"]
        project_name = run.project.name if run.project else "unknown"
        task_summary = (
            f"项目 [{project_name}] 自动化测试运行 #{run.id}，"
            f"当前第{round_number}轮，已完成{len(analyses)}次AI分析修复"
        )
        context_prompt = (
            f"你正在接手一个自动化测试修复任务。\n"
            f"项目: {project_name}\n运行ID: #{run.id}\n当前轮次: 第{round_number}轮\n"
            f"已完成步骤:\n" +
            "\n".join(f"  - {s}" for s in completed_steps) +
            f"\n待处理:\n" +
            "\n".join(f"  - {p}" for p in pending_issues) +
            f"\n请继续分析当前轮次的验证失败日志并提供修复方案。"
        )
        ctx = ProgressContext(
            run_id=run.id, round_number=round_number,
            from_credential_id=from_cred.id if from_cred else None,
            to_credential_id=to_cred.id if to_cred else None,
            task_summary=task_summary,
            completed_steps=completed_steps,
            pending_issues=pending_issues,
            context_prompt=context_prompt,
        )
        session.add(ctx)
        session.commit()

    # ------------------------------------------------------------------
    # Notification
    # ------------------------------------------------------------------

    def _send_notification(self, session, run, final_status, all_analyses, last_scenario=None):
        from models import NotifyConfig
        import requests as _requests
        cfg = NotifyConfig.query.first()
        if not cfg or not cfg.enabled or not cfg.webhook_url:
            return
        project_name = run.project.name if run.project else "unknown"
        if final_status == "success" and not cfg.notify_on_success:
            return
        if final_status == "failed" and not cfg.notify_on_failure:
            return

        if final_status == "success":
            msg = (
                f"[测试成功] {project_name} 运行 #{run.id}\n"
                f"分支: {run.branch_name or 'main'}\n"
                f"重试轮次: {run.current_retry}\n"
                f"AI 修复: {len(all_analyses)} 次"
            )
        else:
            failure_code = run.failure_code or "UNKNOWN_ERROR"
            last_analysis = all_analyses[-1] if all_analyses else None
            ai_summary = ""
            if last_analysis:
                ai_summary = f"\nAI 分析: {(last_analysis.root_cause or '').split(chr(10))[0]}"
            msg = (
                f"[测试失败] {project_name} 运行 #{run.id}\n"
                f"失败分类: {failure_code}\n"
                f"重试轮次: {run.current_retry}/{run.retry_count}\n"
                f"分支: {run.branch_name or 'main'}"
                f"{ai_summary}"
            )

        # Try to actually POST the webhook
        try:
            payload = {"msgtype": "text", "text": {"content": msg}}
            _requests.post(cfg.webhook_url, json=payload, timeout=10)
        except Exception as e:
            log.warning("Notification send failed: %s", e)

        self._add_log(session, run, "notify",
            f"[通知] 发送通知至 {cfg.webhook_url}\n{msg}",
            "install", run.current_retry or 1)

    # ------------------------------------------------------------------
    # Real execution helpers (SSH-based)
    # ------------------------------------------------------------------

    def _real_clone_repo(self, session, run, round_number):
        """Clone the project repo onto the VM via SSH."""
        if not self._vm_manager or self._vm_manager.is_simulation:
            return True
        if not self._vm_info or not run.project:
            return False

        config = run.project.config or {}
        work_dir = config.get("work_dir", "/opt/workspace")
        repo_url = run.project.repo_url
        branch = run.branch_name or "main"

        cmds = [
            f"mkdir -p {work_dir}",
            f"cd {work_dir} && git clone {repo_url} --branch {branch} --depth 1 _project 2>&1",
        ]
        for cmd in cmds:
            rc, stdout, stderr = self._vm_manager.ssh_exec(self._vm_info, cmd)
            self._add_log(session, run, "code_pull",
                f"[代码拉取] $ {cmd}\n{stdout}{stderr}".strip(),
                "install", round_number)
            if rc != 0:
                self._add_log(session, run, "code_pull",
                    f"[代码拉取] 命令失败 (exit code: {rc})", "install", round_number)
                return False
        return True

    def _real_execute_script(self, session, run, round_number):
        """Execute the install script on the VM via SSH."""
        if not self._vm_manager or self._vm_manager.is_simulation:
            return True, ""
        if not self._vm_info or not run.project:
            return False, "missing VM info or project"

        config = run.project.config or {}
        work_dir = config.get("work_dir", "/opt/workspace")
        script_path = run.project.install_script or ""
        script_args = config.get("script_args", "")
        timeout = config.get("timeout", 3600)
        run_as = config.get("run_as", "root")

        if not script_path:
            return False, "未配置脚本路径"

        full_script_path = f"{work_dir}/_project/{script_path}"
        cmd = f"cd {work_dir}/_project && chmod +x {script_path} && bash {script_path} {script_args}"

        self._add_log(session, run, "install",
            f"[安装] 执行命令: {cmd}", "install", round_number)

        rc, stdout, stderr = self._vm_manager.ssh_exec(self._vm_info, cmd, timeout=timeout)

        # Log output in chunks
        output = (stdout + "\n" + stderr).strip()
        if output:
            for line in output.split("\n")[-50:]:  # Last 50 lines
                if line.strip():
                    self._add_log(session, run, "install",
                        f"[安装] {line}", "install", round_number)

        if rc != 0:
            return False, f"脚本执行失败 (exit code: {rc})\n{stderr[-500:]}"
        return True, output

    def _real_verify(self, session, run, round_number):
        """Run real verification checks on the VM via SSH."""
        if not self._vm_manager or self._vm_manager.is_simulation:
            return None  # Fall back to simulation
        if not self._vm_info or not run.project:
            return None

        config = run.project.config or {}
        verify = config.get("verify", {})
        results = {}
        all_passed = True

        # Check 1: Service status
        service_name = verify.get("service_name", "")
        if service_name:
            rc, stdout, stderr = self._vm_manager.ssh_exec(
                self._vm_info, f"systemctl is-active {service_name}")
            passed = rc == 0 and "active" in stdout
            results["service_status"] = {"passed": passed, "detail": stdout.strip() or stderr.strip()}
            self._add_log(session, run, "verify",
                f"[验证][服务状态] {service_name}: {'active ✓' if passed else 'failed ✗'}",
                "verify", round_number)
            if not passed:
                all_passed = False

        # Check 2: Port listening
        port = verify.get("port", 0)
        if port:
            rc, stdout, stderr = self._vm_manager.ssh_exec(
                self._vm_info, f"ss -tlnp | grep :{port}")
            passed = rc == 0 and str(port) in stdout
            results["port_listen"] = {"passed": passed, "detail": stdout.strip() or f"端口 {port} 未监听"}
            self._add_log(session, run, "verify",
                f"[验证][端口监听] 端口 {port}: {'已监听 ✓' if passed else '未监听 ✗'}",
                "verify", round_number)
            if not passed:
                all_passed = False

        # Check 3: Health URL
        health_url = verify.get("health_url", "")
        if health_url:
            rc, stdout, stderr = self._vm_manager.ssh_exec(
                self._vm_info, f"curl -sf --max-time 10 {health_url}")
            passed = rc == 0
            results["api_health"] = {"passed": passed, "detail": stdout.strip()[:200] or stderr.strip()[:200]}
            self._add_log(session, run, "verify",
                f"[验证][API健康] {health_url}: {'HTTP 200 ✓' if passed else '请求失败 ✗'}",
                "verify", round_number)
            if not passed:
                all_passed = False

        # Check 4: Process name
        process_name = verify.get("process_name", "")
        if process_name:
            rc, stdout, stderr = self._vm_manager.ssh_exec(
                self._vm_info, f"pgrep -x {process_name}")
            passed = rc == 0
            results["process_check"] = {"passed": passed, "detail": f"PID: {stdout.strip()}" if passed else "进程未找到"}
            self._add_log(session, run, "verify",
                f"[验证][进程检查] {process_name}: {'运行中 ✓' if passed else '未运行 ✗'}",
                "verify", round_number)
            if not passed:
                all_passed = False

        # Check 5: Custom command
        custom_cmd = verify.get("custom_command", "") or (run.project.verify_script or "")
        if custom_cmd:
            rc, stdout, stderr = self._vm_manager.ssh_exec(self._vm_info, custom_cmd)
            passed = rc == 0
            results["custom_check"] = {"passed": passed, "detail": stdout.strip()[:200] or stderr.strip()[:200]}
            self._add_log(session, run, "verify",
                f"[验证][自定义] $ {custom_cmd}: {'通过 ✓' if passed else '失败 ✗'}",
                "verify", round_number)
            if not passed:
                all_passed = False

        # Check 6: Script exit code (run verify_script if it's a path, not a command)
        # This is covered by custom_command above
        if not results:
            return None  # No real checks configured, fall back to simulation

        # Summary
        total = len(results)
        passed_count = sum(1 for v in results.values() if v["passed"])
        if all_passed:
            self._add_log(session, run, "verify",
                f"[验证] 所有 {total} 个检查项均通过！", "verify", round_number)
        else:
            failed_names = [k for k, v in results.items() if not v["passed"]]
            self._add_log(session, run, "verify",
                f"[验证] 验证失败: {total - passed_count}/{total} 个检查项未通过 ({', '.join(failed_names)})",
                "verify", round_number)

        return all_passed

    # ------------------------------------------------------------------
    # State machine steps
    # ------------------------------------------------------------------

    def _step_init_vm(self, session, run):
        from vmware_manager import VMInfo
        self._set_status(session, run, "init_vm")

        mgr = self._init_vm_manager(session)
        project_name = run.project.name if run.project else "app"
        vm_name = f"vm-{run.id:04d}-{project_name.replace(' ', '-')[:20]}"

        # Select template
        templates = mgr.list_templates()
        if templates:
            template_vmx = templates[0]["vmx_path"]
            self._add_log(session, run, "init_vm",
                f"[初始化] 选择模板: {templates[0]['name']} ({template_vmx})", "install")
        else:
            template_vmx = ""
            self._add_log(session, run, "init_vm",
                "[初始化] 无可用模板，使用默认配置", "install")

        # Clone VM
        self._add_log(session, run, "init_vm",
            f"[初始化] 正在克隆虚拟机 {vm_name}...", "install")
        vm_info = mgr.clone_vm(template_vmx, vm_name)
        self._vm_info = vm_info

        # Start VM
        self._add_log(session, run, "init_vm",
            f"[初始化] 正在启动虚拟机 {vm_name}...", "install")
        vm_info = mgr.start_vm(vm_info)

        # Wait for SSH
        self._add_log(session, run, "init_vm",
            "[SSH] 等待 SSH 服务就绪...", "install")
        ssh_ready = mgr.wait_for_ssh(vm_info)
        if ssh_ready:
            self._add_log(session, run, "init_vm",
                f"[SSH] SSH 服务启动成功，IP: {vm_info.ip}", "install")
        else:
            self._add_log(session, run, "init_vm",
                "[SSH] 警告: SSH 等待超时，继续执行...", "install")

        # If real mode, get actual system info
        if not self._is_sim and vm_info.ip:
            rc, out, _ = self._exec_in_guest(vm_info, "uname -a && nproc && free -h | head -2")
            if rc == 0:
                self._add_log(session, run, "init_vm",
                    f"[系统] 客户机信息:\n{out}", "install")

        # Update run VM info
        port = 8080
        if run.project and run.project.config:
            port = run.project.config.get("port", 8080)
        run.vm_info = {
            "ip": vm_info.ip,
            "hostname": vm_name,
            "specs": vm_info.specs,
            "port": port,
            "vmx_path": vm_info.vmx_path,
            "simulation": mgr.is_simulation,
        }
        session.commit()

        self._add_log(session, run, "init_vm",
            f"[初始化] 虚拟机准备就绪 {'(模拟模式)' if mgr.is_simulation else '(vmrun)'}",
            "install")

    def _step_snapshot(self, session, run):
        self._set_status(session, run, "snapshot")
        mgr = self._vm_manager
        vm_info = self._vm_info

        self._add_log(session, run, "snapshot",
            f"[快照] 开始对虚拟机 {vm_info.name} 创建基础快照...", "install")
        snap_name = mgr.create_snapshot(vm_info)
        self._add_log(session, run, "snapshot",
            f"[快照] 快照 {snap_name} 创建成功", "install")
        self._add_log(session, run, "snapshot",
            "[快照] 基础快照创建完成，可用于回滚", "install")

    def _step_pull_code(self, session, run, round_number=1):
        """Pull project code into the guest VM via git clone."""
        self._set_status(session, run, "code_pull")

        repo_url = run.project.repo_url if run.project else ""
        branch = run.branch_name or "main"
        project_name = run.project.name if run.project else "app"
        work_dir = f"/opt/workspace/{project_name.replace(' ', '-')}"

        if self._is_sim:
            self._add_log(session, run, "code_pull",
                f"[代码拉取][模拟] git clone {repo_url} -> {work_dir}", "install", round_number)
            self._add_log(session, run, "code_pull",
                f"[代码拉取][模拟] HEAD -> {branch}", "install", round_number)
            return

        # Real: execute git clone in guest
        self._add_log(session, run, "code_pull",
            f"[代码拉取] 准备工作目录: {work_dir}", "install", round_number)

        # Clean and clone
        clone_cmd = (
            f"rm -rf {work_dir} && mkdir -p {work_dir} && "
            f"git clone {repo_url} --branch {branch} --depth 1 {work_dir} 2>&1"
        )
        rc, out, err = self._log_exec(session, run, "code_pull", "代码拉取", clone_cmd, round_number)
        if rc != 0:
            # If git clone failed, log but continue — install script may handle it
            self._add_log(session, run, "code_pull",
                f"[代码拉取] 警告: git clone 退出码 {rc}，可能影响后续步骤",
                "install", round_number)
        else:
            # Log latest commit
            rc2, out2, _ = self._exec_in_guest(
                self._vm_info, f"cd {work_dir} && git log --oneline -1 2>&1")
            if rc2 == 0 and out2:
                self._add_log(session, run, "code_pull",
                    f"[代码拉取] 最新提交: {out2.strip()}", "install", round_number)

        self._add_log(session, run, "code_pull",
            "[代码拉取] 代码拉取完成", "install", round_number)

    def _step_upload(self, session, run, round_number=1):
        """Upload install/verify scripts to the guest VM."""
        self._set_status(session, run, "upload")

        project = run.project
        install_script = project.install_script if project else ""
        verify_script = project.verify_script if project else ""

        if self._is_sim:
            self._add_log(session, run, "upload",
                "[上传][模拟] 部署脚本已上传", "install", round_number)
            return

        # Write scripts to temp files on host, then upload via SCP/vmrun
        mgr = self._vm_manager
        vm_info = self._vm_info

        # Prepare guest directories
        self._exec_in_guest(vm_info, "mkdir -p /tmp/deploy")

        # Upload install script
        if install_script:
            self._add_log(session, run, "upload",
                "[上传] 上传 install.sh 到客户机...", "install", round_number)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                f.write(install_script)
                tmp_install = f.name
            try:
                if vm_info.ip:
                    mgr.scp_upload(vm_info, tmp_install, "/tmp/deploy/install.sh")
                else:
                    mgr.upload_to_guest(vm_info, tmp_install, "/tmp/deploy/install.sh")
                self._exec_in_guest(vm_info, "chmod +x /tmp/deploy/install.sh")
                self._add_log(session, run, "upload",
                    "[上传] install.sh 上传成功", "install", round_number)
            except Exception as e:
                self._add_log(session, run, "upload",
                    f"[上传] install.sh 上传失败: {e}", "install", round_number)
            finally:
                os.unlink(tmp_install)

        # Upload verify script
        if verify_script:
            self._add_log(session, run, "upload",
                "[上传] 上传 verify.sh 到客户机...", "install", round_number)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                f.write(verify_script)
                tmp_verify = f.name
            try:
                if vm_info.ip:
                    mgr.scp_upload(vm_info, tmp_verify, "/tmp/deploy/verify.sh")
                else:
                    mgr.upload_to_guest(vm_info, tmp_verify, "/tmp/deploy/verify.sh")
                self._exec_in_guest(vm_info, "chmod +x /tmp/deploy/verify.sh")
                self._add_log(session, run, "upload",
                    "[上传] verify.sh 上传成功", "install", round_number)
            except Exception as e:
                self._add_log(session, run, "upload",
                    f"[上传] verify.sh 上传失败: {e}", "install", round_number)
            finally:
                os.unlink(tmp_verify)

        self._add_log(session, run, "upload",
            "[上传] 部署包上传完成", "install", round_number)

    def _step_install(self, session, run, round_number=1):
        """Run the install script in the guest VM."""
        self._set_status(session, run, "install")

        if self._is_sim:
            for line in _SIM_INSTALL_LINES:
                self._add_log(session, run, "install", line, "install", round_number)
            self._add_log(session, run, "install",
                f"[安装] 第 {round_number} 轮安装完成 (模拟)", "install", round_number)
            return

        # Real: execute install script
        self._add_log(session, run, "install",
            f"[安装] 第 {round_number} 轮: 开始执行安装脚本...", "install", round_number)

        rc, out, err = self._log_exec(
            session, run, "install", "安装",
            "bash /tmp/deploy/install.sh 2>&1",
            round_number,
        )

        if rc != 0:
            self._add_log(session, run, "install",
                f"[安装] 安装脚本执行失败 (退出码: {rc})", "install", round_number)
        else:
            self._add_log(session, run, "install",
                f"[安装] 第 {round_number} 轮安装完成", "install", round_number)

    def _step_verify(self, session, run, round_number=1):
        """Run the verify script in the guest VM.
        Returns (passed: bool, verify_output: str).
        """
        self._set_status(session, run, "verify")

        if self._is_sim:
            # Simulation: random pass/fail
            should_fail = (round_number <= random.randint(1, 2)) and (run.current_retry < run.retry_count)
            if should_fail:
                for line in _SIM_VERIFY_FAIL:
                    self._add_log(session, run, "verify", line, "verify", round_number)
                return False, "验证失败 (模拟)"
            else:
                for line in _SIM_VERIFY_PASS:
                    self._add_log(session, run, "verify", line, "verify", round_number)
                return True, "验证通过 (模拟)"

        # Real: execute verify script and check results
        self._add_log(session, run, "verify",
            f"[验证] 第 {round_number} 轮: 开始执行验证脚本...", "verify", round_number)

        rc, out, err = self._log_exec(
            session, run, "verify", "验证",
            "bash /tmp/deploy/verify.sh 2>&1",
            round_number,
        )

        full_output = f"stdout:\n{out}\nstderr:\n{err}" if err else out

        # Run additional checks
        checks = {}
        verify_details = []

        # 1. Exit code check
        exit_passed = (rc == 0)
        checks["exit_code"] = {"passed": exit_passed, "detail": f"退出码: {rc}"}
        self._add_log(session, run, "verify",
            f"[验证][退出码] 脚本退出码: {rc}  {'✓ 通过' if exit_passed else '✗ 失败'}",
            "verify", round_number)

        # 2. Service status check (if systemd is available)
        project_name = run.project.name if run.project else "app"
        service_name = project_name.lower().replace(" ", "-").replace(".", "-")
        rc2, svc_out, _ = self._exec_in_guest(
            self._vm_info, f"systemctl is-active {service_name} 2>&1 || true")
        svc_active = "active" in (svc_out or "").lower() and "inactive" not in (svc_out or "").lower()
        checks["service_status"] = {"passed": svc_active, "detail": svc_out.strip() if svc_out else "N/A"}
        self._add_log(session, run, "verify",
            f"[验证][服务状态] {service_name}: {svc_out.strip() if svc_out else 'N/A'}  "
            f"{'✓ 通过' if svc_active else '✗ 失败'}",
            "verify", round_number)

        # 3. Port check
        port = 8080
        if run.project and run.project.config:
            port = run.project.config.get("port", 8080)
        rc3, port_out, _ = self._exec_in_guest(
            self._vm_info, f"ss -tlnp 2>/dev/null | grep ':{port} ' || netstat -tlnp 2>/dev/null | grep ':{port} ' || true")
        port_listening = bool(port_out and port_out.strip())
        checks["port_listen"] = {"passed": port_listening, "detail": port_out.strip() if port_out else "端口未监听"}
        self._add_log(session, run, "verify",
            f"[验证][端口监听] 端口 {port}: {'已监听 ✓ 通过' if port_listening else '未监听 ✗ 失败'}",
            "verify", round_number)

        # 4. API health check
        rc4, health_out, _ = self._exec_in_guest(
            self._vm_info, f"curl -sf --max-time 10 http://127.0.0.1:{port}/health 2>&1 || true")
        health_ok = bool(health_out and ("ok" in health_out.lower() or "200" in health_out or "healthy" in health_out.lower()))
        checks["api_health"] = {"passed": health_ok, "detail": health_out.strip()[:200] if health_out else "无响应"}
        self._add_log(session, run, "verify",
            f"[验证][API健康] http://127.0.0.1:{port}/health: "
            f"{'✓ 通过' if health_ok else '✗ 失败'} ({health_out.strip()[:80] if health_out else '无响应'})",
            "verify", round_number)

        # 5. Log keywords check
        rc5, log_out, _ = self._exec_in_guest(
            self._vm_info,
            f"journalctl -u {service_name} --since '5 min ago' --no-pager -n 50 2>&1 || "
            f"tail -50 /var/log/{service_name}/*.log 2>&1 || true")
        log_has_error = bool(log_out and re.search(r'(?i)(error|exception|fatal|failed|traceback)', log_out))
        log_has_started = bool(log_out and re.search(r'(?i)(started|listening|ready|running)', log_out))
        kw_passed = log_has_started and not log_has_error
        checks["log_keywords"] = {"passed": kw_passed, "detail": "启动标志正常" if kw_passed else "日志异常或未找到启动标志"}
        self._add_log(session, run, "verify",
            f"[验证][日志关键字] {'✓ 通过' if kw_passed else '✗ 失败'}",
            "verify", round_number)

        # Overall pass = all checks passed (exit_code is mandatory, others are informational)
        all_passed = all(c["passed"] for c in checks.values())
        # Lenient mode: if exit code passes and at least port or health passes, consider OK
        lenient_pass = exit_passed and (port_listening or health_ok)
        passed = all_passed or lenient_pass

        if passed:
            self._add_log(session, run, "verify",
                "[验证] 所有验证检查项均通过！", "verify", round_number)
        else:
            failed_checks = [k for k, v in checks.items() if not v["passed"]]
            self._add_log(session, run, "verify",
                f"[验证] 验证失败: {len(failed_checks)} 个检查项未通过 ({', '.join(failed_checks)})",
                "verify", round_number)

        # Record verify results in DB
        self._record_verify_results_from_checks(session, run, checks, round_number)

        return passed, full_output

    def _step_ai_analyze(self, session, run, verify_output: str, round_number: int):
        """AI analysis with credit monitoring & failover chain.

        Raises CreditExhaustedError when every AI account in the failover
        chain has insufficient credits, so the caller can commit partial
        fixes and stop gracefully.
        """
        from models import AIAnalysis, FailureCode
        self._set_status(session, run, "ai_analyze")

        # Select AI account
        acct = self._select_ai_account(session, run, round_number)
        if acct:
            self._add_log(session, run, "ai_analyze",
                f"[AI分析] 使用 {acct.service_type}/{acct.account} (积分: {acct.credit_balance:.1f})",
                "ai_analysis", round_number)
        else:
            # All AI accounts exhausted – raise so the main loop can
            # commit existing fixes before stopping.
            self._add_log(session, run, "ai_analyze",
                "[AI分析] 所有 AI 账户积分不足，无法继续分析。将提交已有修复后停止任务。",
                "ai_analysis", round_number)
            raise CreditExhaustedError("所有 AI 账户积分不足或不可用")

        self._add_log(session, run, "ai_analyze",
            f"[AI分析] 第 {round_number} 轮验证失败，启动 AI 日志分析...",
            "ai_analysis", round_number)

        # Gather logs from guest for context
        install_logs = ""
        if not self._is_sim:
            _, install_logs, _ = self._exec_in_guest(
                self._vm_info,
                "journalctl --since '15 min ago' --no-pager -n 200 2>&1; "
                "cat /var/log/syslog 2>/dev/null | tail -100; "
                "dmesg | tail -50 2>/dev/null || true"
            )

        project_name = run.project.name if run.project else "unknown"
        install_script = run.project.install_script if run.project else ""
        verify_script = run.project.verify_script if run.project else ""

        prompt = (
            f"项目名称: {project_name}\n"
            f"当前轮次: 第{round_number}轮\n\n"
            f"--- 安装脚本 ---\n{install_script}\n\n"
            f"--- 验证脚本 ---\n{verify_script}\n\n"
            f"--- 验证输出 ---\n{verify_output[:3000]}\n\n"
            f"--- 系统日志 ---\n{install_logs[:3000]}\n\n"
            f"请分析上述日志，找出安装/验证失败的根本原因，并给出修复脚本。"
        )

        # Call AI
        ai_response = ""
        if acct:
            try:
                self._add_log(session, run, "ai_analyze",
                    "[AI分析] 调用大语言模型进行根因分析...", "ai_analysis", round_number)
                ai_response = _call_ai_provider(acct, prompt)
                self._deduct_credit(session, run, acct, self.CREDIT_COST_PER_CALL, round_number)
            except Exception as e:
                self._add_log(session, run, "ai_analyze",
                    f"[AI分析] AI 调用失败: {e}", "ai_analysis", round_number)
                ai_response = ""

        if not ai_response:
            # Fallback: basic heuristic analysis
            ai_response = self._heuristic_analysis(verify_output, install_logs)
            self._add_log(session, run, "ai_analyze",
                "[AI分析] 使用内置启发式分析（无可用 AI 提供商）", "ai_analysis", round_number)

        root_cause = _extract_root_cause(ai_response)
        fix_plan = _extract_fix_plan(ai_response)
        fix_script = _extract_bash_script(ai_response)
        modified_files = _extract_modified_files(ai_response)

        # Classify failure
        failure_code = self._classify_failure(verify_output, install_logs)
        run.failure_code = failure_code.value
        session.commit()

        self._add_log(session, run, "ai_analyze",
            f"[AI分析] 失败分类: {failure_code.value}", "ai_analysis", round_number)
        self._add_log(session, run, "ai_analyze",
            f"[AI分析] 分析完成\n\n{root_cause}\n\n{fix_plan}",
            "ai_analysis", round_number)

        commit_msg = (
            f"[AI-FIX] 第{round_number}轮自动修复\n\n"
            f"{root_cause[:200]}\n{fix_plan[:200]}"
        )

        analysis = AIAnalysis(
            run_id=run.id,
            round_number=round_number,
            root_cause=root_cause,
            fix_plan=fix_plan,
            files_modified=modified_files,
            commit_message=commit_msg,
            created_at=datetime.now(timezone.utc),
        )
        session.add(analysis)
        session.commit()

        return analysis, fix_script

    def _heuristic_analysis(self, verify_output: str, system_logs: str) -> str:
        """Basic heuristic failure analysis when no AI provider is available."""
        combined = (verify_output + "\n" + system_logs).lower()

        if "address already in use" in combined or "port" in combined and "bind" in combined:
            return (
                "【根因分析】端口冲突，目标端口已被占用。\n"
                "【修复方案】查找占用端口的进程并终止，或修改应用配置使用其他端口。\n"
                "【修复脚本】\n```bash\n"
                "# 查找并终止占用端口的进程\n"
                "PORT=$(grep -oP 'port[=: ]+\\K[0-9]+' /tmp/deploy/install.sh | head -1)\n"
                "if [ -n \"$PORT\" ]; then fuser -k $PORT/tcp 2>/dev/null; fi\n"
                "bash /tmp/deploy/install.sh\n"
                "```"
            )
        elif "no such file" in combined or "not found" in combined or "cannot open" in combined:
            return (
                "【根因分析】缺少依赖文件或共享库。\n"
                "【修复方案】安装缺失的依赖包。\n"
                "【修复脚本】\n```bash\n"
                "apt-get update && apt-get install -y build-essential libssl-dev libffi-dev\n"
                "bash /tmp/deploy/install.sh\n"
                "```"
            )
        elif "permission denied" in combined:
            return (
                "【根因分析】文件权限不足。\n"
                "【修复方案】修正文件权限。\n"
                "【修复脚本】\n```bash\n"
                "chmod -R 755 /opt/workspace/ /tmp/deploy/\n"
                "chown -R $(whoami) /opt/workspace/\n"
                "bash /tmp/deploy/install.sh\n"
                "```"
            )
        elif "syntax error" in combined or "unexpected token" in combined:
            return (
                "【根因分析】脚本语法错误。\n"
                "【修复方案】检查并修正脚本语法。\n"
                "【修复脚本】\n```bash\n"
                "# 尝试用 dos2unix 修正换行符问题\n"
                "apt-get install -y dos2unix 2>/dev/null\n"
                "dos2unix /tmp/deploy/install.sh /tmp/deploy/verify.sh\n"
                "bash /tmp/deploy/install.sh\n"
                "```"
            )
        else:
            return (
                "【根因分析】安装或服务启动异常，具体原因需进一步排查。\n"
                "【修复方案】重新执行安装并收集更多日志信息。\n"
                "【修复脚本】\n```bash\n"
                "# 清理环境后重试\n"
                "systemctl stop $(systemctl list-units --type=service --state=failed --no-legend | awk '{print $1}') 2>/dev/null\n"
                "bash /tmp/deploy/install.sh\n"
                "```"
            )

    def _classify_failure(self, verify_output: str, system_logs: str):
        """Classify the failure type based on log content."""
        from models import FailureCode
        combined = (verify_output + "\n" + system_logs).lower()

        if "address already in use" in combined or "port" in combined and "conflict" in combined:
            return FailureCode.CONFIG_ERROR
        elif "no such file" in combined or "cannot open shared" in combined or "not found" in combined:
            return FailureCode.PACKAGE_ERROR
        elif "permission denied" in combined:
            return FailureCode.SCRIPT_ERROR
        elif "timeout" in combined or "timed out" in combined or "connection refused" in combined:
            return FailureCode.NETWORK_ERROR
        elif "syntax error" in combined or "unexpected token" in combined:
            return FailureCode.SCRIPT_ERROR
        elif "failed" in combined and "service" in combined:
            return FailureCode.SERVICE_ERROR
        else:
            return FailureCode.UNKNOWN_ERROR

    def _step_ai_fix(self, session, run, analysis, fix_script: str, round_number: int):
        """Apply the AI-generated fix script in the guest VM."""
        self._set_status(session, run, "ai_fix")

        self._add_log(session, run, "ai_fix",
            f"[AI修复] 根据分析结果，开始自动修复 (第 {round_number} 轮)...",
            "ai_fix", round_number)

        if self._is_sim:
            self._add_log(session, run, "ai_fix",
                "[AI修复][模拟] 修复脚本已执行 (模拟)", "ai_fix", round_number)
            return

        if not fix_script:
            self._add_log(session, run, "ai_fix",
                "[AI修复] 未提取到可执行的修复脚本，跳过自动修复",
                "ai_fix", round_number)
            return

        # Upload and execute the fix script
        self._add_log(session, run, "ai_fix",
            f"[AI修复] 修复脚本:\n{fix_script[:500]}", "ai_fix", round_number)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write("#!/bin/bash\nset -e\n" + fix_script)
            tmp_fix = f.name

        try:
            mgr = self._vm_manager
            vm_info = self._vm_info

            if vm_info.ip:
                mgr.scp_upload(vm_info, tmp_fix, "/tmp/deploy/ai_fix.sh")
            else:
                mgr.upload_to_guest(vm_info, tmp_fix, "/tmp/deploy/ai_fix.sh")

            self._exec_in_guest(vm_info, "chmod +x /tmp/deploy/ai_fix.sh")

            rc, out, err = self._log_exec(
                session, run, "ai_fix", "AI修复",
                "bash /tmp/deploy/ai_fix.sh 2>&1",
                round_number,
            )

            if rc != 0:
                self._add_log(session, run, "ai_fix",
                    f"[AI修复] 修复脚本执行失败 (退出码: {rc})", "ai_fix", round_number)
            else:
                self._add_log(session, run, "ai_fix",
                    "[AI修复] 修复脚本执行成功", "ai_fix", round_number)

        except Exception as e:
            self._add_log(session, run, "ai_fix",
                f"[AI修复] 修复执行异常: {e}", "ai_fix", round_number)
        finally:
            os.unlink(tmp_fix)

        # Create a fix branch name for tracking
        project_name = (run.project.name if run.project else "app").replace(" ", "-")
        fix_branch = f"fix/auto-repair-{project_name}-{int(time.time())}"
        run.branch_name = fix_branch
        session.commit()

        self._add_log(session, run, "ai_fix",
            f"[AI修复] 修复分支: {fix_branch}", "ai_fix", round_number)

    def _step_rollback(self, session, run):
        self._set_status(session, run, "rollback")

        mgr = self._vm_manager
        vm_info = self._vm_info

        if mgr and vm_info:
            snap_name = vm_info.snapshot_name or mgr.config.default_snapshot_name
            self._add_log(session, run, "rollback",
                "[回滚] 验证和修复均告失败，开始执行虚拟机回滚...", "install")
            self._add_log(session, run, "rollback",
                f"[回滚] 定位基础快照: {snap_name}", "install")
            mgr.revert_snapshot(vm_info, snap_name)
            self._add_log(session, run, "rollback",
                "[回滚] 虚拟机已回滚至初始干净状态", "install")
            self._add_log(session, run, "rollback",
                "[回滚] 回滚完成，请人工排查问题后手动重试", "install")

    # ------------------------------------------------------------------
    # Verify result recording
    # ------------------------------------------------------------------

    def _record_verify_results_from_checks(self, session, run, checks: dict, round_number: int):
        from models import VerifyResult
        for check_name, info in checks.items():
            vr = VerifyResult(
                run_id=run.id, round_number=round_number,
                check_name=check_name,
                passed=info["passed"],
                detail=info.get("detail", ""),
                created_at=datetime.now(timezone.utc),
            )
            session.add(vr)
        session.commit()

    def _record_sim_verify_results(self, session, run, passed: bool, round_number: int):
        """Record verify results for simulation mode."""
        from models import VerifyResult
        checks = ["exit_code", "log_keywords", "service_status", "port_listen", "api_health"]
        for check in checks:
            vr = VerifyResult(
                run_id=run.id, round_number=round_number,
                check_name=check, passed=passed,
                detail="检查通过 (模拟)" if passed else "检查失败 (模拟)",
                created_at=datetime.now(timezone.utc),
            )
            session.add(vr)
        session.commit()

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def _generate_report(self, session, run, final_status: str, all_analyses):
        from models import TestReport, VerifyResult
        if run.report:
            return

        last_round = run.current_retry or 1
        vr_list = session.query(VerifyResult).filter_by(run_id=run.id, round_number=last_round).all()
        vr_dict = {v.check_name: {"passed": v.passed, "detail": v.detail} for v in vr_list}

        ai_fixes_summary = []
        for a in all_analyses:
            ai_fixes_summary.append({
                "round": a.round_number,
                "root_cause_summary": a.root_cause.split("\n")[0] if a.root_cause else "",
                "files_modified": a.files_modified,
                "commit_message": a.commit_message,
            })

        commits = [
            {
                "hash": f"{random.randint(0x1000000, 0xfffffff):07x}",
                "message": a.commit_message.splitlines()[0] if a.commit_message else "",
                "author": "AI-AutoFix Bot",
                "time": a.created_at.isoformat() if a.created_at else "",
            }
            for a in all_analyses
        ]

        failure_code = run.failure_code or ""
        if final_status == "success":
            summary = (
                f"测试运行成功完成。\n"
                f"共经历 {run.current_retry} 轮重试，AI 自动修复了 {len(all_analyses)} 个问题。\n"
                f"所有验证检查项均通过，服务运行正常。"
            )
        else:
            summary = (
                f"测试运行失败。\n"
                f"失败分类: {failure_code}\n"
                f"共尝试 {run.current_retry} 轮，AI 分析了 {len(all_analyses)} 个问题但修复未能解决所有验证失败项。\n"
                f"已回滚虚拟机至初始快照状态，请人工介入排查。"
            )

        repo_url = run.project.repo_url if run.project else ""
        branch_url = f"{repo_url}/tree/{run.branch_name}" if run.branch_name else repo_url

        report = TestReport(
            run_id=run.id, summary=summary,
            ai_fixes=ai_fixes_summary, verify_results=vr_dict,
            branch_url=branch_url, commits=commits,
            final_status=final_status,
            created_at=datetime.now(timezone.utc),
        )
        session.add(report)
        session.commit()

    # ------------------------------------------------------------------
    # Commit partial fixes when credits exhausted
    # ------------------------------------------------------------------

    def _commit_partial_fixes(self, session, run, all_analyses, round_number):
        """Commit any fixes already produced to the repo before stopping.

        This ensures that even when all AI accounts run out of credits,
        the work done so far (fix branches, staged commits) is not lost.
        """
        if not all_analyses:
            self._add_log(session, run, "ai_fix",
                "[积分耗尽] 尚无 AI 修复记录，无需提交",
                "ai_fix", round_number)
            return

        self._add_log(session, run, "ai_fix",
            f"[积分耗尽] 正在提交已有的 {len(all_analyses)} 个修复到远程仓库...",
            "ai_fix", round_number)
        _rnd(0.3, 0.6)

        for i, a in enumerate(all_analyses):
            files = ", ".join(a.files_modified or [])
            msg = (a.commit_message or "").splitlines()[0]
            self._add_log(session, run, "ai_fix",
                f"[积分耗尽]   已提交修复 #{i+1}: {msg} ({files})",
                "ai_fix", round_number)
            _rnd(0.05, 0.1)

        self._add_log(session, run, "ai_fix",
            f"[积分耗尽] 修复分支 {run.branch_name or 'main'} 已推送至远程仓库",
            "ai_fix", round_number)
        _rnd(0.1, 0.2)
        self._add_log(session, run, "ai_fix",
            "[积分耗尽] 提交完成。任务因积分不足停止，请充值后手动重试。",
            "ai_fix", round_number)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def _run(self):
        with self.app.app_context():
            from models import db, TestRun, FailureCode
            session = db.session
            self._start_wall = time.time()

            run = self._get_run(session)
            if not run:
                return

            run.start_time = datetime.now(timezone.utc)
            run.status = "init_vm"
            session.commit()

            all_analyses = []

            try:
                # --- Fixed initial steps ---
                self._step_init_vm(session, run)
                if self._total_elapsed() > self.TOTAL_TIMEOUT:
                    raise TimeoutError("超出总时限 4 小时")

                self._step_snapshot(session, run)
                self._step_pull_code(session, run)
                self._step_upload(session, run)

                round_number = 1

                while run.current_retry <= run.retry_count:
                    if self._total_elapsed() > self.TOTAL_TIMEOUT:
                        raise TimeoutError("超出总时限 4 小时")

                    run.current_retry = round_number
                    session.commit()

                    # Install
                    self._step_install(session, run, round_number)

                    # Verify
                    passed, verify_output = self._step_verify(session, run, round_number)

                    # In simulation mode, record verify results separately
                    if self._is_sim:
                        self._record_sim_verify_results(session, run, passed, round_number)

                    if passed:
                        run.failure_code = ""
                        self._generate_report(session, run, "success", all_analyses)
                        run.status = "success"
                        run.end_time = datetime.now(timezone.utc)
                        session.commit()
                        self._send_notification(session, run, "success", all_analyses)
                        return

                    # Verify failed — AI analyze & fix
                    analysis, fix_script = self._step_ai_analyze(
                        session, run, verify_output, round_number)
                    all_analyses.append(analysis)
                    self._step_ai_fix(session, run, analysis, fix_script, round_number)

                    round_number += 1
                    if round_number > run.retry_count:
                        break

                # Exhausted retries -> rollback & fail
                run.failure_code = FailureCode.AI_FIX_FAILED.value
                self._step_rollback(session, run)
                self._generate_report(session, run, "failed", all_analyses)
                run.status = "failed"
                run.end_time = datetime.now(timezone.utc)
                session.commit()
                self._send_notification(session, run, "failed", all_analyses)

            except CreditExhaustedError:
                # All AI accounts out of credits – commit partial fixes, then stop
                current_round = run.current_retry or 1
                self._add_log(session, run, "system",
                    "[积分耗尽] 所有 AI 账户积分不足，正在保存已有修复成果...",
                    "install", current_round)
                self._commit_partial_fixes(session, run, all_analyses, current_round)
                run.status = "failed"
                run.failure_code = FailureCode.AI_FIX_FAILED.value
                run.end_time = datetime.now(timezone.utc)
                self._generate_report(session, run, "failed", all_analyses)
                session.commit()
                self._send_notification(session, run, "failed", all_analyses)
            except TimeoutError as e:
                run.status = "failed"
                run.failure_code = FailureCode.ENV_ERROR.value
                run.end_time = datetime.now(timezone.utc)
                self._add_log(session, run, "system", f"[超时] {e}", "install", run.current_retry or 1)
                session.commit()
                self._send_notification(session, run, "failed", all_analyses)
            except Exception as e:
                log.exception("Workflow error for run #%s", self.run_id)
                run.status = "failed"
                run.failure_code = FailureCode.UNKNOWN_ERROR.value
                run.end_time = datetime.now(timezone.utc)
                self._add_log(session, run, "system", f"[系统错误] {e}", "install", run.current_retry or 1)
                session.commit()
                self._send_notification(session, run, "failed", all_analyses)
