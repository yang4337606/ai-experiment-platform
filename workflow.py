"""
Workflow engine for AI Automated Experiment Platform (AI自动化实验平台).
Drives a TestRun through its full state machine in a background thread.
"""
import random
import threading
import time
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Realistic log content libraries (Chinese)
# ---------------------------------------------------------------------------

_VM_INIT_LOGS = [
    "[初始化] 正在向资源池申请虚拟机资源...",
    "[初始化] 资源申请成功，分配节点: node-cluster-03",
    "[网络] 正在配置虚拟网络接口 eth0...",
    "[网络] IP 地址已分配: {ip}",
    "[网络] 子网掩码: 255.255.255.0  网关: 192.168.1.1",
    "[网络] DNS 配置完成: 8.8.8.8, 114.114.114.0",
    "[SSH] 正在生成 RSA 4096 密钥对...",
    "[SSH] 公钥已注入 authorized_keys",
    "[SSH] SSH 服务启动成功，监听端口 22",
    "[主机名] 设置主机名为: {hostname}",
    "[系统] OS: Ubuntu 22.04 LTS x86_64",
    "[系统] 内核版本: 5.15.0-91-generic",
    "[系统] CPU: {cpu_cores} vCPU  内存: {memory}GB  磁盘: {disk}GB",
    "[系统] 时区已同步至 Asia/Shanghai",
    "[系统] 虚拟机初始化完成，耗时 {elapsed}s",
]

_SNAPSHOT_LOGS = [
    "[快照] 开始对虚拟机 {hostname} 创建基础快照...",
    "[快照] 正在冻结文件系统 I/O...",
    "[快照] 正在创建 COW (Copy-On-Write) 快照...",
    "[快照] 快照 ID: snap-{snap_id} 创建成功",
    "[快照] 快照大小: {size}MB，已压缩至 {compressed}MB",
    "[快照] 快照元数据已写入注册表",
    "[快照] 文件系统 I/O 已恢复",
    "[快照] 基础快照创建完成，可用于回滚",
]

_CODE_PULL_LOGS = [
    "[代码拉取] 初始化 Git 工作区: /opt/workspace/{project}",
    "[代码拉取] 配置 Git 凭证...",
    "[代码拉取] 执行: git clone {repo_url} --branch {branch} --depth 1",
    "[代码拉取] Cloning into '/opt/workspace/{project}'...",
    "[代码拉取] remote: Enumerating objects: 1247, done.",
    "[代码拉取] remote: Counting objects: 100% (1247/1247), done.",
    "[代码拉取] remote: Compressing objects: 100% (892/892), done.",
    "[代码拉取] Receiving objects: 100% (1247/1247), 23.41 MiB | 15.32 MiB/s, done.",
    "[代码拉取] Resolving deltas: 100% (445/445), done.",
    "[代码拉取] HEAD -> {branch}，最新提交: {commit_hash}",
    "[代码拉取] 代码拉取完成",
]

_UPLOAD_LOGS = [
    "[上传] 正在打包安装脚本和配置文件...",
    "[上传] 压缩包大小: {pkg_size}KB",
    "[上传] 通过 SCP 上传至 {ip}:/tmp/deploy/",
    "[上传] 上传进度: 100% 完成",
    "[上传] 设置脚本执行权限: chmod +x /tmp/deploy/*.sh",
    "[上传] 文件完整性校验 (MD5): 通过",
    "[上传] 部署包上传完成",
]

_INSTALL_LOGS_JAVA = [
    "[安装] 开始执行安装脚本: /tmp/deploy/install.sh",
    "[安装] 更新系统软件包索引...",
    "[安装] apt-get update: 获取 47 个包列表",
    "[安装] 正在安装 JDK 17...",
    "[安装] 已安装: openjdk-17-jdk (17.0.9+9-1~22.04)",
    "[安装] JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64",
    "[安装] 正在安装 Maven 3.9.6...",
    "[安装] 下载 apache-maven-3.9.6-bin.tar.gz (8.7 MB)...",
    "[安装] Maven 安装完成: mvn -version -> Apache Maven 3.9.6",
    "[安装] 正在构建项目: mvn clean package -DskipTests",
    "[安装] [INFO] Scanning for projects...",
    "[安装] [INFO] Building {project} 1.0.0-SNAPSHOT",
    "[安装] [INFO] --- maven-compiler-plugin:3.11.0:compile ---",
    "[安装] [INFO] Compiling 142 source files to /opt/workspace/{project}/target/classes",
    "[安装] [INFO] BUILD SUCCESS",
    "[安装] [INFO] Total time: 1:23 min",
    "[安装] 正在配置 Systemd 服务: {project}.service",
    "[安装] systemctl enable {project}.service -> 已启用",
    "[安装] systemctl start {project}.service",
    "[安装] 等待服务启动 (超时: 60s)...",
]

