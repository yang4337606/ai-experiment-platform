"""
Flask application for AI Automated Experiment Platform (AI自动化实验平台)
"""
import random
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from models import (
    AIAnalysis, CreditLog, FailureCode, NotifyConfig, ProgressContext, Project,
    RunLog, RunStatus, ServiceCredential, TestReport, TestRun, VerifyResult,
    db, encrypt_value, decrypt_value,
)
from workflow import WorkflowEngine

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///experiment.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    CORS(app)
    db.init_app(app)

    with app.app_context():
        db.create_all()
        _seed_data(app)

    _register_routes(app)

    @app.route("/")
    def index():
        return render_template("index.html")

    return app


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def _seed_data(app):
    """Insert sample projects and completed runs if DB is empty."""
    if Project.query.count() > 0:
        return  # already seeded

    # --- Projects ---
    projects = [
        Project(
            name="SpringBoot用户服务",
            repo_url="https://git.example.com/platform/user-service",
            install_script="#!/bin/bash\nset -e\napt-get install -y openjdk-17-jdk\nmvn clean package -DskipTests\nsystemctl enable user-service && systemctl start user-service",
            verify_script="#!/bin/bash\nsystemctl is-active user-service\ncurl -sf http://127.0.0.1:8080/actuator/health",
            config={"port": 8080, "jvm_opts": "-Xms512m -Xmx1024m", "profiles": "prod"},
        ),
        Project(
            name="Python数据分析平台",
            repo_url="https://git.example.com/platform/data-analyzer",
            install_script="#!/bin/bash\nset -e\npython3 -m venv /opt/analyzer/venv\nsource /opt/analyzer/venv/bin/activate\npip install -r requirements.txt\npython manage.py db upgrade\ngsystemctl enable analyzer && systemctl start analyzer",
            verify_script="#!/bin/bash\nsystemctl is-active analyzer\ncurl -sf http://127.0.0.1:5000/health",
            config={"port": 5000, "workers": 4, "log_level": "info"},
        ),
        Project(
            name="Node.js前端网关",
            repo_url="https://git.example.com/platform/frontend-gateway",
            install_script="#!/bin/bash\nset -e\nnpm ci --production\nnpm run build\npm2 start ecosystem.config.js",
            verify_script="#!/bin/bash\npm2 status gateway | grep online\ncurl -sf http://127.0.0.1:3000/ping",
            config={"port": 3000, "node_env": "production", "cluster_mode": True},
        ),
    ]
    for p in projects:
        db.session.add(p)
    db.session.flush()

    # --- Completed runs (pre-seeded with realistic data) ---
    _seed_run_success(projects[0], app)
    _seed_run_success_with_fix(projects[1], app)
    _seed_run_failed(projects[2], app)
    _seed_run_inprogress(projects[0])

    # --- Notify config ---
    db.session.add(NotifyConfig(
        webhook_url="https://hooks.example.com/notify",
        enabled=False,
        notify_on_success=True,
        notify_on_failure=True,
        notify_on_error=True,
    ))

    db.session.commit()


def _make_log(run_id, round_number, phase, content, log_type, ts):
    return RunLog(run_id=run_id, round_number=round_number, phase=phase,
                  content=content, log_type=log_type, created_at=ts)


def _ts(base, delta_seconds):
    return base + timedelta(seconds=delta_seconds)


