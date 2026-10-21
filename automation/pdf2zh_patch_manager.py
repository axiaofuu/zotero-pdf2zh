"""Optional macOS/uv startup guard for reviewed BabelDOC font and layout fixes.

See README-patched-startup.md for scope, backups and update limitations.
"""

from __future__ import annotations

import argparse
import ast
import base64
import concurrent.futures
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime
from importlib.metadata import distribution, version
from urllib.request import urlopen

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

FONT_CASES = {
    "NewCM10-Regular": False,
    "NewCM10-Bold": False,
    "NewCM10-Italic": False,
    "ABCDEF+NewCM10-Regular": False,
    "NewComputerModern10-Regular": False,
    "NewCMMath-Book": True,
    "NewCMMath-Bold": True,
    "NewComputerModernMath-Regular": True,
}
REPLACEMENTS = (
    ('r"|.*NewCM.*"', 'r"|.*NewCMMath.*"'),
    ('r"|.*NewComputerModern.*"', 'r"|.*NewComputerModernMath.*"'),
)


def font_checks(source: str) -> None:
    tree = ast.parse(source)
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "is_formulas_font")
    scope = {"re": re, "functools": functools, "base64": base64}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "font_guard", "exec"), scope)
    for pattern in (None, r".*Math.*"):
        for name, expected in FONT_CASES.items():
            if scope["is_formulas_font"](name, pattern) != expected:
                raise ValueError(f"字体分类错误：{name}, pattern={pattern}")
        if scope["is_formulas_font"]("DejaVuSansMono", pattern) != (pattern is None):
            raise ValueError("自定义公式字体规则的行为发生变化。")


def repaired_source(source: str) -> tuple[str, bool]:
    try:
        font_checks(source)
        return source, False
    except Exception:
        pass
    candidate = source
    for old, new in REPLACEMENTS:
        if candidate.count(old) == 1 and candidate.count(new) == 0:
            candidate = candidate.replace(old, new, 1)
        elif candidate.count(old) == 0 and candidate.count(new) == 1:
            continue
        else:
            raise ValueError("新版字体代码不符合已知补丁结构，需要人工检查。")
    font_checks(candidate)
    return candidate, True


def ensure_patch(env_dir: Path, *, allow_repair: bool) -> None:
    helper = Path(distribution("BabelDOC").locate_file(
        "babeldoc/format/pdf/document_il/utils/formular_helper.py"
    )).resolve()
    if not helper.is_relative_to(env_dir.resolve()):
        raise RuntimeError(f"BabelDOC 不属于指定环境：{helper}")
    original = helper.read_text()
    candidate, changed = repaired_source(original)
    if changed:
        if not allow_repair:
            raise RuntimeError("检测到未应用的已知字体补丁，请正常启动以自动修复。")
        digest = hashlib.sha256(original.encode()).hexdigest()[:12]
        backup = helper.with_name(f"{helper.name}.before-newcm-{digest}")
        if not backup.exists():
            shutil.copy2(helper, backup)
        with tempfile.NamedTemporaryFile(mode="w", dir=helper.parent, delete=False) as f:
            temp_path = Path(f.name)
            f.write(candidate)
        try:
            temp_path.chmod(helper.stat().st_mode)
            os.replace(temp_path, helper)
        finally:
            temp_path.unlink(missing_ok=True)
        print("✅ 已自动重放 NewCM 字体补丁，原文件已备份。", flush=True)
    else:
        print("✅ 字体分类检查通过；已有修复，无需重复打补丁。", flush=True)
    print(f"✅ 当前环境：{env_dir}", flush=True)
    print(f"✅ pdf2zh-next {version('pdf2zh-next')} / BabelDOC {version('BabelDOC')}", flush=True)