_INSTALL_LOGS_PYTHON = [
    "[安装] 开始执行安装脚本: /tmp/deploy/install.sh",
    "[安装] 检查 Python 版本: python3 --version -> Python 3.11.6",
    "[安装] 创建虚拟环境: python3 -m venv /opt/{project}/venv",
    "[安装] 激活虚拟环境",
    "[安装] 安装依赖: pip install -r requirements.txt",
    "[安装] Collecting flask==3.0.1",
    "[安装] Collecting sqlalchemy==2.0.25",
    "[安装] Collecting celery==5.3.6",
    "[安装] Collecting redis==5.0.1",
    "[安装] Installing collected packages: flask, sqlalchemy, celery, redis (共 38 个包)",
    "[安装] Successfully installed all packages",
    "[安装] 初始化数据库: python manage.py db upgrade",
    "[安装] 正在配置 Gunicorn 服务...",
    "[安装] systemctl enable {project}.service -> 已启用",
    "[安装] systemctl start {project}.service",
    "[安装] 等待服务启动 (超时: 60s)...",
]

_INSTALL_LOGS_NODE = [
    "[安装] 开始执行安装脚本: /tmp/deploy/install.sh",
    "[安装] 检查 Node.js 版本: node --version -> v20.11.0",
    "[安装] 检查 npm 版本: npm --version -> 10.2.4",
    "[安装] 安装项目依赖: npm ci --production",
    "[安装] npm warn deprecated inflight@1.0.6",
    "[安装] added 847 packages in 34s",
    "[安装] 构建前端资产: npm run build",
    "[安装] 构建完成，输出目录: dist/",
    "[安装] 正在配置 PM2 进程管理器...",
    "[安装] pm2 start ecosystem.config.js",
    "[安装] 等待服务启动 (超时: 60s)...",
]

_VERIFY_PASS_LOGS = [
    # exit_code check
    "[验证][退出码] 执行验证脚本: /tmp/deploy/verify.sh",
    "[验证][退出码] 脚本退出码: 0  ✓ 通过",
    # log_keywords check
    "[验证][日志关键字] 检查服务日志中是否包含启动成功标志...",
    "[验证][日志关键字] journalctl -u {service} | grep 'Started successfully'",
    "[验证][日志关键字] 找到关键字: 'Application started successfully on port {port}'  ✓ 通过",
    # service_status check
    "[验证][服务状态] systemctl status {service}.service",
    "[验证][服务状态] ● {service}.service - {project} Application Service",
    "[验证][服务状态]    Loaded: loaded (/etc/systemd/system/{service}.service; enabled)",
    "[验证][服务状态]    Active: active (running) since {time}; 12s ago",
    "[验证][服务状态]  服务状态: active (running)  ✓ 通过",
    # port_listen check
    "[验证][端口监听] ss -tlnp | grep :{port}",
    "[验证][端口监听] LISTEN 0 128 0.0.0.0:{port} 0.0.0.0:* users:((\"{service}\",pid={pid},fd=6))",
    "[验证][端口监听] 端口 {port} 已正常监听  ✓ 通过",
    # api_health check
    "[验证][API健康] curl -sf http://127.0.0.1:{port}/health",
    '[验证][API健康] 响应: {"status":"ok","version":"1.0.0","uptime":12}',
    "[验证][API健康] HTTP 状态码: 200  ✓ 通过",
    "[验证] 所有验证检查项均通过！",
]

_VERIFY_FAIL_LOGS_PORT = [
    "[验证][退出码] 执行验证脚本: /tmp/deploy/verify.sh",
    "[验证][退出码] 脚本退出码: 0  ✓ 通过",
    "[验证][日志关键字] 检查服务日志中是否包含启动成功标志...",
    "[验证][日志关键字] journalctl -u {service} | grep 'Started successfully'",
    "[验证][日志关键字] 找到关键字: 'Started'  ✓ 通过",
    "[验证][服务状态] systemctl status {service}.service",
    "[验证][服务状态]    Active: active (running)  ✓ 通过",
    "[验证][端口监听] ss -tlnp | grep :{port}",
    "[验证][端口监听] (无输出)",
    "[验证][端口监听] 错误: 端口 {port} 未监听  ✗ 失败",
    "[验证][端口监听] 可能原因: 端口被占用或服务未正常绑定",
    "[验证][API健康] 跳过 (依赖端口监听检查通过)",
    "[验证] 验证失败: 1 个检查项未通过 (port_listen)",
]