def _seed_run_success(project, app):
    """A clean first-run success with no AI fixes needed."""
    start = datetime.utcnow() - timedelta(hours=3)
    run = TestRun(
        project_id=project.id,
        status="success",
        branch_name="main",
        start_time=start,
        end_time=_ts(start, 280),
        retry_count=10,
        current_retry=1,
        vm_info={"ip": "10.0.1.101", "hostname": "vm-0001", "specs": {"cpu": 4, "memory": 8, "disk": 100}, "port": 8080},
        created_at=start,
    )
    db.session.add(run)
    db.session.flush()

    t = 0
    logs = [
        (0,  "init_vm",   "[初始化] 正在向资源池申请虚拟机资源...",                   "install"),
        (3,  "init_vm",   "[网络] IP 地址已分配: 10.0.1.101",                         "install"),
        (6,  "init_vm",   "[SSH] SSH 服务启动成功，监听端口 22",                       "install"),
        (10, "init_vm",   "[系统] OS: Ubuntu 22.04 LTS  CPU: 4 vCPU  内存: 8GB",      "install"),
        (15, "snapshot",  "[快照] 快照 snap-831920 创建成功",                          "install"),
        (20, "code_pull", "[代码拉取] git clone 完成，HEAD -> main",                   "install"),
        (25, "upload",    "[上传] 部署包上传完成",                                      "install"),
        (30, "install",   "[安装] 正在安装 JDK 17...",                                  "install"),
        (50, "install",   "[安装] [INFO] BUILD SUCCESS",                               "install"),
        (70, "install",   "[安装] systemctl start user-service",                       "install"),
        (80, "install",   "[安装] 第 1 轮安装完成",                                     "install"),
        (85, "verify",    "[验证][服务状态] Active: active (running)  ✓ 通过",         "verify"),
        (88, "verify",    "[验证][端口监听] 端口 8080 已正常监听  ✓ 通过",              "verify"),
        (92, "verify",    "[验证][API健康] HTTP 状态码: 200  ✓ 通过",                   "verify"),
        (95, "verify",    "[验证] 所有验证检查项均通过！",                               "verify"),
    ]
    for delta, phase, content, lt in logs:
        db.session.add(_make_log(run.id, 1, phase, content, lt, _ts(start, delta)))

    for check, detail in [("exit_code","检查通过"),("log_keywords","检查通过"),
                           ("service_status","检查通过"),("port_listen","检查通过"),("api_health","检查通过")]:
        db.session.add(VerifyResult(run_id=run.id, round_number=1, check_name=check,
                                    passed=True, detail=detail, created_at=_ts(start, 92)))

    db.session.add(TestReport(
        run_id=run.id,
        summary="测试运行成功完成。\n共经历 1 轮，无需 AI 修复，所有验证检查项均通过，服务运行正常。",
        ai_fixes=[],
        verify_results={"exit_code":{"passed":True},"log_keywords":{"passed":True},
                        "service_status":{"passed":True},"port_listen":{"passed":True},"api_health":{"passed":True}},
        branch_url="https://git.example.com/platform/user-service/tree/main",
        commits=[],
        final_status="success",
        created_at=_ts(start, 280),
    ))


