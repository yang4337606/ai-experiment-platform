"""
Pre-built install/verify script templates for common project types.
Organized by category so users can one-click create a project.
"""

SCRIPT_TEMPLATES = [
    # =========================================================================
    # Category: Java / SpringBoot
    # =========================================================================
    {
        "id": "java-springboot",
        "category": "Java",
        "name": "SpringBoot 应用",
        "description": "Spring Boot 微服务, Maven 构建, systemd 管理",
        "icon": "&#9749;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === SpringBoot 应用安装 ==="

# 安装 JDK
echo "[安装] 安装 OpenJDK 17..."
apt-get update -qq
apt-get install -y openjdk-17-jdk maven curl > /dev/null 2>&1
java -version

# 构建项目
echo "[安装] Maven 构建..."
cd /opt/workspace/app
mvn clean package -DskipTests -q

# 部署
JAR_FILE=$(find target -name "*.jar" -not -name "*-sources.jar" | head -1)
echo "[安装] 部署 JAR: $JAR_FILE"
mkdir -p /opt/app
cp "$JAR_FILE" /opt/app/app.jar

# 创建 systemd 服务
cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=SpringBoot Application
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/java -Xms512m -Xmx1024m -jar /opt/app/app.jar --server.port=8080
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable app
systemctl start app
echo "[安装] SpringBoot 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
echo "[验证] === SpringBoot 应用验证 ==="

echo "[验证] 检查服务状态..."
systemctl is-active app

echo "[验证] 检查端口 8080..."
sleep 5
ss -tlnp | grep ':8080' || netstat -tlnp | grep ':8080'

echo "[验证] 健康检查..."
curl -sf --max-time 15 http://127.0.0.1:8080/actuator/health || curl -sf --max-time 15 http://127.0.0.1:8080/health

echo "[验证] 所有检查通过"
""",
        "config": {"port": 8080, "jvm_opts": "-Xms512m -Xmx1024m"}
    },
    {
        "id": "java-gradle",
        "category": "Java",
        "name": "Java Gradle 项目",
        "description": "Gradle 构建的 Java 应用, 支持 Spring / Quarkus",
        "icon": "&#9749;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Java Gradle 项目安装 ==="

apt-get update -qq
apt-get install -y openjdk-17-jdk curl unzip > /dev/null 2>&1

# 安装 Gradle (如果没有 wrapper)
cd /opt/workspace/app
if [ -f "./gradlew" ]; then
    chmod +x ./gradlew
    echo "[安装] 使用 Gradle Wrapper..."
    ./gradlew build -x test --no-daemon -q
else
    echo "[安装] 安装 Gradle..."
    wget -q https://services.gradle.org/distributions/gradle-8.5-bin.zip -O /tmp/gradle.zip
    unzip -qo /tmp/gradle.zip -d /opt
    export PATH=/opt/gradle-8.5/bin:$PATH
    gradle build -x test --no-daemon -q
fi

JAR_FILE=$(find build/libs -name "*.jar" -not -name "*-plain.jar" | head -1)
mkdir -p /opt/app
cp "$JAR_FILE" /opt/app/app.jar

cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=Java Gradle Application
After=network.target
[Service]
Type=simple
ExecStart=/usr/bin/java -jar /opt/app/app.jar --server.port=8080
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable app && systemctl start app
echo "[安装] Java Gradle 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active app
sleep 5
ss -tlnp | grep ':8080'
curl -sf --max-time 15 http://127.0.0.1:8080/health || curl -sf --max-time 15 http://127.0.0.1:8080/actuator/health
echo "[验证] 所有检查通过"
""",
        "config": {"port": 8080}
    },

    # =========================================================================
    # Category: Python
    # =========================================================================
    {
        "id": "python-flask",
        "category": "Python",
        "name": "Flask Web 应用",
        "description": "Python Flask + Gunicorn, virtualenv 隔离",
        "icon": "&#128013;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Flask 应用安装 ==="

apt-get update -qq
apt-get install -y python3 python3-pip python3-venv curl > /dev/null 2>&1

cd /opt/workspace/app
python3 -m venv /opt/app/venv
source /opt/app/venv/bin/activate

echo "[安装] 安装依赖..."
pip install --quiet -r requirements.txt
pip install --quiet gunicorn

cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=Flask Application (Gunicorn)
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/workspace/app
ExecStart=/opt/app/venv/bin/gunicorn -w 4 -b 0.0.0.0:5000 app:app
Restart=on-failure
Environment=FLASK_ENV=production
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable app && systemctl start app
echo "[安装] Flask 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active app
sleep 3
ss -tlnp | grep ':5000'
curl -sf --max-time 10 http://127.0.0.1:5000/health || curl -sf --max-time 10 http://127.0.0.1:5000/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 5000, "workers": 4}
    },
    {
        "id": "python-django",
        "category": "Python",
        "name": "Django Web 应用",
        "description": "Django + Gunicorn + Nginx 反代",
        "icon": "&#128013;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Django 应用安装 ==="

apt-get update -qq
apt-get install -y python3 python3-pip python3-venv nginx curl libpq-dev > /dev/null 2>&1

cd /opt/workspace/app
python3 -m venv /opt/app/venv
source /opt/app/venv/bin/activate

pip install --quiet -r requirements.txt
pip install --quiet gunicorn

echo "[安装] 数据库迁移..."
python manage.py migrate --noinput
python manage.py collectstatic --noinput 2>/dev/null || true

cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=Django Application (Gunicorn)
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/workspace/app
ExecStart=/opt/app/venv/bin/gunicorn -w 4 -b 127.0.0.1:8000 config.wsgi:application
Restart=on-failure
Environment=DJANGO_SETTINGS_MODULE=config.settings
[Install]
WantedBy=multi-user.target
EOF

# Nginx 反代
cat > /etc/nginx/sites-available/app << 'NGINX'
server {
    listen 80;
    server_name _;
    location /static/ { alias /opt/workspace/app/static/; }
    location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
}
NGINX
ln -sf /etc/nginx/sites-available/app /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable app && systemctl start app
systemctl restart nginx
echo "[安装] Django 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active app
systemctl is-active nginx
sleep 3
ss -tlnp | grep ':80'
curl -sf --max-time 10 http://127.0.0.1/admin/ || curl -sf --max-time 10 http://127.0.0.1/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 80, "workers": 4}
    },
    {
        "id": "python-fastapi",
        "category": "Python",
        "name": "FastAPI 应用",
        "description": "FastAPI + Uvicorn 高性能异步 API",
        "icon": "&#128013;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === FastAPI 应用安装 ==="

apt-get update -qq
apt-get install -y python3 python3-pip python3-venv curl > /dev/null 2>&1

cd /opt/workspace/app
python3 -m venv /opt/app/venv
source /opt/app/venv/bin/activate

pip install --quiet -r requirements.txt
pip install --quiet uvicorn[standard]

cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=FastAPI Application (Uvicorn)
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/workspace/app
ExecStart=/opt/app/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable app && systemctl start app
echo "[安装] FastAPI 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active app
sleep 3
ss -tlnp | grep ':8000'
curl -sf --max-time 10 http://127.0.0.1:8000/docs || curl -sf --max-time 10 http://127.0.0.1:8000/health
echo "[验证] 所有检查通过"
""",
        "config": {"port": 8000, "workers": 4}
    },

    # =========================================================================
    # Category: Node.js
    # =========================================================================
    {
        "id": "nodejs-express",
        "category": "Node.js",
        "name": "Express.js 应用",
        "description": "Node.js Express + PM2 进程管理",
        "icon": "&#128154;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Express.js 应用安装 ==="

# 安装 Node.js 20 LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
apt-get install -y nodejs > /dev/null 2>&1
npm install -g pm2

cd /opt/workspace/app
echo "[安装] 安装依赖..."
npm ci --production

# PM2 启动
pm2 start npm --name "app" -- start || pm2 start index.js --name "app" -i max
pm2 save
pm2 startup systemd -u root --hp /root 2>/dev/null || true
echo "[安装] Express.js 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
pm2 status app | grep -E "online|running"
sleep 3
ss -tlnp | grep ':3000' || ss -tlnp | grep ':8080'
curl -sf --max-time 10 http://127.0.0.1:3000/health || curl -sf --max-time 10 http://127.0.0.1:3000/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 3000, "node_env": "production"}
    },
    {
        "id": "nodejs-nextjs",
        "category": "Node.js",
        "name": "Next.js 应用",
        "description": "Next.js SSR/SSG 应用, PM2 管理",
        "icon": "&#128154;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Next.js 应用安装 ==="

curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
apt-get install -y nodejs > /dev/null 2>&1
npm install -g pm2

cd /opt/workspace/app
npm ci
echo "[安装] 构建 Next.js..."
npm run build

pm2 start npm --name "nextjs" -- start
pm2 save
pm2 startup systemd -u root --hp /root 2>/dev/null || true
echo "[安装] Next.js 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
pm2 status nextjs | grep -E "online|running"
sleep 5
ss -tlnp | grep ':3000'
curl -sf --max-time 15 http://127.0.0.1:3000/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 3000, "node_env": "production"}
    },
    {
        "id": "nodejs-nestjs",
        "category": "Node.js",
        "name": "NestJS 应用",
        "description": "NestJS 企业级框架, TypeScript 构建",
        "icon": "&#128154;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === NestJS 应用安装 ==="

curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
apt-get install -y nodejs > /dev/null 2>&1
npm install -g pm2

cd /opt/workspace/app
npm ci
echo "[安装] 构建 NestJS..."
npm run build

pm2 start dist/main.js --name "nestjs" -i max
pm2 save
pm2 startup systemd -u root --hp /root 2>/dev/null || true
echo "[安装] NestJS 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
pm2 status nestjs | grep -E "online|running"
sleep 3
ss -tlnp | grep ':3000'
curl -sf --max-time 10 http://127.0.0.1:3000/health || curl -sf --max-time 10 http://127.0.0.1:3000/api
echo "[验证] 所有检查通过"
""",
        "config": {"port": 3000}
    },

    # =========================================================================
    # Category: Go
    # =========================================================================
    {
        "id": "go-service",
        "category": "Go",
        "name": "Go 微服务",
        "description": "Go 编译部署, systemd 管理",
        "icon": "&#128039;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Go 微服务安装 ==="

# 安装 Go 1.22
apt-get update -qq
apt-get install -y curl > /dev/null 2>&1
if ! command -v go &>/dev/null; then
    wget -q https://go.dev/dl/go1.22.0.linux-amd64.tar.gz -O /tmp/go.tar.gz
    rm -rf /usr/local/go && tar -C /usr/local -xzf /tmp/go.tar.gz
    export PATH=$PATH:/usr/local/go/bin
    echo 'export PATH=$PATH:/usr/local/go/bin' >> /etc/profile
fi
go version

cd /opt/workspace/app
echo "[安装] 编译 Go 程序..."
CGO_ENABLED=0 go build -o /opt/app/server .

cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=Go Microservice
After=network.target
[Service]
Type=simple
ExecStart=/opt/app/server
Restart=on-failure
Environment=PORT=8080
Environment=GIN_MODE=release
[Install]
WantedBy=multi-user.target
EOF

mkdir -p /opt/app
systemctl daemon-reload && systemctl enable app && systemctl start app
echo "[安装] Go 微服务启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active app
sleep 2
ss -tlnp | grep ':8080'
curl -sf --max-time 10 http://127.0.0.1:8080/health || curl -sf --max-time 10 http://127.0.0.1:8080/ping
echo "[验证] 所有检查通过"
""",
        "config": {"port": 8080}
    },

    # =========================================================================
    # Category: Docker
    # =========================================================================
    {
        "id": "docker-compose",
        "category": "Docker",
        "name": "Docker Compose 项目",
        "description": "docker-compose 一键编排多容器应用",
        "icon": "&#128051;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Docker Compose 项目安装 ==="

# 安装 Docker
if ! command -v docker &>/dev/null; then
    echo "[安装] 安装 Docker..."
    curl -fsSL https://get.docker.com | sh > /dev/null 2>&1
    systemctl enable docker && systemctl start docker
fi

# 安装 docker-compose
if ! command -v docker-compose &>/dev/null; then
    apt-get install -y docker-compose-plugin > /dev/null 2>&1 || \
    curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" -o /usr/local/bin/docker-compose && chmod +x /usr/local/bin/docker-compose
fi

cd /opt/workspace/app
echo "[安装] 启动容器..."
docker compose up -d || docker-compose up -d
echo "[安装] Docker Compose 启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
echo "[验证] 检查容器状态..."
cd /opt/workspace/app
docker compose ps || docker-compose ps
# 检查所有容器是否 running
RUNNING=$(docker compose ps --format json 2>/dev/null | grep -c '"running"' || docker-compose ps | grep -c "Up")
if [ "$RUNNING" -gt 0 ]; then
    echo "[验证] $RUNNING 个容器运行中"
else
    echo "[验证] 无运行中的容器" && exit 1
fi
echo "[验证] 所有检查通过"
""",
        "config": {"port": 80}
    },
    {
        "id": "docker-single",
        "category": "Docker",
        "name": "单容器 Docker 应用",
        "description": "Dockerfile 构建 + 单容器运行",
        "icon": "&#128051;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Docker 单容器应用安装 ==="

if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh > /dev/null 2>&1
    systemctl enable docker && systemctl start docker
fi

cd /opt/workspace/app
echo "[安装] 构建镜像..."
docker build -t myapp:latest .
echo "[安装] 启动容器..."
docker rm -f myapp 2>/dev/null || true
docker run -d --name myapp --restart=unless-stopped -p 8080:8080 myapp:latest
echo "[安装] 容器启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
docker ps | grep myapp | grep -q "Up"
sleep 3
ss -tlnp | grep ':8080'
curl -sf --max-time 10 http://127.0.0.1:8080/health || curl -sf --max-time 10 http://127.0.0.1:8080/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 8080}
    },

    # =========================================================================
    # Category: Database
    # =========================================================================
    {
        "id": "db-mysql",
        "category": "数据库",
        "name": "MySQL 8.0",
        "description": "MySQL 8.0 数据库服务器安装配置",
        "icon": "&#128451;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === MySQL 8.0 安装 ==="

export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
echo "mysql-server mysql-server/root_password password root123" | debconf-set-selections
echo "mysql-server mysql-server/root_password_again password root123" | debconf-set-selections
apt-get install -y mysql-server mysql-client > /dev/null 2>&1

# 配置
cat > /etc/mysql/mysql.conf.d/custom.cnf << 'EOF'
[mysqld]
bind-address = 0.0.0.0
max_connections = 500
innodb_buffer_pool_size = 1G
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
EOF

systemctl enable mysql && systemctl restart mysql

# 安全设置
mysql -uroot -proot123 -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'root123';" 2>/dev/null || true
mysql -uroot -proot123 -e "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'root123'; GRANT ALL PRIVILEGES ON *.* TO 'root'@'%'; FLUSH PRIVILEGES;" 2>/dev/null || true
echo "[安装] MySQL 8.0 安装完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active mysql
ss -tlnp | grep ':3306'
mysql -uroot -proot123 -e "SELECT VERSION();" 2>/dev/null
echo "[验证] 所有检查通过"
""",
        "config": {"port": 3306, "root_password": "root123"}
    },
    {
        "id": "db-postgresql",
        "category": "数据库",
        "name": "PostgreSQL 16",
        "description": "PostgreSQL 16 数据库服务器",
        "icon": "&#128451;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === PostgreSQL 16 安装 ==="

apt-get update -qq
apt-get install -y wget gnupg2 > /dev/null 2>&1
sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget -qO- https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add - 2>/dev/null
apt-get update -qq
apt-get install -y postgresql-16 > /dev/null 2>&1

# 配置远程访问
echo "host all all 0.0.0.0/0 md5" >> /etc/postgresql/16/main/pg_hba.conf
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" /etc/postgresql/16/main/postgresql.conf

systemctl enable postgresql && systemctl restart postgresql

sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres123';" 2>/dev/null
echo "[安装] PostgreSQL 16 安装完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active postgresql
ss -tlnp | grep ':5432'
sudo -u postgres psql -c "SELECT version();"
echo "[验证] 所有检查通过"
""",
        "config": {"port": 5432}
    },
    {
        "id": "db-redis",
        "category": "数据库",
        "name": "Redis 7",
        "description": "Redis 7 内存缓存与消息队列",
        "icon": "&#128451;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Redis 7 安装 ==="

apt-get update -qq
apt-get install -y redis-server > /dev/null 2>&1

# 配置
sed -i 's/^bind .*/bind 0.0.0.0/' /etc/redis/redis.conf
sed -i 's/^# requirepass .*/requirepass redis123/' /etc/redis/redis.conf
sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf
sed -i 's/^# maxmemory .*/maxmemory 512mb/' /etc/redis/redis.conf
sed -i 's/^# maxmemory-policy .*/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf

systemctl enable redis-server && systemctl restart redis-server
echo "[安装] Redis 安装完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active redis-server || systemctl is-active redis
ss -tlnp | grep ':6379'
redis-cli -a redis123 ping | grep -q PONG
echo "[验证] 所有检查通过"
""",
        "config": {"port": 6379}
    },
    {
        "id": "db-mongodb",
        "category": "数据库",
        "name": "MongoDB 7",
        "description": "MongoDB 7 文档数据库",
        "icon": "&#128451;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === MongoDB 7 安装 ==="

apt-get update -qq
apt-get install -y gnupg curl > /dev/null 2>&1
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg
echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu $(lsb_release -cs)/mongodb-org/7.0 multiverse" > /etc/apt/sources.list.d/mongodb-org-7.0.list
apt-get update -qq
apt-get install -y mongodb-org > /dev/null 2>&1

sed -i 's/bindIp: 127.0.0.1/bindIp: 0.0.0.0/' /etc/mongod.conf

systemctl enable mongod && systemctl start mongod
echo "[安装] MongoDB 7 安装完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active mongod
ss -tlnp | grep ':27017'
mongosh --eval "db.runCommand({ ping: 1 })" 2>/dev/null || mongo --eval "db.runCommand({ ping: 1 })"
echo "[验证] 所有检查通过"
""",
        "config": {"port": 27017}
    },

    # =========================================================================
    # Category: Web Server
    # =========================================================================
    {
        "id": "web-nginx",
        "category": "Web 服务器",
        "name": "Nginx",
        "description": "Nginx Web 服务器 + 反向代理配置",
        "icon": "&#127760;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Nginx 安装 ==="

apt-get update -qq
apt-get install -y nginx curl > /dev/null 2>&1

# 基础配置
cat > /etc/nginx/sites-available/default << 'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    root /var/www/html;
    index index.html;
    server_name _;
    location / { try_files $uri $uri/ =404; }
    location /health { return 200 '{"status":"ok"}'; add_header Content-Type application/json; }
}
EOF

# 部署静态文件
if [ -d "/opt/workspace/app/dist" ]; then
    cp -r /opt/workspace/app/dist/* /var/www/html/
elif [ -d "/opt/workspace/app/build" ]; then
    cp -r /opt/workspace/app/build/* /var/www/html/
elif [ -d "/opt/workspace/app/public" ]; then
    cp -r /opt/workspace/app/public/* /var/www/html/
fi

nginx -t
systemctl enable nginx && systemctl restart nginx
echo "[安装] Nginx 安装完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active nginx
ss -tlnp | grep ':80'
curl -sf --max-time 10 http://127.0.0.1/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 80}
    },
    {
        "id": "web-apache",
        "category": "Web 服务器",
        "name": "Apache httpd",
        "description": "Apache HTTP Server 安装配置",
        "icon": "&#127760;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Apache 安装 ==="

apt-get update -qq
apt-get install -y apache2 curl > /dev/null 2>&1

# 部署静态文件
if [ -d "/opt/workspace/app/dist" ]; then
    cp -r /opt/workspace/app/dist/* /var/www/html/
fi

a2enmod rewrite proxy proxy_http 2>/dev/null || true
systemctl enable apache2 && systemctl restart apache2
echo "[安装] Apache 安装完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active apache2
ss -tlnp | grep ':80'
curl -sf --max-time 10 http://127.0.0.1/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 80}
    },

    # =========================================================================
    # Category: Message Queue
    # =========================================================================
    {
        "id": "mq-rabbitmq",
        "category": "消息队列",
        "name": "RabbitMQ",
        "description": "RabbitMQ 消息队列 + 管理控制台",
        "icon": "&#128236;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === RabbitMQ 安装 ==="

apt-get update -qq
apt-get install -y curl gnupg > /dev/null 2>&1

# 安装 Erlang & RabbitMQ
curl -fsSL https://packagecloud.io/rabbitmq/rabbitmq-server/gpgkey | apt-key add - 2>/dev/null
apt-get install -y erlang-nox rabbitmq-server > /dev/null 2>&1

systemctl enable rabbitmq-server && systemctl start rabbitmq-server

# 启用管理插件
rabbitmq-plugins enable rabbitmq_management
rabbitmqctl add_user admin admin123 2>/dev/null || true
rabbitmqctl set_user_tags admin administrator
rabbitmqctl set_permissions -p / admin ".*" ".*" ".*"
echo "[安装] RabbitMQ 安装完成 (管理控制台: http://ip:15672)"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active rabbitmq-server
ss -tlnp | grep ':5672'
ss -tlnp | grep ':15672'
rabbitmqctl status | grep -q "running"
echo "[验证] 所有检查通过"
""",
        "config": {"port": 5672, "management_port": 15672}
    },

    # =========================================================================
    # Category: DevOps
    # =========================================================================
    {
        "id": "devops-jenkins",
        "category": "DevOps",
        "name": "Jenkins CI/CD",
        "description": "Jenkins 持续集成服务器",
        "icon": "&#9881;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Jenkins 安装 ==="

apt-get update -qq
apt-get install -y openjdk-17-jdk curl > /dev/null 2>&1

curl -fsSL https://pkg.jenkins.io/debian-stable/jenkins.io-2023.key | tee /usr/share/keyrings/jenkins-keyring.asc > /dev/null
echo "deb [signed-by=/usr/share/keyrings/jenkins-keyring.asc] https://pkg.jenkins.io/debian-stable binary/" > /etc/apt/sources.list.d/jenkins.list
apt-get update -qq
apt-get install -y jenkins > /dev/null 2>&1

systemctl enable jenkins && systemctl start jenkins
echo "[安装] Jenkins 安装完成"
echo "[安装] 初始密码: $(cat /var/lib/jenkins/secrets/initialAdminPassword 2>/dev/null || echo '等待生成中...')"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active jenkins
sleep 10
ss -tlnp | grep ':8080'
curl -sf --max-time 30 http://127.0.0.1:8080/login || curl -sf --max-time 30 http://127.0.0.1:8080/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 8080}
    },
    {
        "id": "devops-gitlab-runner",
        "category": "DevOps",
        "name": "GitLab Runner",
        "description": "GitLab CI/CD Runner 注册与配置",
        "icon": "&#9881;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === GitLab Runner 安装 ==="

curl -fsSL "https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh" | bash > /dev/null 2>&1
apt-get install -y gitlab-runner > /dev/null 2>&1

# 安装 Docker (Runner executor)
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh > /dev/null 2>&1
    usermod -aG docker gitlab-runner
fi

systemctl enable gitlab-runner && systemctl start gitlab-runner
echo "[安装] GitLab Runner 安装完成"
echo "[安装] 请执行 gitlab-runner register 进行注册"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active gitlab-runner
gitlab-runner --version
gitlab-runner list 2>&1
echo "[验证] 所有检查通过"
""",
        "config": {}
    },

    # =========================================================================
    # Category: Monitoring
    # =========================================================================
    {
        "id": "monitor-prometheus-grafana",
        "category": "监控",
        "name": "Prometheus + Grafana",
        "description": "Prometheus 监控 + Grafana 可视化仪表盘",
        "icon": "&#128200;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Prometheus + Grafana 安装 ==="

apt-get update -qq
apt-get install -y wget curl adduser > /dev/null 2>&1

# Prometheus
PROM_VER="2.48.1"
useradd --no-create-home --shell /bin/false prometheus 2>/dev/null || true
mkdir -p /etc/prometheus /var/lib/prometheus
wget -q "https://github.com/prometheus/prometheus/releases/download/v${PROM_VER}/prometheus-${PROM_VER}.linux-amd64.tar.gz" -O /tmp/prom.tar.gz
tar xzf /tmp/prom.tar.gz -C /tmp
cp /tmp/prometheus-*/prometheus /tmp/prometheus-*/promtool /usr/local/bin/
cp -r /tmp/prometheus-*/consoles /tmp/prometheus-*/console_libraries /etc/prometheus/

cat > /etc/prometheus/prometheus.yml << 'EOF'
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
EOF

cat > /etc/systemd/system/prometheus.service << 'EOF'
[Unit]
Description=Prometheus
After=network.target
[Service]
Type=simple
User=prometheus
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus
[Install]
WantedBy=multi-user.target
EOF

chown -R prometheus:prometheus /etc/prometheus /var/lib/prometheus

# Grafana
apt-get install -y apt-transport-https software-properties-common > /dev/null 2>&1
wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor > /usr/share/keyrings/grafana.gpg
echo "deb [signed-by=/usr/share/keyrings/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
apt-get update -qq
apt-get install -y grafana > /dev/null 2>&1

systemctl daemon-reload
systemctl enable prometheus grafana-server
systemctl start prometheus grafana-server
echo "[安装] Prometheus(:9090) + Grafana(:3000) 安装完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active prometheus
systemctl is-active grafana-server
ss -tlnp | grep ':9090'
ss -tlnp | grep ':3000'
curl -sf --max-time 10 http://127.0.0.1:9090/-/healthy
curl -sf --max-time 10 http://127.0.0.1:3000/api/health
echo "[验证] 所有检查通过"
""",
        "config": {"port": 9090, "grafana_port": 3000}
    },

    # =========================================================================
    # Category: PHP
    # =========================================================================
    {
        "id": "php-laravel",
        "category": "PHP",
        "name": "Laravel 应用",
        "description": "PHP Laravel + Nginx + Composer",
        "icon": "&#128187;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Laravel 应用安装 ==="

apt-get update -qq
apt-get install -y php8.1-fpm php8.1-mbstring php8.1-xml php8.1-mysql php8.1-curl nginx curl unzip > /dev/null 2>&1

# Composer
curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer > /dev/null 2>&1

cd /opt/workspace/app
composer install --no-dev --optimize-autoloader --quiet
cp .env.example .env 2>/dev/null || true
php artisan key:generate --force 2>/dev/null || true
php artisan migrate --force 2>/dev/null || true

chown -R www-data:www-data storage bootstrap/cache

cat > /etc/nginx/sites-available/default << 'NGINX'
server {
    listen 80;
    root /opt/workspace/app/public;
    index index.php;
    location / { try_files $uri $uri/ /index.php?$query_string; }
    location ~ \\.php$ {
        fastcgi_pass unix:/run/php/php8.1-fpm.sock;
        fastcgi_param SCRIPT_FILENAME $realpath_root$fastcgi_script_name;
        include fastcgi_params;
    }
    location /health { return 200 '{"status":"ok"}'; add_header Content-Type application/json; }
}
NGINX

systemctl restart php8.1-fpm nginx
echo "[安装] Laravel 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active php8.1-fpm
systemctl is-active nginx
ss -tlnp | grep ':80'
curl -sf --max-time 10 http://127.0.0.1/
echo "[验证] 所有检查通过"
""",
        "config": {"port": 80}
    },

    # =========================================================================
    # Category: C++ / Rust
    # =========================================================================
    {
        "id": "cpp-cmake",
        "category": "C/C++",
        "name": "C++ CMake 项目",
        "description": "CMake 编译 + systemd 部署",
        "icon": "&#9889;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === C++ CMake 项目安装 ==="

apt-get update -qq
apt-get install -y build-essential cmake git > /dev/null 2>&1

cd /opt/workspace/app
mkdir -p build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc)