_VERIFY_FAIL_LOGS_SERVICE = [
    "[验证][退出码] 执行验证脚本: /tmp/deploy/verify.sh",
    "[验证][退出码] 脚本退出码: 1  ✗ 失败",
    "[验证][日志关键字] 检查服务日志中是否包含启动成功标志...",
    "[验证][日志关键字] journalctl -u {service} --since '1 min ago'",
    "[验证][日志关键字] Error: Failed to bind to port {port}: Address already in use",
    "[验证][日志关键字] 未找到预期关键字  ✗ 失败",
    "[验证][服务状态] systemctl status {service}.service",
    "[验证][服务状态]    Active: failed (Result: exit-code) since {time}; 3s ago",
    "[验证][服务状态]    Process: ExecStart={service} (code=exited, status=1/FAILURE)",
    "[验证][服务状态] 服务状态: failed  ✗ 失败",
    "[验证][端口监听] 跳过 (依赖服务状态检查通过)",
    "[验证][API健康] 跳过 (依赖端口监听检查通过)",
    "[验证] 验证失败: 3 个检查项未通过 (exit_code, log_keywords, service_status)",
]

_VERIFY_FAIL_LOGS_DEPENDENCY = [
    "[验证][退出码] 执行验证脚本: /tmp/deploy/verify.sh",
    "[验证][退出码] 脚本退出码: 127  ✗ 失败",
    "[验证][退出码] stderr: /opt/{project}/bin/start.sh: line 12: libssl.so.3: 共享库加载失败",
    "[验证][日志关键字] journalctl -u {service} --since '2 min ago'",
    "[验证][日志关键字] error while loading shared libraries: libssl.so.3: cannot open shared object file",
    "[验证][日志关键字] 未找到预期关键字  ✗ 失败",
    "[验证][服务状态] Active: failed  ✗ 失败",
    "[验证][端口监听] 跳过 (依赖服务状态检查通过)",
    "[验证][API健康] 跳过 (依赖端口监听检查通过)",
    "[验证] 验证失败: 3 个检查项未通过 (exit_code, log_keywords, service_status)",
]