def _seed_run_success_with_fix(project, app):
    """Run that failed on round 1, AI fixed it, succeeded on round 2."""
    start = datetime.utcnow() - timedelta(hours=1, minutes=30)
    run = TestRun(
        project_id=project.id,
        status="success",
        branch_name="fix/auto-repair-python数据分析平台-1746950000",
        start_time=start,
        end_time=_ts(start, 520),
        retry_count=10,
        current_retry=2,
        vm_info={"ip": "10.0.1.102", "hostname": "vm-0002", "specs": {"cpu": 4, "memory": 8, "disk": 100}, "port": 5000},
        created_at=start,
    )
    db.session.add(run)
    db.session.flush()

    logs = [
        (0,   1, "init_vm",    "[初始化] 正在向资源池申请虚拟机资源...",                             "install"),
        (5,   1, "init_vm",    "[网络] IP 地址已分配: 10.0.1.102",                                  "install"),
        (12,  1, "snapshot",   "[快照] 快照 snap-294711 创建成功",                                  "install"),
        (18,  1, "code_pull",  "[代码拉取] git clone 完成，HEAD -> main",                            "install"),
        (22,  1, "upload",     "[上传] 部署包上传完成",                                               "install"),
        (25,  1, "install",    "[安装] 检查 Python 版本: Python 3.11.6",                             "install"),
        (30,  1, "install",    "[安装] pip install -r requirements.txt ... 完成",                    "install"),
        (55,  1, "install",    "[安装] systemctl start analyzer",                                    "install"),
        (60,  1, "install",    "[安装] 第 1 轮安装完成",                                              "install"),
        (62,  1, "verify",     "[验证][退出码] 脚本退出码: 127  ✗ 失败",                              "verify"),
        (64,  1, "verify",     "[验证][退出码] stderr: libssl.so.3: 共享库加载失败",                  "verify"),
        (66,  1, "verify",     "[验证][服务状态] Active: failed  ✗ 失败",                            "verify"),
        (68,  1, "verify",     "[验证] 验证失败: 3 个检查项未通过 (exit_code, log_keywords, service_status)", "verify"),
        (70,  1, "ai_analyze", "[AI分析] 第 1 轮验证失败，启动 AI 日志分析...",                       "ai_analysis"),
        (75,  1, "ai_analyze", "[AI分析] 调用大语言模型进行根因分析...",                              "ai_analysis"),
        (90,  1, "ai_analyze", "[AI分析] 分析完成\n\n【根因分析】缺少运行时共享库\n应用启动时报 libssl.so.3 缺失，目标系统仅有 OpenSSL 1.1。", "ai_analysis"),
        (95,  1, "ai_fix",     "[AI修复] 创建修复分支: fix/auto-repair-python数据分析平台-1746950000", "ai_fix"),
        (97,  1, "ai_fix",     "[AI修复]   修改文件: scripts/install.sh",                             "ai_fix"),
        (100, 1, "ai_fix",     "[AI修复] 推送修复分支到远程仓库",                                    "ai_fix"),
        (110, 2, "install",    "[安装] 安装依赖 libssl3...",                                          "install"),
        (130, 2, "install",    "[安装] 第 2 轮安装完成",                                              "install"),
        (135, 2, "verify",     "[验证][服务状态] Active: active (running)  ✓ 通过",                  "verify"),
        (138, 2, "verify",     "[验证][端口监听] 端口 5000 已正常监听  ✓ 通过",                       "verify"),
        (142, 2, "verify",     "[验证][API健康] HTTP 状态码: 200  ✓ 通过",                            "verify"),
        (145, 2, "verify",     "[验证] 所有验证检查项均通过！",                                       "verify"),
    ]
    for delta, rnd, phase, content, lt in logs:
        db.session.add(_make_log(run.id, rnd, phase, content, lt, _ts(start, delta)))

    for check, detail in [("exit_code","退出码 127"),("log_keywords","未找到启动成功关键字"),
                           ("service_status","服务状态 failed"),("port_listen","跳过"),("api_health","跳过")]:
        passed = check not in ("exit_code","log_keywords","service_status")
        db.session.add(VerifyResult(run_id=run.id, round_number=1, check_name=check,
                                    passed=passed, detail=detail, created_at=_ts(start, 68)))
    for check in ["exit_code","log_keywords","service_status","port_listen","api_health"]:
        db.session.add(VerifyResult(run_id=run.id, round_number=2, check_name=check,
                                    passed=True, detail="检查通过", created_at=_ts(start, 140)))

    analysis = AIAnalysis(
        run_id=run.id,
        round_number=1,
        root_cause="【根因分析】缺少运行时共享库\n\n应用编译时链接了 OpenSSL 3.x (libssl.so.3)，目标系统仅安装了 OpenSSL 1.1，动态链接器报错: cannot open shared object file。",
        fix_plan="【修复方案】在安装脚本中补充安装缺失的依赖包\n\n修改文件: scripts/install.sh\n  追加: apt-get install -y libssl3 openssl",
        files_modified=["scripts/install.sh", "docs/requirements.md"],
        commit_message="[AI-FIX] 第1轮自动修复\n\n错误现象: 应用启动时报 libssl.so.3 共享库缺失错误\n根因判断: 目标系统缺少 OpenSSL 3.x 运行时库\n修复内容: 在 install.sh 中补充 apt-get install libssl3",
        created_at=_ts(start, 90),
    )
    db.session.add(analysis)

    db.session.add(TestReport(
        run_id=run.id,
        summary="测试运行成功完成。\n共经历 2 轮重试，AI 自动修复了 1 个问题。\n所有验证检查项均通过，服务运行正常。",
        ai_fixes=[{
            "round": 1,
            "root_cause_summary": "【根因分析】缺少运行时共享库",
            "files_modified": ["scripts/install.sh", "docs/requirements.md"],
            "commit_message": "[AI-FIX] 第1轮自动修复",
        }],
        verify_results={"exit_code":{"passed":True},"log_keywords":{"passed":True},
                        "service_status":{"passed":True},"port_listen":{"passed":True},"api_health":{"passed":True}},
        branch_url="https://git.example.com/platform/data-analyzer/tree/fix/auto-repair-python数据分析平台-1746950000",
        commits=[{"hash":"a3f91bc","message":"[AI-FIX] 第1轮自动修复","author":"AI-AutoFix Bot","time":_ts(start,97).isoformat()}],
        final_status="success",
        created_at=_ts(start, 520),
    ))