def ensure_layout_patch(env_dir: Path, *, allow_repair: bool) -> None:
    """Replay only reviewed source hashes, as one rollback-capable transaction."""
    bundle = Path(__file__).resolve().parent / "babeldoc-patches"
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("schema") != 1:
        raise RuntimeError("排版补丁清单版本未知。")
    checks = manifest.get("checks", [{"payload": "check_layout.py", "sha256": manifest["check_sha256"]}])
    check_paths = []
    for entry in checks:
        check = (bundle / entry["payload"]).resolve()
        if not check.is_relative_to(bundle.resolve()):
            raise RuntimeError("回归检查路径越界。")
        if hashlib.sha256(check.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"回归检查文件校验失败：{check.name}")
        check_paths.append(check)
    root = Path(distribution("BabelDOC").locate_file("babeldoc/format/pdf/document_il")).resolve()
    if not root.is_relative_to(env_dir.resolve()):
        raise RuntimeError("排版补丁目标不属于指定环境。")
    planned = []
    for entry in manifest["files"]:
        target = (root / entry["target"]).resolve()
        payload = (bundle / entry["payload"]).resolve()
        if not target.is_relative_to(root) or not payload.is_relative_to(bundle.resolve()):
            raise RuntimeError("排版补丁路径越界。")
        candidate = payload.read_bytes()
        if hashlib.sha256(candidate).hexdigest() != entry["after_sha256"]:
            raise RuntimeError(f"排版补丁文件校验失败：{payload.name}")
        compile(candidate, str(target), "exec")
        original = target.read_bytes() if target.exists() else None
        digest = hashlib.sha256(original).hexdigest() if original is not None else None
        if digest == entry["after_sha256"]:
            continue
        if digest not in {entry["before_sha256"], *entry.get("replace_sha256", [])}:
            raise RuntimeError(f"{target.name} 与已验证的官方源码不匹配，拒绝覆盖；更新时将恢复旧环境。")
        planned.append((target, original, candidate))
    if planned and not allow_repair:
        raise RuntimeError("排版/样式标签补丁尚未应用，请停止已有服务后正常启动。")
    if planned and listening():
        raise RuntimeError("服务正在运行，不能修改排版引擎；请先停止服务。")
    written = []
    try:
        for target, original, candidate in planned:
            if original is not None:
                digest = hashlib.sha256(original).hexdigest()[:12]
                backup = target.with_name(f"{target.name}.before-layout-{digest}")
                if not backup.exists():
                    shutil.copy2(target, backup)
            written.append((target, original))
            atomic_write(target, candidate)
        for check in check_paths:
            subprocess.run([str(env_dir / "bin/python"), "-I", str(check)], check=True, timeout=60)
    except BaseException:
        for target, original in reversed(written):
            if original is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write(target, original)
        raise
    print("✅ 编号列表、代码块、单词拼接及样式标签检查通过；原有 NewCM 修复保留。", flush=True)


def atomic_write(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        file.write(content)
    try:
        temp.chmod(path.stat().st_mode if path.exists() else 0o644)
        os.replace(temp, path)
        # Invalidate same-second, same-size bytecode after repair/rollback.
        cache = path.parent / "__pycache__"
        for pyc in cache.glob(path.stem + ".*.pyc"):
            pyc.unlink(missing_ok=True)
    finally:
        temp.unlink(missing_ok=True)


def uv_binary() -> str:
    found = shutil.which("uv")
    if not found:
        fallback = Path.home() / ".local/bin/uv"
        if fallback.is_file():
            found = str(fallback)
    if not found:
        raise RuntimeError("找不到 uv；保留当前环境，请检查 uv 安装。")
    return found


def runtime_requirements(server_dir: Path) -> list[str]:
    data = json.loads((server_dir / "config/venv.json").read_text())
    reqs = data["pdf2zh_next"]["uv"]["packages"]
    if not isinstance(reqs, list) or not reqs or not all(isinstance(x, str) for x in reqs):
        raise ValueError("venv.json 中的 uv packages 配置无效。")
    return reqs


def latest_compatible(package: str, spec: SpecifierSet) -> Version:
    with urlopen(f"https://pypi.org/pypi/{package}/json", timeout=5) as response:
        data = json.load(response)
    python_version = ".".join(map(str, sys.version_info[:3]))
    candidates = []
    for raw, files in data["releases"].items():
        try:
            candidate = Version(raw)
        except InvalidVersion:
            continue
        if candidate.is_prerelease or candidate.is_devrelease or candidate not in spec:
            continue
        if any(not f.get("yanked", False) and
               python_version in SpecifierSet(f.get("requires_python") or "") for f in files):
            candidates.append(candidate)
    if not candidates:
        raise RuntimeError("未找到满足当前版本约束的正式发布版。")
    return max(candidates)


def check_runtime_updates(server_dir: Path) -> bool:
    reqs = [Requirement(x) for x in runtime_requirements(server_dir)]
    next_req = next(r for r in reqs if r.name.lower().replace("_", "-") == "pdf2zh-next")
    babel_req = next(Requirement(r) for r in distribution("pdf2zh-next").requires or []
                     if Requirement(r).name.lower() == "babeldoc")
    babel_overrides = [r for r in reqs if r.name.lower() == "babeldoc"]
    if (distribution("BabelDOC").read_text("direct_url.json")
            or distribution("pdf2zh-next").read_text("direct_url.json")
            or next_req.url or any(r.url for r in babel_overrides)):
        print("ℹ️ 检测到源码/Fork 安装来源，跳过官方依赖升级；请先同步对应源码分支。")
        return False
    if babel_overrides:
        babel_req.specifier &= babel_overrides[0].specifier
    specs = {"pdf2zh-next": next_req.specifier, "BabelDOC": babel_req.specifier}
    available = False
    print("🔍 检查官方翻译依赖发布版本（遵循当前兼容范围）...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {name: executor.submit(latest_compatible, name, spec) for name, spec in specs.items()}
        for name, future in futures.items():
            try:
                latest = future.result()
                current = Version(version(name))
                print(f"  {name}: 本地 {current}，兼容范围内最新 {latest}", flush=True)
                available |= latest > current
            except Exception as exc:
                print(f"⚠️ {name} 更新检查失败，继续使用当前环境：{exc}", flush=True)
    return available


def listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8890), timeout=1):
            return True
    except OSError:
        return False