# Root cause / fix plan templates (Chinese)
_FAILURE_SCENARIOS = [
    {
        "type": "port_conflict",
        "root_cause": (
            "【根因分析】端口冲突问题\n\n"
            "通过分析安装日志和系统状态，发现应用启动失败的根本原因为端口占用冲突：\n\n"
            "1. 应用配置文件 application.yaml 中将服务绑定端口配置为 8080\n"
            "2. 系统中已有进程 nginx (PID: 3421) 占用了 8080 端口\n"
            "3. 应用尝试绑定时抛出 Address already in use 异常并立即退出\n\n"
            "证据链：\n"
            "- journalctl 日志: 'Caused by: java.net.BindException: Address already in use'\n"
            "- ss -tlnp 输出: nginx 监听 0.0.0.0:8080\n"
            "- 应用 exit code: 1"
        ),
        "fix_plan": (
            "【修复方案】将应用服务端口从 8080 修改为 8090\n\n"
            "修改文件: src/main/resources/application.yaml\n"
            "  - server.port: 8080  →  server.port: 8090\n\n"
            "修改文件: config/nginx.conf\n"
            "  - proxy_pass http://127.0.0.1:8080  →  proxy_pass http://127.0.0.1:8090\n\n"
            "同时更新健康检查脚本 verify.sh 中的端口参数。"
        ),
        "files": ["src/main/resources/application.yaml", "config/nginx.conf", "scripts/verify.sh"],
        "commit_msg_body": "错误现象: 服务启动失败，端口 8080 被 nginx 占用\n根因判断: application.yaml 中 server.port=8080 与系统已有服务冲突\n修复内容: 将 server.port 修改为 8090，同步更新 nginx 反代配置及验证脚本",
    },
    {
        "type": "missing_dependency",
        "root_cause": (
            "【根因分析】缺少运行时共享库\n\n"
            "应用启动时动态链接器无法找到所需的共享库文件：\n\n"
            "1. 应用编译时链接了 OpenSSL 3.x (libssl.so.3)\n"
            "2. 目标系统 Ubuntu 22.04 默认安装的是 OpenSSL 1.1 (libssl.so.1.1)\n"
            "3. 动态链接器报错: cannot open shared object file: No such file or directory\n\n"
            "证据链：\n"
            "- ldd 输出: libssl.so.3 => not found\n"
            "- dpkg -l | grep libssl: libssl1.1 已安装，libssl3 未安装\n"
            "- 错误信息: error while loading shared libraries: libssl.so.3"
        ),
        "fix_plan": (
            "【修复方案】在安装脚本中补充安装缺失的依赖包\n\n"
            "修改文件: scripts/install.sh\n"
            "  在包安装步骤中追加: apt-get install -y libssl3 openssl\n\n"
            "同时在 requirements 文档中明确标注运行时依赖：\n"
            "  docs/requirements.md: 新增 libssl3 >= 3.0.0 运行时依赖说明"
        ),
        "files": ["scripts/install.sh", "docs/requirements.md"],
        "commit_msg_body": "错误现象: 应用启动时报 libssl.so.3 共享库缺失错误\n根因判断: 目标系统缺少 OpenSSL 3.x 运行时库，仅有 1.1 版本\n修复内容: 在 install.sh 中补充 apt-get install libssl3，并更新依赖文档",
    },
    {
        "type": "permission_error",
        "root_cause": (
            "【根因分析】文件权限不足\n\n"
            "服务账户无法读取应用必要的配置文件和写入日志目录：\n\n"
            "1. 应用以 appuser 服务账户运行（安全隔离策略）\n"
            "2. 配置文件 /etc/app/config.json 所有者为 root，权限为 600\n"
            "3. 日志目录 /var/log/app/ 所有者为 root，appuser 无写入权限\n"
            "4. 应用启动时因权限拒绝而退出\n\n"
            "证据链：\n"
            "- 错误日志: PermissionError: [Errno 13] Permission denied: '/etc/app/config.json'\n"
            "- ls -la 输出: -rw------- root root /etc/app/config.json\n"
            "- id appuser: uid=1001(appuser) gid=1001(appuser)"
        ),
        "fix_plan": (
            "【修复方案】修正配置文件和日志目录的权限\n\n"
            "修改文件: scripts/install.sh\n"
            "  在服务启动前追加以下命令:\n"
            "  chown -R appuser:appuser /etc/app/\n"
            "  chmod 640 /etc/app/config.json\n"
            "  chown -R appuser:appuser /var/log/app/\n"
            "  chmod 750 /var/log/app/"
        ),
        "files": ["scripts/install.sh"],
        "commit_msg_body": "错误现象: 服务以 appuser 启动时读取配置文件及写入日志目录均报 Permission denied\n根因判断: 安装脚本未正确设置 /etc/app/ 和 /var/log/app/ 的所有者及权限\n修复内容: 在 install.sh 服务启动前追加 chown/chmod 权限修正命令",
    },
    {
        "type": "config_error",
        "root_cause": (
            "【根因分析】配置文件格式错误\n\n"
            "应用启动时解析配置文件失败，导致初始化中断：\n\n"
            "1. 配置文件 config/app.yaml 中数据库连接字符串格式不正确\n"
            "2. 预期格式: postgresql://user:pass@host:5432/dbname\n"
            "3. 实际配置: postgres://user:pass@host/dbname（缺少端口，协议名称不匹配）\n"
            "4. SQLAlchemy 引擎初始化时抛出 ArgumentError\n\n"
            "证据链：\n"
            "- 错误日志: sqlalchemy.exc.ArgumentError: Could not parse rfc1738 URL\n"
            "- 配置文件内容: database.url: postgres://appuser:***@db-host/appdb\n"
            "- 预期值: postgresql://appuser:***@db-host:5432/appdb"
        ),
        "fix_plan": (
            "【修复方案】修正数据库连接 URL 格式\n\n"
            "修改文件: config/app.yaml\n"
            "  database.url: postgres://appuser:{{DB_PASS}}@db-host/appdb\n"
            "  →  database.url: postgresql://appuser:{{DB_PASS}}@db-host:5432/appdb\n\n"
            "修改文件: config/app.yaml.template（模板同步更新）\n\n"
            "补充单元测试: tests/test_config.py，验证 URL 解析正确性。"
        ),
        "files": ["config/app.yaml", "config/app.yaml.template", "tests/test_config.py"],
        "commit_msg_body": "错误现象: 应用启动时 SQLAlchemy 报 Could not parse rfc1738 URL 错误\n根因判断: config/app.yaml 中 database.url 使用了 postgres:// 而非 postgresql://，且缺少端口号\n修复内容: 修正 URL scheme 为 postgresql:// 并补充 :5432 端口，同步更新配置模板",
    },
]

_ROLLBACK_LOGS = [
    "[回滚] 验证和修复均告失败，开始执行虚拟机回滚...",
    "[回滚] 定位基础快照: snap-{snap_id}",
    "[回滚] 正在停止当前所有服务...",
    "[回滚] 正在恢复虚拟机至快照状态...",
    "[回滚] 恢复进度: 100%",
    "[回滚] 虚拟机已回滚至初始干净状态",
    "[回滚] 回滚完成，请人工排查问题后手动重试",
]


