#!/usr/bin/env bash
# pre-delete-check.sh — 删除/移动仓库目录前的清点（2026-11-05 加固）
#
# 用途：在 `rm -rf <仓库>`、`mv <仓库>`、`git clone` 覆盖旧目录之前，确认
# 「里面的东西是否都有别处的副本」。灵感来自同名 skill（laurigates/git-repo-delete-check），
# 本实现补上了它没有的两类：**忽略区里的不可再生资产**、**本地裸镜像与快照的覆盖度**。
#
# 用法：
#   ./pre-delete-check.sh [仓库路径]      # 只体检，不动任何东西
#   ./pre-delete-check.sh --force [路径]  # 体检后仍然返回 0（供脚本调用）
#
# 退出码：0=可以删；1=有风险，需人工确认
set -uo pipefail

FORCE=0
[ "${1:-}" = "--force" ] && { FORCE=1; shift; }
TARGET="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
[ -d "$TARGET/.git" ] || { echo "不是 git 工作区：$TARGET"; exit 2; }
cd "$TARGET" || exit 2

say() { printf '\033[36m%s\033[0m\n' "$*"; }
ok()  { printf '\033[32m✓ %s\033[0m\n' "$*"; }
bad() { printf '\033[31m✗ %s\033[0m\n' "$*"; }
warn(){ printf '\033[33m! %s\033[0m\n' "$*"; }
RISK=0

say "① 未推送的提交"
if git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
  N="$(git rev-list --count '@{u}..HEAD')"
  if [ "$N" -gt 0 ]; then
    bad "$N 个提交未推送到 $(git rev-parse --abbrev-ref '@{u}')"
    git log --oneline '@{u}..HEAD' | head -5 | sed 's/^/    /'; RISK=1
  else ok "已全部推送"; fi
else
  warn "当前分支无 upstream，无法判断是否推送过"
  N="$(git rev-list --count --all)"
  echo "    本地共 $N 个提交（无法确认远端是否有副本）"; RISK=1
fi

say "② 未提交的改动"
DIRTY="$(git status --porcelain | wc -l)"
if [ "$DIRTY" -gt 0 ]; then
  bad "$DIRTY 处未提交改动（删除即丢失）"
  git status --short | head -8 | sed 's/^/    /'; RISK=1
else ok "工作区洁净"; fi

say "③ stash（最容易忘的一类）"
ST="$(git stash list | wc -l)"
if [ "$ST" -gt 0 ]; then
  bad "$ST 条 stash 不在任何分支上"
  git stash list | head -5 | sed 's/^/    /'; RISK=1
else ok "无 stash"; fi

say "④ 未跟踪文件"
UNTRACKED="$(git ls-files --others --exclude-standard | wc -l)"
if [ "$UNTRACKED" -gt 0 ]; then
  warn "$UNTRACKED 个未跟踪文件（不在 git 里，删除即丢失）"
  git ls-files --others --exclude-standard | head -8 | sed 's/^/    /'; RISK=1
else ok "无未跟踪文件"; fi

say "⑤ 忽略区资产（git 不管，删除即永失）"
IGNORED_N="$(git ls-files --others --ignored --exclude-standard --directory 2>/dev/null | wc -l)"
if [ "$IGNORED_N" -gt 0 ]; then
  echo "  忽略区有 $IGNORED_N 个条目，按体量："
  git ls-files --others --ignored --exclude-standard --directory 2>/dev/null \
    | while IFS= read -r p; do [ -e "$p" ] && du -sh "$p" 2>/dev/null; done \
    | sort -rh | head -8 | sed 's/^/    /'
  warn "其中若含不可再生资产（原始件／台账／报告），需确认已在快照与异地副本中"; RISK=1
else ok "无忽略区文件"; fi

say "⑥ 本地裸镜像覆盖"
M="${HARDEN_MIRROR:-$HOME/backups/$(basename "$TARGET").git}"
[ -d "$M" ] || M="$HOME/Backups/rime/mirror.git"
if [ -d "$M" ]; then
  HEAD_SHA="$(git rev-parse HEAD 2>/dev/null)"
  if git -C "$M" cat-file -e "$HEAD_SHA^{commit}" 2>/dev/null; then
    ok "镜像已含 HEAD ${HEAD_SHA:0:9}（$M）"
  else bad "镜像不含 HEAD——删除前先 mirror-sync"; RISK=1; fi
else
  bad "无本地镜像——删库后无法从镜像恢复"; RISK=1
fi

say "⑦ 未合并的分支"
for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do
  [ "$b" = "$(git rev-parse --abbrev-ref HEAD)" ] && continue
  git merge-base --is-ancestor "$b" HEAD 2>/dev/null || { warn "分支 $b 有未合并提交"; RISK=1; }
done
[ "$RISK" = 0 ] && ok "无未合并分支"

echo
if [ "$RISK" = 0 ]; then
  ok "体检通过：可以删除（所有内容在远端或镜像中有副本）"
  exit 0
fi
if [ "$FORCE" = 1 ]; then
  warn "有风险项，但 --force 已放行"
  exit 0
fi
bad "有风险项，**不要直接删除**。先处置上面标 ✗ 的项，或明确 --force 承担后果。"
exit 1