BINARY=$(find . -maxdepth 1 -type f -executable | head -1)
mkdir -p /opt/app
cp "$BINARY" /opt/app/server

cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=C++ Application
After=network.target
[Service]
Type=simple
ExecStart=/opt/app/server
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable app && systemctl start app
echo "[安装] C++ 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active app
sleep 2
ss -tlnp | grep ':8080' || ss -tlnp | grep ':80'
echo "[验证] 所有检查通过"
""",
        "config": {"port": 8080}
    },
    {
        "id": "rust-cargo",
        "category": "Rust",
        "name": "Rust Cargo 项目",
        "description": "Rust cargo build + systemd 部署",
        "icon": "&#129408;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === Rust Cargo 项目安装 ==="

apt-get update -qq
apt-get install -y curl build-essential > /dev/null 2>&1

if ! command -v cargo &>/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y > /dev/null 2>&1
    source $HOME/.cargo/env
fi

cd /opt/workspace/app
echo "[安装] 编译 Rust 项目..."
cargo build --release

BINARY=$(find target/release -maxdepth 1 -type f -executable ! -name "*.d" | head -1)
mkdir -p /opt/app
cp "$BINARY" /opt/app/server

cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=Rust Application
After=network.target
[Service]
Type=simple
ExecStart=/opt/app/server
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable app && systemctl start app
echo "[安装] Rust 应用启动完成"
""",
        "verify_script": """#!/bin/bash
set -e
systemctl is-active app
sleep 2
ss -tlnp | grep ':8080' || ss -tlnp | grep ':80'
echo "[验证] 所有检查通过"
""",
        "config": {"port": 8080}
    },

    # =========================================================================
    # Category: 通用脚本
    # =========================================================================
    {
        "id": "generic-shell",
        "category": "通用",
        "name": "Shell 脚本部署",
        "description": "自定义 Shell 脚本, 适用于任何项目",
        "icon": "&#128196;",
        "install_script": """#!/bin/bash
set -e
echo "[安装] === 自定义安装 ==="
echo "[安装] 请替换此脚本内容为您的实际安装步骤"

# 示例: 安装系统依赖
# apt-get update && apt-get install -y <packages>

# 示例: 拉取代码 & 安装项目依赖
# cd /opt/workspace/app && make install

# 示例: 配置服务
# systemctl enable myservice && systemctl start myservice

echo "[安装] 安装完成"
""",
        "verify_script": """#!/bin/bash
set -e
echo "[验证] === 自定义验证 ==="
echo "[验证] 请替换此脚本内容为您的实际验证步骤"

# 示例: 检查服务状态
# systemctl is-active myservice

# 示例: 检查端口
# ss -tlnp | grep ':8080'

# 示例: 健康检查
# curl -sf http://127.0.0.1:8080/health

echo "[验证] 验证完成"
""",
        "config": {}
    },
]


def get_templates():
    """Return all templates."""
    return SCRIPT_TEMPLATES


def get_categories():
    """Return templates grouped by category."""
    categories = {}
    for t in SCRIPT_TEMPLATES:
        cat = t["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(t)
    return categories


def get_template_by_id(template_id):
    """Find a template by its id."""
    for t in SCRIPT_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None