# ---------------------------------------------------------------------------
# WorkflowEngine
# ---------------------------------------------------------------------------

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

    Features (设计文档 4.4.4):
      - Credit monitoring & auto account switching
      - AI provider failover chain (MuleRun -> ChatGPT -> Qwen)
      - Staged commits (commit after each meaningful fix step)
      - Failure classification (8 enum codes)
      - Progress context sync on account switch
      - Enhanced notifications with failure code & AI summary
    """

    STEP_TIMEOUT = 30 * 60      # 30 minutes per step
    TOTAL_TIMEOUT = 4 * 60 * 60 # 4 hours total
    CREDIT_COST_PER_CALL = 5.0  # simulated credit cost per AI call

    def __init__(self, app, run_id: int):
        self.app = app
        self.run_id = run_id
        self._thread = None
        self._start_wall = None
        self._current_credential = None  # active AI account

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
        log = RunLog(
            run_id=run.id,
            round_number=round_number,
            phase=phase,
            content=content,
            log_type=log_type,
            created_at=datetime.utcnow(),
        )
        session.add(log)
        session.commit()

    def _fmt(self, template: str, ctx: dict) -> str:
        """Safe format - unknown keys remain as-is."""
        try:
            return template.format(**ctx)
        except KeyError:
            return template

    def _build_ctx(self, run) -> dict:
        """Build a template context from the run's vm_info and project."""
        info = run.vm_info or {}
        project_name = run.project.name if run.project else "app"
        service_name = project_name.lower().replace(" ", "-")
        return {
            "ip": info.get("ip", "10.0.1.42"),
            "hostname": info.get("hostname", f"vm-{run.id:04d}"),
            "cpu_cores": info.get("specs", {}).get("cpu", 4),
            "memory": info.get("specs", {}).get("memory", 8),
            "disk": info.get("specs", {}).get("disk", 100),
            "elapsed": random.randint(18, 35),
            "project": project_name,
            "service": service_name,
            "repo_url": run.project.repo_url if run.project else "https://git.example.com/repo",
            "branch": run.branch_name or "main",
            "commit_hash": f"{random.randint(0x1000000, 0xfffffff):07x}",
            "snap_id": f"{random.randint(100000, 999999)}",
            "size": random.randint(800, 2500),
            "compressed": random.randint(300, 700),
            "pkg_size": random.randint(120, 480),
            "port": info.get("port", 8080),
            "pid": random.randint(10000, 32767),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _emit_logs(self, session, run, templates, phase, log_type, ctx, round_number=1, delay=(0.05, 0.15)):
        """Emit a list of log line templates with small delays between them."""
        for tpl in templates:
            line = self._fmt(tpl, ctx)
            self._add_log(session, run, phase, line, log_type, round_number)
            _rnd(*delay)

    def _total_elapsed(self):
        return time.time() - self._start_wall if self._start_wall else 0

    # ------------------------------------------------------------------
    # Credit monitoring & account pool (设计文档 4.4.4)
    # ------------------------------------------------------------------

    def _select_ai_account(self, session, run, round_number):
        """
        Select the best available AI account following the failover chain.
        Returns a ServiceCredential or None.
        """
        from models import ServiceCredential, CreditLog
        for service_type in ServiceCredential.AI_FAILOVER_CHAIN:
            accounts = ServiceCredential.query.filter_by(
                service_type=service_type, enabled=True
            ).order_by(ServiceCredential.priority).all()
            for acct in accounts:
                if acct.credit_balance >= acct.credit_threshold:
                    # Check if we're switching from a different account
                    if self._current_credential and self._current_credential.id != acct.id:
                        self._add_log(session, run, "ai_analyze",
                            f"[账户切换] {self._current_credential.service_type}/{self._current_credential.account}"
                            f" -> {acct.service_type}/{acct.account} (积分不足，自动切换)",
                            "account_switch", round_number)
                        # Save progress context for continuity
                        self._save_progress_context(session, run, round_number,
                                                    self._current_credential, acct)
                        # Log the switch event
                        switch_log = CreditLog(
                            credential_id=acct.id, run_id=run.id,
                            event_type="switch",
                            amount=0, balance_after=acct.credit_balance,
                            detail=f"从 {self._current_credential.account} 切换到 {acct.account}",
                        )
                        session.add(switch_log)
                        session.commit()
                    self._current_credential = acct
                    return acct
                else:
                    # Low balance alert
                    self._add_log(session, run, "ai_analyze",
                        f"[积分告警] {service_type}/{acct.account} 积分余额 {acct.credit_balance:.1f}"
                        f" 低于阈值 {acct.credit_threshold:.1f}，跳过",
                        "credit", round_number)
        return None

    def _deduct_credit(self, session, run, credential, amount, round_number):
        """Deduct credits and log the event."""
        from models import CreditLog
        credential.credit_balance = max(0, credential.credit_balance - amount)
        credential.last_used_at = datetime.utcnow()
        log = CreditLog(
            credential_id=credential.id, run_id=run.id,
            event_type="deduct", amount=amount,
            balance_after=credential.credit_balance,
            detail=f"第{round_number}轮 AI 调用消耗 {amount} 积分",
        )
        session.add(log)
        session.commit()
        self._add_log(session, run, "ai_analyze",
            f"[积分] {credential.service_type}/{credential.account} "
            f"消耗 {amount} 积分，剩余 {credential.credit_balance:.1f}",
            "credit", round_number)

    def _save_progress_context(self, session, run, round_number, from_cred, to_cred):
        """Save conversation context when switching accounts (进度同步机制)."""
        from models import ProgressContext, AIAnalysis
        # Gather completed analyses as context
        analyses = AIAnalysis.query.filter_by(run_id=run.id).order_by(
            AIAnalysis.round_number
        ).all()
        completed_steps = []
        pending_issues = []
        for a in analyses:
            completed_steps.append(f"第{a.round_number}轮: {(a.root_cause or '').split(chr(10))[0]}")
        pending_issues.append(f"当前第{round_number}轮验证失败，需继续分析修复")
        project_name = run.project.name if run.project else "unknown"
        task_summary = (
            f"项目 [{project_name}] 自动化测试运行 #{run.id}，"
            f"当前第{round_number}轮，已完成{len(analyses)}次AI分析修复"
        )
        context_prompt = (
            f"你正在接手一个自动化测试修复任务。\n"
            f"项目: {project_name}\n"
            f"运行ID: #{run.id}\n"
            f"当前轮次: 第{round_number}轮\n"
            f"已完成步骤:\n" +
            "\n".join(f"  - {s}" for s in completed_steps) +
            f"\n待处理:\n" +
            "\n".join(f"  - {p}" for p in pending_issues) +
            f"\n请继续分析当前轮次的验证失败日志并提供修复方案。"
        )
        ctx = ProgressContext(
            run_id=run.id,
            round_number=round_number,
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
    # Notification helper (设计文档 4.6 增强)
    # ------------------------------------------------------------------

    def _send_notification(self, session, run, final_status, all_analyses, last_scenario=None):
        """Build and log enhanced notification with failure code + AI summary."""
        from models import NotifyConfig
        cfg = NotifyConfig.query.first()
        if not cfg or not cfg.enabled or not cfg.webhook_url:
            return
        project_name = run.project.name if run.project else "unknown"
        if final_status == "success" and not cfg.notify_on_success:
            return
        if final_status == "failed" and not cfg.notify_on_failure:
            return

        # Build notification payload
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
        # Log notification (simulated send)
        self._add_log(session, run, "notify",
            f"[通知] 发送微信通知至 {cfg.webhook_url}\n{msg}",
            "install", run.current_retry or 1)

    # ------------------------------------------------------------------
    # State machine steps
    # ------------------------------------------------------------------

    def _step_init_vm(self, session, run, ctx):
        self._set_status(session, run, "init_vm")
        _rnd(1.5, 2.5)
        self._emit_logs(session, run, _VM_INIT_LOGS, "init_vm", "install", ctx)
        self._add_log(session, run, "init_vm", "[初始化] 虚拟机准备就绪，正在进行下一步...", "install", ctx.get("round_number", 1))

    def _step_snapshot(self, session, run, ctx):
        self._set_status(session, run, "snapshot")
        _rnd(1.0, 2.0)
        self._emit_logs(session, run, _SNAPSHOT_LOGS, "snapshot", "install", ctx)

    def _step_pull_code(self, session, run, ctx):
        self._set_status(session, run, "code_pull")
        _rnd(1.0, 2.0)
        self._emit_logs(session, run, _CODE_PULL_LOGS, "code_pull", "install", ctx)

    def _step_upload(self, session, run, ctx):
        self._set_status(session, run, "upload")
        _rnd(0.5, 1.5)
        self._emit_logs(session, run, _UPLOAD_LOGS, "upload", "install", ctx)

    def _step_install(self, session, run, ctx, round_number=1):
        self._set_status(session, run, "install")
        _rnd(1.5, 3.0)
        project = (run.project.name if run.project else "").lower()
        if "java" in project or "spring" in project:
            templates = _INSTALL_LOGS_JAVA
        elif "node" in project or "js" in project or "vue" in project or "react" in project:
            templates = _INSTALL_LOGS_NODE
        else:
            templates = _INSTALL_LOGS_PYTHON
        self._emit_logs(session, run, templates, "install", "install", ctx, round_number)
        _rnd(0.5, 1.0)
        self._add_log(session, run, "install", f"[安装] 第 {round_number} 轮安装完成", "install", round_number)

    def _step_verify(self, session, run, ctx, round_number=1, force_fail_scenario=None):
        """Returns (passed: bool, scenario: dict|None)."""
        self._set_status(session, run, "verify")
        _rnd(1.0, 2.0)

        if force_fail_scenario is not None:
            scenario_key = force_fail_scenario["type"]
        else:
            scenario_key = None

        should_fail = (round_number <= random.randint(1, 2)) and (run.current_retry < run.retry_count)

        if should_fail:
            scenario = random.choice(_FAILURE_SCENARIOS)
            if scenario["type"] == "port_conflict":
                fail_logs = _VERIFY_FAIL_LOGS_PORT
            elif scenario["type"] == "missing_dependency":
                fail_logs = _VERIFY_FAIL_LOGS_DEPENDENCY
            else:
                fail_logs = _VERIFY_FAIL_LOGS_SERVICE
            self._emit_logs(session, run, fail_logs, "verify", "verify", ctx, round_number)
            return False, scenario
        else:
            self._emit_logs(session, run, _VERIFY_PASS_LOGS, "verify", "verify", ctx, round_number)
            return True, None

    def _step_ai_analyze(self, session, run, scenario: dict, round_number: int):
        """AI analysis with credit monitoring & failover chain."""
        from models import AIAnalysis, FailureCode
        self._set_status(session, run, "ai_analyze")
        _rnd(1.5, 2.5)

        # --- Credit check & account selection ---
        acct = self._select_ai_account(session, run, round_number)
        if acct:
            self._add_log(session, run, "ai_analyze",
                f"[AI分析] 使用 {acct.service_type}/{acct.account} (积分: {acct.credit_balance:.1f})",
                "ai_analysis", round_number)
        else:
            self._add_log(session, run, "ai_analyze",
                "[AI分析] 警告: 所有 AI 账户积分不足或不可用，使用内置分析",
                "ai_analysis", round_number)

        # Log the AI analysis process
        analysis_log_lines = [
            f"[AI分析] 第 {round_number} 轮验证失败，启动 AI 日志分析...",
            "[AI分析] 正在收集安装日志、验证日志和系统状态...",
            "[AI分析] 调用大语言模型进行根因分析...",
            "[AI分析] 模型推理中，请稍候...",
        ]
        for line in analysis_log_lines:
            self._add_log(session, run, "ai_analyze", line, "ai_analysis", round_number)
            _rnd(0.1, 0.3)

        _rnd(1.0, 2.0)

        # --- Deduct credits ---
        if acct:
            self._deduct_credit(session, run, acct, self.CREDIT_COST_PER_CALL, round_number)

        # --- Classify failure ---
        failure_code = FailureCode.from_scenario_type(scenario["type"])
        run.failure_code = failure_code.value
        session.commit()
        self._add_log(session, run, "ai_analyze",
            f"[AI分析] 失败分类: {failure_code.value} ({failure_code.name})",
            "ai_analysis", round_number)

        # Build commit message
        commit_msg = (
            f"[AI-FIX] 第{round_number}轮自动修复\n\n"
            f"{scenario['commit_msg_body']}"
        )

        analysis = AIAnalysis(
            run_id=run.id,
            round_number=round_number,
            root_cause=scenario["root_cause"],
            fix_plan=scenario["fix_plan"],
            files_modified=scenario["files"],
            commit_message=commit_msg,
            created_at=datetime.utcnow(),
        )
        session.add(analysis)
        session.commit()

        self._add_log(session, run, "ai_analyze",
                      f"[AI分析] 分析完成\n\n{scenario['root_cause']}\n\n{scenario['fix_plan']}",
                      "ai_analysis", round_number)
        return analysis

    def _step_ai_fix(self, session, run, scenario: dict, analysis, round_number: int, ctx: dict):
        """AI fix with staged commits (阶段性提交)."""
        self._set_status(session, run, "ai_fix")
        _rnd(1.0, 2.0)

        fix_branch = f"fix/auto-repair-{ctx['project']}-{int(time.time())}"
        fix_log_lines = [
            f"[AI修复] 根据分析结果，开始自动修复 (第 {round_number} 轮)...",
            f"[AI修复] 创建修复分支: {fix_branch}",
            "[AI修复] 正在应用代码变更...",
        ]
        for line in fix_log_lines:
            self._add_log(session, run, "ai_fix", line, "ai_fix", round_number)
            _rnd(0.05, 0.15)

        # --- Staged commits: commit per file (阶段性提交策略) ---
        for i, f in enumerate(scenario["files"]):
            self._add_log(session, run, "ai_fix",
                f"[AI修复]   修改文件: {f}", "ai_fix", round_number)
            _rnd(0.1, 0.2)
            staged_hash = f"{random.randint(0x1000000, 0xfffffff):07x}"
            self._add_log(session, run, "ai_fix",
                f"[AI修复]   阶段性提交 ({i+1}/{len(scenario['files'])}): "
                f"git commit -m '[AI-FIX] 修改 {f}' -> {staged_hash}",
                "ai_fix", round_number)
            _rnd(0.05, 0.1)

        final_lines = [
            "[AI修复] 所有文件修改已逐步提交 (Staged Commit)",
            f"[AI修复] 合并提交: {analysis.commit_message.splitlines()[0]}",
            f"[AI修复] Commit hash: {ctx['commit_hash']}",
            "[AI修复] 推送修复分支到远程仓库...",
            "[AI修复] 修复分支已推送，准备重新安装验证",
        ]
        for line in final_lines:
            self._add_log(session, run, "ai_fix", line, "ai_fix", round_number)
            _rnd(0.05, 0.15)

        run.branch_name = fix_branch
        session.commit()

    def _step_rollback(self, session, run, ctx):
        self._set_status(session, run, "rollback")
        _rnd(1.5, 2.5)
        self._emit_logs(session, run, _ROLLBACK_LOGS, "rollback", "install", ctx)

    # ------------------------------------------------------------------
    # Verify result recording
    # ------------------------------------------------------------------

    def _record_verify_results(self, session, run, passed: bool, scenario, round_number: int):
        from models import VerifyResult
        checks = ["exit_code", "log_keywords", "service_status", "port_listen", "api_health"]
        if passed:
            for check in checks:
                vr = VerifyResult(
                    run_id=run.id, round_number=round_number, check_name=check,
                    passed=True, detail="检查通过", created_at=datetime.utcnow(),
                )
                session.add(vr)
        else:
            scenario_type = scenario["type"] if scenario else "unknown"
            fail_map = {
                "port_conflict": {"port_listen": "端口未监听 (8080 被占用)", "api_health": "跳过 (依赖端口监听)"},
                "missing_dependency": {"exit_code": "退出码 127", "log_keywords": "未找到启动成功关键字", "service_status": "服务状态 failed"},
                "permission_error": {"exit_code": "退出码 1", "log_keywords": "未找到启动成功关键字", "service_status": "服务状态 failed"},
                "config_error": {"exit_code": "退出码 1", "log_keywords": "未找到启动成功关键字", "service_status": "服务状态 failed"},
            }
            fails = fail_map.get(scenario_type, {})
            for check in checks:
                if check in fails:
                    vr = VerifyResult(run_id=run.id, round_number=round_number, check_name=check,
                                      passed=False, detail=fails[check], created_at=datetime.utcnow())
                else:
                    vr = VerifyResult(run_id=run.id, round_number=round_number, check_name=check,
                                      passed=True, detail="检查通过", created_at=datetime.utcnow())
                session.add(vr)
        session.commit()

    # ------------------------------------------------------------------
    # Report generation (with failure_code)
    # ------------------------------------------------------------------

    def _generate_report(self, session, run, final_status: str, all_analyses):
        from models import TestReport, VerifyResult
        if run.report:
            return

        last_round = run.current_retry
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
            run_id=run.id,
            summary=summary,
            ai_fixes=ai_fixes_summary,
            verify_results=vr_dict,
            branch_url=branch_url,
            commits=commits,
            final_status=final_status,
            created_at=datetime.utcnow(),
        )
        session.add(report)
        session.commit()

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

            run.start_time = datetime.utcnow()
            run.status = "init_vm"
            session.commit()

            ctx = self._build_ctx(run)
            all_analyses = []

            try:
                # --- Fixed initial steps ---
                self._step_init_vm(session, run, ctx)
                if self._total_elapsed() > self.TOTAL_TIMEOUT:
                    raise TimeoutError("超出总时限 4 小时")

                self._step_snapshot(session, run, ctx)
                self._step_pull_code(session, run, ctx)
                self._step_upload(session, run, ctx)

                round_number = 1
                last_scenario = None

                while run.current_retry <= run.retry_count:
                    if self._total_elapsed() > self.TOTAL_TIMEOUT:
                        raise TimeoutError("超出总时限 4 小时")

                    run.current_retry = round_number
                    session.commit()

                    # Install
                    self._step_install(session, run, ctx, round_number)

                    # Verify
                    passed, scenario = self._step_verify(session, run, ctx, round_number, last_scenario)
                    self._record_verify_results(session, run, passed, scenario, round_number)

                    if passed:
                        # SUCCESS path
                        run.failure_code = ""
                        self._generate_report(session, run, "success", all_analyses)
                        run.status = "success"
                        run.end_time = datetime.utcnow()
                        session.commit()
                        self._send_notification(session, run, "success", all_analyses)
                        return

                    # Verify failed - AI analyze & fix loop
                    last_scenario = scenario
                    analysis = self._step_ai_analyze(session, run, scenario, round_number)
                    all_analyses.append(analysis)
                    self._step_ai_fix(session, run, scenario, analysis, round_number, ctx)

                    round_number += 1
                    if round_number > run.retry_count:
                        break

                # Exhausted retries -> rollback & fail
                run.failure_code = FailureCode.AI_FIX_FAILED.value
                self._step_rollback(session, run, ctx)
                self._generate_report(session, run, "failed", all_analyses)
                run.status = "failed"
                run.end_time = datetime.utcnow()
                session.commit()
                self._send_notification(session, run, "failed", all_analyses, last_scenario)

            except TimeoutError as e:
                run.status = "failed"
                run.failure_code = FailureCode.ENV_ERROR.value
                run.end_time = datetime.utcnow()
                self._add_log(session, run, "system", f"[超时] {e}", "install", run.current_retry or 1)
                session.commit()
                self._send_notification(session, run, "failed", all_analyses)
            except Exception as e:
                run.status = "failed"
                run.failure_code = FailureCode.UNKNOWN_ERROR.value
                run.end_time = datetime.utcnow()
                self._add_log(session, run, "system", f"[系统错误] {e}", "install", run.current_retry or 1)
                session.commit()
                self._send_notification(session, run, "failed", all_analyses)
                raise
