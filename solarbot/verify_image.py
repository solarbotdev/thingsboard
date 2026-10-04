#!/usr/bin/env python3
"""Smoke test: compare the SolarBot thingsboard.jar with the official one.

    verify_image.py OFFICIAL_JAR CUSTOM_JAR REPO_ROOT

Fails (exit 1) unless:
  (a) BOOT-INF/lib/ui-ngx-*.jar in CUSTOM_JAR is STORED (Spring Boot cannot
      load a compressed nested jar);
  (b) that nested jar's public/index.html has <title>SolarBot</title>, and its
      public/assets/logo_title_white.svg and public/thingsboard.ico have the
      same SHA-256 as the files in this repo's ui-ngx/src;
  (c) every other entry (all other BOOT-INF/lib jars, classes, manifest...)
      has the same SHA-256 as in OFFICIAL_JAR, with no entry added or removed,
      and the launch-script preamble is unchanged.
Stdlib only.
"""
import hashlib
import io
import os
import sys
import zipfile

failures = []


def check(ok, msg):
    print(('PASS ' if ok else 'FAIL ') + msg)
    if not ok:
        failures.append(msg)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def preamble(path):
    data = open(path, 'rb').read(1 << 20)
    return data[:data.find(b'PK\x03\x04')]


def main():
    official, custom, repo = sys.argv[1:4]
    zo, zc = zipfile.ZipFile(official), zipfile.ZipFile(custom)
    ui = [n for n in zc.namelist() if n.startswith('BOOT-INF/lib/ui-ngx-') and n.endswith('.jar')]
    check(len(ui) == 1, 'exactly one nested ui-ngx jar: %s' % ui)
    ui = ui[0]

    info = zc.getinfo(ui)
    check(info.compress_type == zipfile.ZIP_STORED,
          '(a) %s is STORED (compress_type=%d, size=%d, compressed=%d)'
          % (ui, info.compress_type, info.file_size, info.compress_size))

    nested = zipfile.ZipFile(io.BytesIO(zc.read(ui)))
    index = nested.read('public/index.html')
    check(b'<title>SolarBot</title>' in index, '(b) public/index.html has <title>SolarBot</title>')
    for inner, src in (('public/assets/logo_title_white.svg', 'ui-ngx/src/assets/logo_title_white.svg'),
                       ('public/thingsboard.ico', 'ui-ngx/src/thingsboard.ico')):
        got = sha(nested.read(inner))
        want = sha(open(os.path.join(repo, src), 'rb').read())
        check(got == want, '(b) %s sha256 %s == repo %s' % (inner, got, src))

    names_o, names_c = set(zo.namelist()), set(zc.namelist())
    check(names_o == names_c, '(c) same entry names (%d official, %d custom; only-official %s, only-custom %s)'
          % (len(names_o), len(names_c), sorted(names_o - names_c)[:5], sorted(names_c - names_o)[:5]))
    libs = same = 0
    diff = []
    for n in sorted(names_o & names_c):
        if n == ui:
            continue
        if zo.getinfo(n).CRC != zc.getinfo(n).CRC or sha(zo.read(n)) != sha(zc.read(n)):
            diff.append(n)
        else:
            same += 1
            libs += n.startswith('BOOT-INF/lib/')
    check(not diff, '(c) %d entries other than ui-ngx identical by sha256 (%d of them BOOT-INF/lib jars); differing: %s'
          % (same, libs, diff[:10]))
    check(preamble(official) == preamble(custom), '(c) launch-script preamble identical (%d bytes)' % len(preamble(custom)))

    if failures:
        print('%d check(s) failed' % len(failures))
        sys.exit(1)
    print('all checks passed')


if __name__ == '__main__':
    main()