def verify_runtime(env_dir: Path) -> None:
    ensure_patch(env_dir, allow_repair=True)
    ensure_layout_patch(env_dir, allow_repair=True)
    subprocess.run([uv_binary(), "pip", "check", "--python", str(env_dir / "bin/python")], check=True)
    subprocess.run([
        str(env_dir / "bin/python"), "-I", "-c",
        "import pdf2zh_next.high_level; import babeldoc.format.pdf.high_level; "
        "print('✅ 翻译引擎导入检查通过。')",
    ], check=True, timeout=90)


def update_runtime(server_dir: Path, env_dir: Path) -> bool:
    if listening():
        print("⚠️ 8890 端口正在使用，跳过依赖升级。请先停止已有服务。")
        return False
    requirements = runtime_requirements(server_dir)
    uv = uv_binary()
    state_dir = server_dir.parent / ".pdf2zh-local"
    state_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = state_dir / f"venv-before-update-{stamp}"
    failed = state_dir / f"venv-failed-update-{stamp}"
    print(f"💾 备份当前环境：{backup}", flush=True)
    try:
        clone = subprocess.run(["/bin/cp", "-cR", str(env_dir), str(backup)], capture_output=True)
        if clone.returncode:
            if backup.exists():
                shutil.rmtree(backup)
            shutil.copytree(env_dir, backup, symlinks=True)
    except Exception as exc:
        print(f"⚠️ 环境备份未完成，取消升级，原环境未改动：{exc}", flush=True)
        return False
    try:
        subprocess.run([
            uv, "pip", "install", "--python", str(env_dir / "bin/python"),
            "--upgrade-package", "pdf2zh-next", "--upgrade-package", "babeldoc",
            *requirements, "BabelDOC",
        ], check=True)
        subprocess.run([
            str(env_dir / "bin/python"), str(Path(__file__).resolve()),
            "--server-dir", str(server_dir), "--verify",
        ], check=True)
    except BaseException as exc:
        if env_dir.exists():
            env_dir.rename(failed)
        backup.rename(env_dir)
        print(f"⚠️ 更新或检查失败，已恢复更新前环境：{exc}", flush=True)
        print(f"失败环境保留用于排查：{failed}", flush=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False
    print("✅ 依赖已更新，字体、排版、样式标签补丁及运行检查通过。", flush=True)
    print(f"上一个环境的备份保留在：{backup}", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-dir", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-only", action="store_true")
    modes.add_argument("--check-updates-only", action="store_true")
    modes.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    server_dir = args.server_dir.resolve()
    env_dir = server_dir / "zotero-pdf2zh-next-venv"
    if args.verify:
        if listening():
            raise RuntimeError("请先停止已有服务再执行修复验证。")
        verify_runtime(env_dir)
        return 0
    special_mode = args.check_only or args.check_updates_only
    lock = None
    if not special_mode:
        lock = open(Path(__file__).with_suffix(".lock"), "a")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("ℹ️ 已有此启动器启动的服务或更新流程，请使用现有窗口。")
            return 3
        os.set_inheritable(lock.fileno(), True)
    if not special_mode and listening():
        print("ℹ️ 8890 端口已有服务。请沿用现有服务，或关闭后重新运行本脚本。")
        return 3
    ensure_patch(env_dir, allow_repair=not special_mode)
    ensure_layout_patch(env_dir, allow_repair=not special_mode)
    if args.check_only:
        return 0
    available = check_runtime_updates(server_dir)
    if args.check_updates_only:
        return 0
    if available:
        try:
            answer = input("是否更新翻译依赖？将先备份并在检查失败时恢复 [y/N]：").strip().lower()
        except EOFError:
            answer = ""
        if answer in {"y", "yes"}:
            update_runtime(server_dir, env_dir)
        else:
            print("👌 保留当前翻译环境。")
    print("请保持终端窗口开启；在 Zotero 中选择 pdf2zh_next 并发起翻译。", flush=True)
    print("Server 官方更新也会检查；确认更新完成后，请重新运行本脚本。", flush=True)
    os.chdir(server_dir)
    os.execv(str(env_dir / "bin/python"), [
        str(env_dir / "bin/python"), "server.py", "--host=127.0.0.1", "--port=8890",
        "--enable_venv=True", "--env_tool=uv", "--skip_install=True", "--check_update=True",
    ])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"❌ 无法确认修复版环境可用，停止启动：{exc}", file=sys.stderr)
        raise SystemExit(1)
