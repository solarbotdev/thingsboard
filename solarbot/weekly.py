#!/usr/bin/env python3
"""Plan and report steps of .github/workflows/solarbot-weekly.yml. Stdlib only.

    weekly.py plan     -> writes targets / blocked / any to $GITHUB_OUTPUT
    weekly.py report   -> opens, updates or closes issues from what got built

Environment: GH_TOKEN (the workflow's GITHUB_TOKEN), GITHUB_REPOSITORY,
GITHUB_SERVER_URL, GITHUB_RUN_ID; for plan also SOLARBOT_ALLOW_NON_APACHE
(space-separated upstream tags whose non-Apache license a human accepted);
for report TARGETS and BLOCKED (plan's JSON outputs).

Local dry run of plan without GHCR access: SOLARBOT_HAVE_TAGS="4.3.1.4-solarbot.1"
and SOLARBOT_BRANCHES="solarbot/v4.3.1.4" override what it would read.

Versions are compared padded to four components (v4.4 == 4.4.0 == 4.4.0.0).
A "line" is the first three: 4.3.1.4 and 4.3.1.6 are one line, 4.4.0 another.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UPSTREAM = 'thingsboard/thingsboard'
IMAGE_REPO = 'solarbotdev/tb-node'      # ghcr.io/<this>
RELEASE_TAG = re.compile(r'^v\d+(\.\d+){1,3}$')
IMAGE_TAG = re.compile(r'^(\d+(?:\.\d+){1,3})-solarbot\.(\d+)$')
API = 'https://api.github.com'


def key(version):
    """'v4.4' / '4.4.0' -> (4, 4, 0, 0)."""
    parts = [int(p) for p in version.lstrip('v').split('.')]
    return tuple(parts + [0] * (4 - len(parts)))


def api(path, method='GET', body=None, raw=False):
    req = urllib.request.Request(path if path.startswith('http') else API + path, method=method)
    req.add_header('Authorization', 'Bearer ' + os.environ['GH_TOKEN'])
    req.add_header('Accept', 'application/vnd.github.raw' if raw else 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, data, timeout=60) as r:
        out = r.read()
    return out.decode() if raw else (json.loads(out) if out else None)


def ghcr_tags():
    """Tags of ghcr.io/solarbotdev/tb-node via the registry API (works for a
    private package with the workflow token, unlike the Packages REST API)."""
    if 'SOLARBOT_HAVE_TAGS' in os.environ:
        return os.environ['SOLARBOT_HAVE_TAGS'].split()
    import base64
    basic = base64.b64encode(('x:' + os.environ['GH_TOKEN']).encode()).decode()
    req = urllib.request.Request('https://ghcr.io/token?service=ghcr.io&scope=repository:%s:pull' % IMAGE_REPO)
    req.add_header('Authorization', 'Basic ' + basic)
    with urllib.request.urlopen(req, timeout=60) as r:
        token = json.load(r)['token']
    tags, url = [], 'https://ghcr.io/v2/%s/tags/list?n=1000' % IMAGE_REPO
    while url:
        req = urllib.request.Request(url)
        req.add_header('Authorization', 'Bearer ' + token)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                tags += json.load(r).get('tags') or []
                link = r.headers.get('Link', '')
        except urllib.error.HTTPError as e:
            if e.code == 404:  # no package yet
                return []
            raise
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = urllib.parse.urljoin('https://ghcr.io', m.group(1)) if m else None
    return tags


def image_digest(tag):
    """Digest of a published tag (HEAD on the manifest), or None."""
    import base64
    basic = base64.b64encode(('x:' + os.environ['GH_TOKEN']).encode()).decode()
    req = urllib.request.Request('https://ghcr.io/token?service=ghcr.io&scope=repository:%s:pull' % IMAGE_REPO)
    req.add_header('Authorization', 'Basic ' + basic)
    with urllib.request.urlopen(req, timeout=60) as r:
        token = json.load(r)['token']
    req = urllib.request.Request('https://ghcr.io/v2/%s/manifests/%s' % (IMAGE_REPO, tag), method='HEAD')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Accept', ', '.join([
        'application/vnd.oci.image.index.v1+json', 'application/vnd.oci.image.manifest.v1+json',
        'application/vnd.docker.distribution.manifest.list.v2+json',
        'application/vnd.docker.distribution.manifest.v2+json']))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.headers.get('Docker-Content-Digest')
    except urllib.error.HTTPError:
        return None


def solarbot_branches():
    """{key: branch name} for every solarbot/v<upstream tag> branch of the fork."""
    if 'SOLARBOT_BRANCHES' in os.environ:
        names = os.environ['SOLARBOT_BRANCHES'].split()
    else:
        refs = api('/repos/%s/git/matching-refs/heads/solarbot/v' % os.environ['GITHUB_REPOSITORY'])
        names = [r['ref'][len('refs/heads/'):] for r in refs]
    out = {}
    for n in names:
        tag = n[len('solarbot/'):]
        if RELEASE_TAG.match(tag):
            out[key(tag)] = n
    return out


def upstream_releases():
    rels = []
    for page in (1, 2, 3):
        batch = api('/repos/%s/releases?per_page=100&page=%d' % (UPSTREAM, page))
        rels += batch
        if len(batch) < 100:
            break
    return {key(r['tag_name']): r['tag_name'] for r in rels
            if not r['draft'] and not r['prerelease'] and RELEASE_TAG.match(r['tag_name'])}


def upstream_file(path, tag):
    return api('/repos/%s/contents/%s?ref=%s' % (UPSTREAM, path, urllib.parse.quote(tag)), raw=True)


def plan():
    releases = upstream_releases()
    have = {}
    for t in ghcr_tags():
        m = IMAGE_TAG.match(t)
        if m:
            have.setdefault(key(m.group(1)), []).append(t)
    branches = solarbot_branches()
    newest_have = max(have) if have else None
    print('upstream stable releases: %s' % ', '.join(releases[k] for k in sorted(releases, reverse=True)[:8]))
    print('images: %s' % ', '.join(sorted(t for ts in have.values() for t in ts)))
    print('solarbot branches: %s' % ', '.join(branches[k] for k in sorted(branches)))

    wanted = {}
    newest = max(releases)
    # 1. The newest stable release overall, if newer than every image we have.
    if newest_have is None or newest > newest_have:
        wanted[newest] = 'newest upstream release'
    # 2. The newest patch of the line (first three components) of every
    #    existing solarbot branch.
    for b in branches:
        line = [k for k in releases if k[:3] == b[:3]]
        if line and max(line) > b:
            wanted.setdefault(max(line), 'newest patch of the %s line (%s)' % ('.'.join(map(str, b[:3])), branches[b]))
    allowed = set(os.environ.get('SOLARBOT_ALLOW_NON_APACHE', '').split())

    targets, blocked = [], []
    for k in sorted(wanted):
        tag = releases[k]
        if k in have:
            print('%s: image already exists (%s); nothing to do' % (tag, ', '.join(have[k])))
            continue
        # The SolarBot port rebrands the UI. ThingsBoard 4.4+ is BUSL-1.1, whose
        # no-charge production grant requires the ThingsBoard name, logo and
        # attribution to stay visible and unmodified; such a release is not
        # built until a human has decided and listed it in the repository
        # variable SOLARBOT_ALLOW_NON_APACHE.
        lic = upstream_file('LICENSE', tag).lstrip()
        apache = lic.startswith('Apache License')
        if not apache and tag not in allowed:
            first = ' '.join(lic.split('\n', 3)[:3]).strip()[:200]
            print('%s: BLOCKED, LICENSE is not Apache-2.0 (%r...)' % (tag, first))
            blocked.append({'tag': tag, 'reason': wanted[k], 'license_head': first})
            continue
        # Port from the most recent earlier solarbot branch that has an image
        # (a branch still being hand-fixed is not a source).
        prev = [b for b in branches if b < k and b in have]
        if not prev:
            print('::error::%s: no earlier solarbot branch with an image to port from' % tag)
            sys.exit(1)
        pb = branches[max(prev)]
        version = json.loads(upstream_file('ui-ngx/package.json', tag))['version']
        if key(version) != k:
            print('::error::%s: ui-ngx/package.json says %s' % (tag, version))
            sys.exit(1)
        minor = newest_have is not None and k[:3] != newest_have[:3] and k > newest_have
        t = {'tag': tag, 'version': version, 'branch': 'solarbot/' + tag,
             'prev_branch': pb, 'prev_tag': pb[len('solarbot/'):],
             'image_tag': version + '-solarbot.1', 'minor': minor,
             'reason': wanted[k], 'apache': apache}
        print('%s: TARGET %s' % (tag, json.dumps(t)))
        targets.append(t)

    out = os.environ.get('GITHUB_OUTPUT')
    lines = ['targets=' + json.dumps(targets), 'blocked=' + json.dumps(blocked),
             'any=' + ('true' if targets else 'false')]
    if out:
        with open(out, 'a') as fh:
            fh.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


# --- report -------------------------------------------------------------------

def all_issues(repo):
    issues = []
    for page in range(1, 11):
        batch = api('/repos/%s/issues?state=all&per_page=100&page=%d' % (repo, page))
        issues += [i for i in batch if 'pull_request' not in i]
        if len(batch) < 100:
            break
    return issues


def report():
    repo = os.environ['GITHUB_REPOSITORY']
    run_url = '%s/%s/actions/runs/%s' % (os.environ.get('GITHUB_SERVER_URL', 'https://github.com'),
                                        repo, os.environ['GITHUB_RUN_ID'])
    targets = json.loads(os.environ.get('TARGETS') or '[]')
    blocked = json.loads(os.environ.get('BLOCKED') or '[]')
    tags = set(ghcr_tags())
    issues = all_issues(repo)
    jobs = api('/repos/%s/actions/runs/%s/jobs?per_page=100' % (repo, os.environ['GITHUB_RUN_ID']))['jobs']

    def find(title):
        same = [i for i in issues if i['title'] == title]
        return (([i for i in same if i['state'] == 'open'] or same) or [None])[0]

    def comment(issue, body):
        api('/repos/%s/issues/%d/comments' % (repo, issue['number']), 'POST', {'body': body})

    def create(title, body):
        i = api('/repos/%s/issues' % repo, 'POST', {'title': title, 'body': body})
        issues.append(i)
        print('opened #%d %s' % (i['number'], title))
        return i

    failed = 0
    for t in targets:
        tag, image_tag = t['tag'], t['image_tag']
        attention = 'SolarBot build for ThingsBoard %s needs attention' % tag
        if image_tag in tags:
            digest = image_digest(image_tag)
            ref = 'ghcr.io/%s:%s@%s' % (IMAGE_REPO, image_tag, digest)
            print('%s: built %s' % (tag, ref))
            i = find(attention)
            if i and i['state'] == 'open':
                comment(i, 'Built and published `%s` (%s). Closing.' % (ref, run_url))
                api('/repos/%s/issues/%d' % (repo, i['number']), 'PATCH', {'state': 'closed'})
            if t['minor']:
                title = 'ThingsBoard %s image ready — manual upgrade' % tag
                if not find(title):
                    create(title, (
                        '`%s` is published (branch `%s`, git tag `v%s`, %s).\n\n'
                        'This is a new minor/major line, so the VPS does not apply it on its own '
                        '(it only follows patch releases and rebuilds of the line it runs). '
                        'Read the upstream upgrade notes for %s, take a backup, then upgrade '
                        'deliberately with the VPS tooling (`tbctl upgrade`).' % (ref, t['branch'], image_tag, run_url, tag)))
            continue
        failed += 1
        steps = []
        for j in jobs:
            if tag in j['name'] and j.get('conclusion') == 'failure':
                bad = [s['name'] for s in j.get('steps') or [] if s.get('conclusion') == 'failure']
                steps.append('- job **%s**, failed step(s): %s — %s' % (j['name'], ', '.join(bad) or '?', j['html_url']))
        body = ('Weekly run %s could not publish `ghcr.io/%s:%s` from `%s` (%s).\n\n%s\n\n'
                'Port = cherry-pick of `git rev-list %s..%s` onto upstream `%s`, pushed as `%s`; '
                'then the reusable build. See solarbot/README.md on solarbot/main, '
                '"Hand-fixing a port". Nothing was tagged.' % (
                    run_url, IMAGE_REPO, image_tag, t['branch'], t['reason'],
                    '\n'.join(steps) or '- no failed job matched; see the run',
                    t['prev_tag'], t['prev_branch'], tag, t['branch']))
        i = find(attention)
        if i is None:
            create(attention, body)
        else:
            if i['state'] != 'open':
                api('/repos/%s/issues/%d' % (repo, i['number']), 'PATCH', {'state': 'open'})
            comment(i, body)
            print('updated #%d %s' % (i['number'], attention))

    for b in blocked:
        title = 'ThingsBoard %s is not Apache-2.0 licensed — not built automatically' % b['tag']
        if find(title):  # open or closed: a human already saw it
            continue
        create(title, (
            'The weekly job selected %s (%s), but its LICENSE starts with:\n\n> %s\n\n'
            'ThingsBoard 4.4 moved to the Business Source License 1.1. Its no-charge production '
            'grant requires the ThingsBoard name, logo and "Powered by ThingsBoard" attribution to '
            'remain visible and unmodified and a (free) license key, and caps commercial use at '
            '100 devices on one server; the SolarBot image replaces that branding. Read the full '
            'LICENSE at https://github.com/%s/blob/%s/LICENSE before deciding.\n\n'
            'To build it anyway (e.g. with a commercial license), add `%s` to the repository '
            'variable `SOLARBOT_ALLOW_NON_APACHE` (space-separated) and run the weekly workflow. '
            'First seen in %s.' % (b['tag'], b['reason'], b['license_head'], UPSTREAM, b['tag'], b['tag'], run_url)))

    if failed:
        print('%d target(s) not published' % failed)
        sys.exit(1)


if __name__ == '__main__':
    {'plan': plan, 'report': report}[sys.argv[1]]()