def _seed_run_failed(project, app):
    """Run that exhausted retries and was rolled back."""
    start = datetime.utcnow() - timedelta(minutes=45)
    run = TestRun(
        project_id=project.id,
        status="failed",
        branch_name="fix/auto-repair-node.js前端网关-1746960000",
        start_time=start,
        end_time=_ts(start, 900),
        retry_count=10,
        current_retry=3,
        vm_info={"ip": "10.0.1.103", "hostname": "vm-0003", "specs": {"cpu": 2, "memory": 4, "disk": 50}, "port": 3000},
        created_at=start,
    )
    db.session.add(run)
    db.session.flush()

    t_logs = [
        (0,   1, "init_vm",    "[初始化] 虚拟机初始化完成",                                           "install"),
        (20,  1, "install",    "[安装] npm ci --production ... 完成",                                 "install"),
        (40,  1, "verify",     "[验证][服务状态] Active: failed  ✗ 失败",                              "verify"),
        (42,  1, "verify",     "[验证] 验证失败: 端口 3000 未监听",                                    "verify"),
        (45,  1, "ai_analyze", "[AI分析] 第 1 轮验证失败，启动 AI 分析...",                            "ai_analysis"),
        (60,  1, "ai_analyze", "[AI分析] 分析完成: 端口冲突问题",                                     "ai_analysis"),
        (62,  1, "ai_fix",     "[AI修复] 修改端口配置: 3000 → 3010",                                  "ai_fix"),
        (80,  2, "install",    "[安装] 第 2 轮安装完成",                                               "install"),
        (95,  2, "verify",     "[验证][端口监听] 端口 3010 未监听  ✗ 失败",                             "verify"),
        (98,  2, "ai_analyze", "[AI分析] 第 2 轮验证失败，再次分析...",                                "ai_analysis"),
        (115, 2, "ai_fix",     "[AI修复] 修改 PM2 配置，指定 PORT 环境变量",                           "ai_fix"),
        (130, 3, "install",    "[安装] 第 3 轮安装完成",                                               "install"),
        (145, 3, "verify",     "[验证][服务状态] Active: failed  ✗ 失败",                              "verify"),
        (148, 3, "verify",     "[验证] 验证失败，已达最大重试轮次",                                    "verify"),
        (150, 3, "rollback",   "[回滚] 定位基础快照: snap-567890",                                    "install"),
        (160, 3, "rollback",   "[回滚] 虚拟机已回滚至初始干净状态",                                   "install"),
        (162, 3, "rollback",   "[回滚] 回滚完成，请人工排查问题后手动重试",                            "install"),
    ]
    for delta, rnd, phase, content, lt in t_logs:
        db.session.add(_make_log(run.id, rnd, phase, content, lt, _ts(start, delta)))

    for rnd in [1, 2, 3]:
        for check in ["exit_code","log_keywords","service_status","port_listen","api_health"]:
            db.session.add(VerifyResult(run_id=run.id, round_number=rnd, check_name=check,
                                        passed=False, detail="检查失败", created_at=_ts(start, 40 + rnd*50)))

    for rnd, root in [(1,"【根因分析】端口冲突问题\n3000 端口被 nginx 占用"),(2,"【根因分析】PM2 配置未正确传递 PORT 环境变量")]:
        db.session.add(AIAnalysis(
            run_id=run.id, round_number=rnd,
            root_cause=root,
            fix_plan="修改配置文件中的端口绑定设置",
            files_modified=["ecosystem.config.js"],
            commit_message=f"[AI-FIX] 第{rnd}轮自动修复\n\n错误现象: 端口监听失败\n根因判断: 端口冲突\n修复内容: 修改端口配置",
            created_at=_ts(start, 45 + rnd*50),
        ))

    db.session.add(TestReport(
        run_id=run.id,
        summary="测试运行失败。\n共尝试 3 轮，AI 分析了 2 个问题但修复未能解决所有验证失败项。\n已回滚虚拟机至初始快照状态，请人工介入排查。",
        ai_fixes=[
            {"round":1,"root_cause_summary":"【根因分析】端口冲突问题","files_modified":["ecosystem.config.js"],"commit_message":"[AI-FIX] 第1轮自动修复"},
            {"round":2,"root_cause_summary":"【根因分析】PM2 配置未正确传递 PORT","files_modified":["ecosystem.config.js"],"commit_message":"[AI-FIX] 第2轮自动修复"},
        ],
        verify_results={"exit_code":{"passed":False},"log_keywords":{"passed":False},
                        "service_status":{"passed":False},"port_listen":{"passed":False},"api_health":{"passed":False}},
        branch_url="https://git.example.com/platform/frontend-gateway/tree/fix/auto-repair-node.js前端网关-1746960000",
        commits=[
            {"hash":"b1e22fc","message":"[AI-FIX] 第1轮自动修复","author":"AI-AutoFix Bot","time":_ts(start,62).isoformat()},
            {"hash":"c3d44ab","message":"[AI-FIX] 第2轮自动修复","author":"AI-AutoFix Bot","time":_ts(start,115).isoformat()},
        ],
        final_status="failed",
        created_at=_ts(start, 900),
    ))


