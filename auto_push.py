"""Automatically commit and push changes in this repository after a quiet period."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

if os.name == 'nt':
    import msvcrt

ROOT = Path(__file__).resolve().parent
QUIET_SECONDS = 10
POLL_SECONDS = 3
RETRY_SECONDS = 60
SENSITIVE_NAMES = {'.env', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519', 'credentials.json'}
SENSITIVE_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.kdbx'}


def git(*args, check=True):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    result = subprocess.run(['git', *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip() or 'Git 命令失败')
    return result


def log(message):
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_PATH, 'a', encoding='utf-8') as output:
        output.write(f'[{stamp}] {message}\n')


def status_snapshot():
    status = git('status', '--porcelain=v1', '-z', '--untracked-files=all').stdout
    if not status:
        return None, []
    records = status.split(b'\0')
    paths = []
    index = 0
    while index < len(records):
        item = records[index]
        index += 1
        if len(item) < 4:
            continue
        paths.append(os.fsdecode(item[3:]))
        if b'R' in item[:2] or b'C' in item[:2]:
            index += 1
    digest = hashlib.sha256(status)
    digest.update(git('diff', '--binary', 'HEAD', '--').stdout)
    for path in paths:
        try:
            stat = (ROOT / path).stat()
            digest.update(f'{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode('utf-8', errors='replace'))
        except OSError:
            pass
    return digest.hexdigest(), paths


def sensitive(path):
    name = Path(path).name.lower()
    return name in SENSITIVE_NAMES or name.startswith('.env.') or Path(name).suffix in SENSITIVE_SUFFIXES


def check_branch():
    branch = git('symbolic-ref', '--quiet', '--short', 'HEAD').stdout.decode().strip()
    if not branch:
        raise RuntimeError('当前不是分支，已暂停自动上传')
    git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}')
    gitdir = Path(os.fsdecode(git('rev-parse', '--absolute-git-dir').stdout.strip()))
    if any((gitdir / name).exists() for name in ('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD', 'index.lock')):
        raise RuntimeError('存在正在进行的 Git 操作，稍后重试')
    return branch


def run():
    log('自动上传已启动：等待文件稳定后提交并推送当前分支')
    previous = None
    changed_at = time.monotonic()
    last_retry = 0
    while True:
        try:
            branch = check_branch()
            snapshot, paths = status_snapshot()
            now = time.monotonic()
            if snapshot != previous:
                previous, changed_at = snapshot, now
            if snapshot and now - changed_at >= QUIET_SECONDS:
                blocked = [path for path in paths if sensitive(path)]
                if blocked:
                    raise RuntimeError('发现可能包含密钥的文件，已暂停提交：' + ', '.join(blocked))
                git('add', '-A', '--', '.')
                staged = git('diff', '--cached', '--quiet', check=False)
                if staged.returncode == 1:
                    git('commit', '-m', '自动更新 ' + time.strftime('%Y-%m-%d %H:%M:%S'))
                    log(f'已在 {branch} 创建自动提交')
                elif staged.returncode != 0:
                    raise RuntimeError('无法检查暂存区')
                previous = None
                last_retry = 0
            if now - last_retry >= RETRY_SECONDS:
                ahead = int(git('rev-list', '--count', '@{upstream}..HEAD').stdout.strip())
                if ahead:
                    result = git('push', check=False)
                    if result.returncode:
                        raise RuntimeError('推送失败，请检查远程分支、网络或 Git 凭据；本地提交已保留')
                    log(f'已推送 {branch} 上的 {ahead} 个本地提交')
                last_retry = now
        except Exception as exc:
            if time.monotonic() - last_retry >= RETRY_SECONDS:
                log(str(exc))
                last_retry = time.monotonic()
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    try:
        git_dir = Path(os.fsdecode(git('rev-parse', '--absolute-git-dir').stdout.strip()))
        LOG_PATH = git_dir / 'auto-push.log'
        if os.name != 'nt':
            raise RuntimeError('该监听程序目前仅支持 Windows')
        with open(git_dir / 'auto-push.lock', 'a+b') as lock_file:
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('自动上传已在运行')
            run()
    except KeyboardInterrupt:
        log('自动上传已停止')
    except Exception as exc:
        print(f'无法启动自动上传：{exc}', file=sys.stderr)
        sys.exit(1)
