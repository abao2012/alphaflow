#!/usr/bin/env bash
# ============================================================================
# AlphaFlow Hermes 集成安装脚本
# 用法: bash setup.sh
# 幂等设计 - 可多次运行，不会覆盖已有配置
# ============================================================================

set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # 无颜色

# ---- 1. 获取脚本所在目录 ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo -e "${GREEN}[信息]${NC} 脚本目录: $SCRIPT_DIR"

# ---- 2. 创建 ~/.hermes/skills/mlops/alphaflow-monitor/ 目录结构 ----
HERMES_HOME="$HOME/.hermes"
SKILL_DIR="$HERMES_HOME/skills/mlops/alphaflow-monitor"

echo -e "${GREEN}[信息]${NC} 创建技能目录: $SKILL_DIR"
mkdir -p "$SKILL_DIR/scripts"
mkdir -p "$SKILL_DIR/templates"

# ---- 3. 复制技能文件 ----
# 复制 SKILL.md
if [ -f "$SCRIPT_DIR/skills/alphaflow-monitor/SKILL.md" ]; then
    cp -v "$SCRIPT_DIR/skills/alphaflow-monitor/SKILL.md" "$SKILL_DIR/"
else
    echo -e "${YELLOW}[警告]${NC} 未找到 SKILL.md，跳过"
fi

# 复制 scripts/ 目录
if [ -d "$SCRIPT_DIR/skills/alphaflow-monitor/scripts" ]; then
    cp -rv "$SCRIPT_DIR/skills/alphaflow-monitor/scripts/"* "$SKILL_DIR/scripts/"
else
    echo -e "${YELLOW}[警告]${NC} 未找到 scripts/ 目录，跳过"
fi

# 复制 templates/ 目录
if [ -d "$SCRIPT_DIR/skills/alphaflow-monitor/templates" ]; then
    cp -rv "$SCRIPT_DIR/skills/alphaflow-monitor/templates/"* "$SKILL_DIR/templates/"
else
    echo -e "${YELLOW}[警告]${NC} 未找到 templates/ 目录，跳过"
fi

# ---- 4. 创建快照存储目录 ----
SNAPSHOT_DIR="$HERMES_HOME/alphaflow_snapshots"
echo -e "${GREEN}[信息]${NC} 创建快照目录: $SNAPSHOT_DIR"
mkdir -p "$SNAPSHOT_DIR"

# ---- 5. 复制配置文件（仅当目标不存在时，保留用户编辑） ----
CONFIG_SRC="$SCRIPT_DIR/config/alphaflow_config.json"
CONFIG_DST="$HERMES_HOME/alphaflow_config.json"

if [ -f "$CONFIG_SRC" ]; then
    if [ ! -f "$CONFIG_DST" ]; then
        cp -v "$CONFIG_SRC" "$CONFIG_DST"
        echo -e "${GREEN}[信息]${NC} 已复制配置文件到 $CONFIG_DST"
    else
        echo -e "${YELLOW}[信息]${NC} 配置文件已存在，保留当前设置: $CONFIG_DST"
    fi
else
    echo -e "${YELLOW}[警告]${NC} 未找到配置文件 $CONFIG_SRC，跳过"
fi

# ---- 6. 设置脚本可执行权限 ----
echo -e "${GREEN}[信息]${NC} 设置脚本可执行权限..."
if [ -d "$SKILL_DIR/scripts" ]; then
    chmod +x "$SKILL_DIR/scripts/"*.py 2>/dev/null || true
    chmod +x "$SKILL_DIR/scripts/"*.sh 2>/dev/null || true
fi

# ---- 7. 验证 Python 脚本语法 ----
POLL_SCRIPT="$SKILL_DIR/scripts/poll_alphaflow.py"
if [ -f "$POLL_SCRIPT" ]; then
    echo -e "${GREEN}[信息]${NC} 验证 poll_alphaflow.py 语法..."
    if python3 -m py_compile "$POLL_SCRIPT"; then
        echo -e "${GREEN}[通过]${NC} poll_alphaflow.py 语法正确"
    else
        echo -e "${RED}[错误]${NC} poll_alphaflow.py 存在语法错误，请检查"
        exit 1
    fi
else
    echo -e "${YELLOW}[警告]${NC} 未找到 poll_alphaflow.py，跳过语法验证"
fi

# ---- 8. 安装完成，打印下一步操作 ----
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  AlphaFlow Hermes 集成安装完成！${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "已安装文件:"
echo "  技能目录:   $SKILL_DIR"
echo "  快照目录:   $SNAPSHOT_DIR"
echo "  配置文件:   $CONFIG_DST"
echo ""
echo -e "${YELLOW}下一步 - 创建定时轮询任务:${NC}"
echo ""
echo "  hermes cronjob create \\"
echo "    --schedule '*/5 * * * *' \\"
echo "    --prompt '使用 alphaflow-monitor 技能，轮询 AlphaFlow /api/v1/mainlines/scores 检查市场变化' \\"
echo "    --skills alphaflow-monitor"
echo ""
echo -e "${YELLOW}可选 - 编辑配置:${NC}"
echo "  编辑 $CONFIG_DST 以调整 AlphaFlow 服务器地址等参数"
echo ""
