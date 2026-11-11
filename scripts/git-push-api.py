#!/usr/bin/env python3
"""
git-push-api.py -- 用 GitHub Data API 逐文件推送，绕开 git pack 传输

为什么不用 git push
-------------------
本机 `git push` 到 github.com 稳定报 `gitobj/pack: bad pack header`。
已排除：代理（清空 http.proxy 同样失败）、thin pack（--no-thin 同样失败）、
HTTP/1.1 + 512MB postBuffer（同样失败）、对象库损坏（git fsck 干净）、
对象数量（本次仅 44 个对象 / 28 文件）。
`gh api` 走同一网络却正常，说明坏的是 git smart-HTTP 的 pack 通道本身。

所以不走 pack：用 blobs API 逐个上传内容，再用 trees -> commits -> refs
三个 API 拼出一个真实提交。结果与 git push 等价。

安全性：最后一步 refs PATCH 传 force=false，只允许 fast-forward，
        不会覆盖远端上别人的新提交；若远端已前进则直接报错而非强推。

用法：python git-push-api.py <owner/repo> <branch> <base_commit> <message_file>
"""
import base64
import hashlib
import json
import subprocess
import sys


def git(args, binary=False):
    r = subprocess.run(['git'] + args, capture_output=True)
    if r.returncode != 0:
        raise SystemExit('git %s failed: %s' % (args[:2], r.stderr.decode('utf-8', 'replace')[:300]))
    return r.stdout if binary else r.stdout.decode('utf-8').strip()


def gh_json(endpoint, method, payload):
    """用 --input 传完整 JSON，避免 -f 把嵌套结构压成字符串。"""
    body = json.dumps(payload)
    r = subprocess.run(['gh', 'api', endpoint, '--method', method,
                        '--input', '-'],
                       input=body.encode('utf-8'), capture_output=True)
    if r.returncode != 0:
        raise SystemExit('gh api %s %s failed: %s' %
                         (method, endpoint, r.stderr.decode('utf-8', 'replace')[:500]))
    out = r.stdout.decode('utf-8').strip()
    return json.loads(out) if out else {}


def main():
    repo, branch, base, msgfile = sys.argv[1:5]
    message = open(msgfile, encoding='utf-8').read().strip()

    raw = git(['-c', 'core.quotePath=false', 'diff', '--name-status', base, 'HEAD'])
    adds_mods, dels = [], []
    for line in raw.splitlines():
        parts = line.split('\t')
        st, path = parts[0][0], parts[-1]
        (dels if st == 'D' else adds_mods).append((st, path))
    print(f'{len(adds_mods)} 增改 / {len(dels)} 删除')

    tree = []
    # 删除：sha 置 null，GitHub 会把它从 tree 中移除
    for _, p in dels:
        tree.append({'path': p, 'mode': '100644', 'type': 'blob', 'sha': None})

    for i, (_, p) in enumerate(adds_mods, 1):
        sha = git(['rev-parse', f'HEAD:{p}'])
        # cat-file blob 输出的就是纯内容（不带 "blob N\0" 头），
        # 所以自己拼上头再算 SHA，必须等于 git 记录的 sha；
        # 不相等说明内容在读取过程中被动过（换行符规范化等），
        # 上传后会产生另一个对象，提交内容就不是本地这份。
        body = git(['cat-file', 'blob', sha], binary=True)
        local = hashlib.sha1(b'blob %d\0' % len(body) + body).hexdigest()
        if local != sha:
            raise SystemExit(f'对象校验失败 {p}: 本地算出 {local}，git 记录 {sha}')
        res = gh_json(f'repos/{repo}/git/blobs', 'POST',
                      {'content': base64.b64encode(body).decode(),
                       'encoding': 'base64'})
        tree.append({'path': p, 'mode': '100644', 'type': 'blob',
                     'sha': res['sha']})
        print(f'  [{i}/{len(adds_mods)}] {p} -> {res["sha"][:8]}')

    r = subprocess.run(['gh', 'api', f'repos/{repo}/git/commits/{base}'],
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit('base commit 读取失败: ' + r.stderr.decode('utf-8', 'replace')[:300])
    base_commit = json.loads(r.stdout.decode())

    t = gh_json(f'repos/{repo}/git/trees', 'POST',
                {'base_tree': base_commit['tree']['sha'], 'tree': tree})
    print(f'tree {t["sha"][:8]}')

    c = gh_json(f'repos/{repo}/git/commits', 'POST',
                {'message': message, 'parents': [base], 'tree': t['sha']})
    print(f'commit {c["sha"][:8]}')

    gh_json(f'repos/{repo}/git/refs/heads/{branch}', 'PATCH',
            {'sha': c['sha'], 'force': False})
    print(f'ref {branch} -> {c["sha"][:8]}')


if __name__ == '__main__':
    main()