def _seed_run_inprogress(project):
    """A run currently in-progress (status=install, no workflow thread – just for UI demo)."""
    start = datetime.utcnow() - timedelta(minutes=5)
    run = TestRun(
        project_id=project.id,
        status="install",
        branch_name="main",
        start_time=start,
        retry_count=10,
        current_retry=1,
        vm_info={"ip": "10.0.1.104", "hostname": "vm-0004", "specs": {"cpu": 4, "memory": 8, "disk": 100}, "port": 8080},
        created_at=start,
    )
    db.session.add(run)
    db.session.flush()

    logs = [
        (0,  "init_vm",   "[初始化] 正在向资源池申请虚拟机资源...",  "install"),
        (5,  "init_vm",   "[网络] IP 地址已分配: 10.0.1.104",       "install"),
        (15, "snapshot",  "[快照] 快照创建成功",                     "install"),
        (22, "code_pull", "[代码拉取] git clone 完成",               "install"),
        (28, "upload",    "[上传] 部署包上传完成",                    "install"),
        (32, "install",   "[安装] 正在安装 JDK 17...",               "install"),
        (50, "install",   "[安装] [INFO] Compiling 142 source files...", "install"),
    ]
    for delta, phase, content, lt in logs:
        db.session.add(_make_log(run.id, 1, phase, content, lt, _ts(start, delta)))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _register_routes(app):

    # ---- Dashboard ----
    @app.route("/api/dashboard")
    def dashboard():
        total = TestRun.query.count()
        success = TestRun.query.filter_by(status="success").count()
        failed = TestRun.query.filter_by(status="failed").count()
        active_statuses = [s.value for s in RunStatus
                           if s not in (RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.PENDING)]
        active = TestRun.query.filter(TestRun.status.in_(active_statuses)).count()
        finished = TestRun.query.filter(
            TestRun.status.in_(["success", "failed"]),
            TestRun.current_retry > 0,
        ).all()
        avg_retries = (
            round(sum(r.current_retry for r in finished) / len(finished), 2)
            if finished else 0
        )
        success_rate = round(success / total * 100, 1) if total > 0 else 0
        return jsonify({
            "total_runs": total,
            "success": success,
            "failed": failed,
            "active_runs": active,
            "success_rate": success_rate,
            "avg_retries": avg_retries,
        })

    # ---- Projects ----
    @app.route("/api/projects", methods=["GET"])
    def list_projects():
        projects = Project.query.order_by(Project.created_at.desc()).all()
        return jsonify([p.to_dict() for p in projects])

    @app.route("/api/projects", methods=["POST"])
    def create_project():
        data = request.get_json(force=True)
        required = ["name", "repo_url", "install_script", "verify_script"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"缺少必填字段: {', '.join(missing)}"}), 400
        if Project.query.filter_by(name=data["name"]).first():
            return jsonify({"error": "项目名称已存在"}), 409
        p = Project(
            name=data["name"],
            repo_url=data["repo_url"],
            install_script=data["install_script"],
            verify_script=data["verify_script"],
            config=data.get("config", {}),
        )
        db.session.add(p)
        db.session.commit()
        return jsonify(p.to_dict()), 201

    @app.route("/api/projects/<int:pid>", methods=["GET"])
    def get_project(pid):
        p = Project.query.get_or_404(pid)
        return jsonify(p.to_dict())

    @app.route("/api/projects/<int:pid>", methods=["PUT"])
    def update_project(pid):
        p = Project.query.get_or_404(pid)
        data = request.get_json(force=True)
        for field in ["name", "repo_url", "install_script", "verify_script", "config"]:
            if field in data:
                setattr(p, field, data[field])
        db.session.commit()
        return jsonify(p.to_dict())

    @app.route("/api/projects/<int:pid>", methods=["DELETE"])
    def delete_project(pid):
        p = Project.query.get_or_404(pid)
        db.session.delete(p)
        db.session.commit()
        return jsonify({"message": "项目已删除"})

    # ---- Test Runs ----
    @app.route("/api/runs", methods=["GET"])
    def list_runs():
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        project_id = request.args.get("project_id", type=int)
        status = request.args.get("status")

        q = TestRun.query.order_by(TestRun.created_at.desc())
        if project_id:
            q = q.filter_by(project_id=project_id)
        if status:
            q = q.filter_by(status=status)

        runs = q.limit(per_page).offset((page - 1) * per_page).all()
        return jsonify([r.to_dict() for r in runs])

    @app.route("/api/runs", methods=["POST"])
    def create_run():
        data = request.get_json(force=True)
        project_id = data.get("project_id")
        if not project_id:
            return jsonify({"error": "缺少 project_id"}), 400
        project = Project.query.get(project_id)
        if not project:
            return jsonify({"error": "项目不存在"}), 404

        run = TestRun(
            project_id=project_id,
            status="pending",
            branch_name=data.get("branch_name", "main"),
            retry_count=data.get("retry_count", 10),
            current_retry=0,
            vm_info={},
            created_at=datetime.utcnow(),
        )
        db.session.add(run)
        db.session.flush()

        # Assign a VM IP and hostname for simulation
        run.vm_info = {
            "ip": f"10.0.1.{random.randint(10, 250)}",
            "hostname": f"vm-{run.id:04d}",
            "specs": {"cpu": 4, "memory": 8, "disk": 100},
            "port": project.config.get("port", 8080) if project.config else 8080,
        }
        db.session.commit()

        # Launch workflow in background
        engine = WorkflowEngine(app, run.id)
        engine.start()

        return jsonify(run.to_dict()), 201

    @app.route("/api/runs/<int:rid>", methods=["GET"])
    def get_run(rid):
        run = TestRun.query.get_or_404(rid)
        return jsonify(run.to_dict(include_related=True))

    @app.route("/api/runs/<int:rid>/logs", methods=["GET"])
    def get_run_logs(rid):
        TestRun.query.get_or_404(rid)
        round_number = request.args.get("round", type=int)
        phase = request.args.get("phase")
        log_type = request.args.get("log_type")

        q = RunLog.query.filter_by(run_id=rid)
        if round_number is not None:
            q = q.filter_by(round_number=round_number)
        if phase:
            q = q.filter_by(phase=phase)
        if log_type:
            q = q.filter_by(log_type=log_type)

        logs = q.order_by(RunLog.created_at).all()
        return jsonify([l.to_dict() for l in logs])

    @app.route("/api/runs/<int:rid>/report", methods=["GET"])
    def get_run_report(rid):
        run = TestRun.query.get_or_404(rid)
        if not run.report:
            return jsonify({"error": "报告尚未生成"}), 404
        return jsonify(run.report.to_dict())

    @app.route("/api/runs/<int:rid>/retry", methods=["POST"])
    def retry_run(rid):
        run = TestRun.query.get_or_404(rid)
        if run.status not in ("failed", "success"):
            return jsonify({"error": "只能对已完成的运行进行手动重试"}), 400
        # Reset the run
        run.status = "pending"
        run.start_time = None
        run.end_time = None
        run.current_retry = 0
        run.branch_name = run.project.repo_url and "main" or "main"
        db.session.commit()

        engine = WorkflowEngine(app, run.id)
        engine.start()
        return jsonify({"message": "重试已启动", "run": run.to_dict()})

    # ---- Notify Settings ----
    @app.route("/api/settings/notify", methods=["GET"])
    def get_notify():
        cfg = NotifyConfig.query.first()
        if not cfg:
            cfg = NotifyConfig()
            db.session.add(cfg)
            db.session.commit()
        return jsonify(cfg.to_dict())

    @app.route("/api/settings/notify", methods=["PUT"])
    def update_notify():
        cfg = NotifyConfig.query.first()
        if not cfg:
            cfg = NotifyConfig()
            db.session.add(cfg)
        data = request.get_json(force=True)
        for field in ["webhook_url", "enabled", "notify_on_success", "notify_on_failure", "notify_on_error"]:
            if field in data:
                setattr(cfg, field, data[field])
        db.session.commit()
        return jsonify(cfg.to_dict())

    @app.route("/api/notify/test", methods=["POST"])
    def test_notify():
        cfg = NotifyConfig.query.first()
        if not cfg or not cfg.webhook_url:
            return jsonify({"error": "未配置 Webhook URL"}), 400
        # Simulate sending (no real HTTP call)
        return jsonify({"message": f"测试通知已发送至 {cfg.webhook_url}", "success": True})

    # ---- Service Credentials (Account Pool) ----
    @app.route("/api/settings/credentials", methods=["GET"])
    def list_credentials():
        creds = ServiceCredential.query.order_by(
            ServiceCredential.service_type, ServiceCredential.priority
        ).all()
        # Group by service_type
        grouped = {}
        for c in creds:
            grouped.setdefault(c.service_type, []).append(c.to_dict())
        return jsonify(grouped)

    @app.route("/api/settings/credentials/<service_type>", methods=["GET"])
    def list_credentials_for_service(service_type):
        creds = ServiceCredential.query.filter_by(service_type=service_type).order_by(
            ServiceCredential.priority
        ).all()
        return jsonify([c.to_dict() for c in creds])

    @app.route("/api/settings/credentials", methods=["POST"])
    def create_credential():
        data = request.get_json(force=True)
        st = data.get("service_type", "")
        if st not in ServiceCredential.SERVICE_TYPES:
            return jsonify({"error": f"不支持的服务类型: {st}"}), 400
        if not data.get("account"):
            return jsonify({"error": "账号不能为空"}), 400
        cred = ServiceCredential(
            service_type=st,
            account=data["account"],
            encrypted_secret=encrypt_value(data.get("secret", "")),
            priority=data.get("priority", 0),
            credit_balance=data.get("credit_balance", 0.0),
            credit_threshold=data.get("credit_threshold", 10.0),
            extra=data.get("extra", {}),
            enabled=data.get("enabled", True),
        )
        db.session.add(cred)
        db.session.commit()
        return jsonify(cred.to_dict()), 201

    @app.route("/api/settings/credentials/<int:cred_id>", methods=["PUT"])
    def update_credential(cred_id):
        cred = ServiceCredential.query.get_or_404(cred_id)
        data = request.get_json(force=True)
        for field in ["account", "priority", "credit_balance", "credit_threshold", "extra", "enabled"]:
            if field in data:
                setattr(cred, field, data[field])
        if "secret" in data and data["secret"]:
            cred.encrypted_secret = encrypt_value(data["secret"])
        db.session.commit()
        return jsonify(cred.to_dict())

    @app.route("/api/settings/credentials/<int:cred_id>", methods=["DELETE"])
    def delete_credential(cred_id):
        cred = ServiceCredential.query.get_or_404(cred_id)
        db.session.delete(cred)
        db.session.commit()
        return jsonify({"message": "账号已删除"})

    @app.route("/api/settings/credentials/<int:cred_id>/test", methods=["POST"])
    def test_credential(cred_id):
        cred = ServiceCredential.query.get_or_404(cred_id)
        if not cred.account:
            return jsonify({"error": "未配置账号信息", "success": False}), 400
        return jsonify({"message": f"{cred.service_type} ({cred.account}) 连接测试成功", "success": True})

    # ---- Credit Monitoring ----
    @app.route("/api/credits/overview", methods=["GET"])
    def credits_overview():
        """Return credit status for all AI provider accounts."""
        ai_types = ServiceCredential.AI_FAILOVER_CHAIN
        result = {}
        for st in ai_types:
            accounts = ServiceCredential.query.filter_by(service_type=st, enabled=True).order_by(
                ServiceCredential.priority
            ).all()
            result[st] = [{
                "id": a.id,
                "account": a.account,
                "credit_balance": a.credit_balance,
                "credit_threshold": a.credit_threshold,
                "is_low": a.credit_balance < a.credit_threshold,
                "priority": a.priority,
            } for a in accounts]
        return jsonify(result)

    @app.route("/api/credits/<int:cred_id>/recharge", methods=["POST"])
    def recharge_credit(cred_id):
        cred = ServiceCredential.query.get_or_404(cred_id)
        data = request.get_json(force=True)
        amount = data.get("amount", 0)
        if amount <= 0:
            return jsonify({"error": "充值金额须大于 0"}), 400
        cred.credit_balance += amount
        log = CreditLog(
            credential_id=cred.id, event_type="recharge",
            amount=amount, balance_after=cred.credit_balance,
            detail=f"手动充值 {amount} 积分",
        )
        db.session.add(log)
        db.session.commit()
        return jsonify(cred.to_dict())

    @app.route("/api/credits/logs", methods=["GET"])
    def credit_logs():
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        cred_id = request.args.get("credential_id", type=int)
        q = CreditLog.query.order_by(CreditLog.created_at.desc())
        if cred_id:
            q = q.filter_by(credential_id=cred_id)
        logs = q.limit(per_page).offset((page - 1) * per_page).all()
        return jsonify([l.to_dict() for l in logs])

    # ---- AI Provider Failover Info ----
    @app.route("/api/ai/providers", methods=["GET"])
    def ai_providers():
        """Return the failover chain with account availability."""
        chain = []
        for st in ServiceCredential.AI_FAILOVER_CHAIN:
            accounts = ServiceCredential.query.filter_by(service_type=st, enabled=True).order_by(
                ServiceCredential.priority
            ).all()
            available = [a for a in accounts if a.credit_balance >= a.credit_threshold]
            chain.append({
                "service_type": st,
                "total_accounts": len(accounts),
                "available_accounts": len(available),
                "accounts": [a.to_dict() for a in accounts],
            })
        return jsonify(chain)

    # ---- Progress Context ----
    @app.route("/api/runs/<int:rid>/progress", methods=["GET"])
    def get_progress(rid):
        TestRun.query.get_or_404(rid)
        contexts = ProgressContext.query.filter_by(run_id=rid).order_by(
            ProgressContext.created_at.desc()
        ).all()
        return jsonify([c.to_dict() for c in contexts])

    # ---- Failure Codes ----
    @app.route("/api/failure-codes", methods=["GET"])
    def list_failure_codes():
        return jsonify([{"code": fc.value, "name": fc.name} for fc in FailureCode])

    # ---- Health check ----
    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "service": "AI自动化实验平台"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=3000, debug=False)
