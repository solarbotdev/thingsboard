#!/usr/bin/env python3
"""Replace the UI inside ThingsBoard's Spring Boot jar, and nothing else.

    overlay_ui.py OFFICIAL_JAR DIST_DIR OUT_JAR

OFFICIAL_JAR is /usr/share/thingsboard/bin/thingsboard.jar from the official
tb-node image. Upstream builds ui-ngx into target/generated-resources/public,
packs it as a plain jar (ui-ngx-<version>.jar, files under public/) and the
spring-boot-maven-plugin nests that jar, STORED, as BOOT-INF/lib/ui-ngx-*.jar
inside thingsboard.jar, behind an embedded launch script (<executable>true).

This rewrites the outer jar by copying every other entry's raw bytes (local
header, data, data descriptor and central-directory record) untouched, so the
server code stays byte-identical to the official build. Only the ui-ngx entry
is rebuilt: its non-public/ entries (META-INF) are kept, public/ is replaced by
DIST_DIR, and the result is written STORED, which Spring Boot's nested-jar
loader requires (a DEFLATED nested jar fails at startup).

The JDK `jar --update` tool is deliberately not used: it reads the archive as a
stream from byte 0 and does not understand the launch-script preamble.
Stdlib only.
"""
import hashlib
import io
import os
import struct
import sys
import zipfile
import zlib

EOCD_SIG = 0x06054B50
CEN_SIG = 0x02014B50
LOC_SIG = 0x04034B50
UI_PREFIX = 'BOOT-INF/lib/ui-ngx-'


def die(msg):
    sys.exit('overlay_ui: ' + msg)


def parse(data):
    """Return (base, first, entries, eocd_pos); entries in central-directory order.

    base: what the stored offsets are relative to (the end of the launch script
    when the zip was written after it, as Spring Boot does; 0 when the offsets
    are absolute). first: where the first local record starts, i.e. the
    preamble length. Both conventions are kept as found.
    """
    eocd_pos = data.rfind(struct.pack('<I', EOCD_SIG), max(0, len(data) - 65557))
    if eocd_pos < 0:
        die('no end-of-central-directory record')
    (_, disk, cd_disk, n_disk, n_total, cd_size, cd_off, clen) = struct.unpack_from('<IHHHHIIH', data, eocd_pos)
    # ZIP64 or multi-disk would need offsets this code does not rewrite; a
    # silent mis-rewrite would only show up as a broken jar at startup.
    if disk or cd_disk or n_disk != n_total or 0xFFFF in (n_total,) or 0xFFFFFFFF in (cd_size, cd_off):
        die('ZIP64/multi-disk archive not supported')
    if eocd_pos + 22 + clen != len(data):
        die('trailing bytes after the end-of-central-directory record')
    cd_start = eocd_pos - cd_size
    base = cd_start - cd_off
    if base < 0:
        die('central directory offset points past its real position')
    entries = []
    p = cd_start
    for _ in range(n_total):
        sig, = struct.unpack_from('<I', data, p)
        if sig != CEN_SIG:
            die('bad central directory record at %d' % p)
        f = struct.unpack_from('<IHHHHHHIIIHHHHHII', data, p)
        nlen, xlen, cmlen, off = f[10], f[11], f[12], f[16]
        if 0xFFFFFFFF in (f[8], f[9], off):
            die('ZIP64 entry not supported')
        name = data[p + 46:p + 46 + nlen].decode('utf-8')
        entries.append({'name': name, 'cen': data[p:p + 46 + nlen + xlen + cmlen],
                        'loc': base + off, 'method': f[4], 'fields': f})
        p += 46 + nlen + xlen + cmlen
    if p != eocd_pos:
        die('central directory size mismatch')
    # Local records are contiguous in file order; each one runs to the next.
    by_off = sorted(entries, key=lambda e: e['loc'])
    for e, nxt in zip(by_off, by_off[1:] + [None]):
        e['end'] = nxt['loc'] if nxt else cd_start
        if struct.unpack_from('<I', data, e['loc'])[0] != LOC_SIG:
            die('bad local header for ' + e['name'])
    return base, by_off[0]['loc'], entries, eocd_pos


def build_ui_jar(old_bytes, dist_dir, date_time):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(old_bytes)) as old, zipfile.ZipFile(out, 'w') as new:
        kept = [i for i in old.infolist() if not i.filename.startswith('public/')]
        if not any(i.filename.startswith('public/') for i in old.infolist()):
            die('official ui-ngx jar has no public/ entries; packaging changed?')
        for info in kept:  # META-INF/MANIFEST.MF, maven pom.xml/properties
            new.writestr(info, old.read(info), compress_type=info.compress_type)
        dirs, files = ['public/'], []
        for root, dnames, fnames in os.walk(dist_dir):
            dnames.sort()
            rel = os.path.relpath(root, dist_dir).replace(os.sep, '/')
            base = 'public/' if rel == '.' else 'public/' + rel + '/'
            if rel != '.':
                dirs.append(base)
            for fn in sorted(fnames):
                files.append((base + fn, os.path.join(root, fn)))
        for d in dirs:
            zi = zipfile.ZipInfo(d, date_time)
            zi.external_attr = (0o40755 << 16) | 0x10
            new.writestr(zi, b'')
        for name, path in files:
            zi = zipfile.ZipInfo(name, date_time)
            zi.external_attr = 0o100644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(path, 'rb') as fh:
                new.writestr(zi, fh.read())
    return out.getvalue(), len(files)


def main():
    if len(sys.argv) != 4:
        die('usage: overlay_ui.py OFFICIAL_JAR DIST_DIR OUT_JAR')
    src, dist, dst = sys.argv[1:]
    if not os.path.isfile(os.path.join(dist, 'index.html')):
        die(dist + ' has no index.html; not a ui-ngx production build')
    data = open(src, 'rb').read()
    base, first, entries, eocd_pos = parse(data)
    ui = [e for e in entries if e['name'].startswith(UI_PREFIX) and e['name'].endswith('.jar')]
    if len(ui) != 1:
        die('expected exactly one %s*.jar, found %d' % (UI_PREFIX, len(ui)))
    ui = ui[0]
    if ui['method'] != 0:
        die('official ui-ngx entry is not STORED; packaging changed?')

    # The official nested jar's own bytes (STORED: data follows the local header).
    loc = struct.unpack_from('<IHHHHHIIIHH', data, ui['loc'])
    data_start = ui['loc'] + 30 + loc[9] + loc[10]
    old_ui = data[data_start:data_start + ui['fields'][8]]
    if zlib.crc32(old_ui) != ui['fields'][7]:
        die('CRC mismatch reading the official ui-ngx jar')
    dos_time, dos_date = ui['fields'][5], ui['fields'][6]
    date_time = (((dos_date >> 9) & 0x7F) + 1980, (dos_date >> 5) & 0x0F, dos_date & 0x1F,
                 (dos_time >> 11) & 0x1F, (dos_time >> 5) & 0x3F, (dos_time & 0x1F) * 2)
    new_ui, nfiles = build_ui_jar(old_ui, dist, date_time)
    crc = zlib.crc32(new_ui)
    name_b = ui['name'].encode('utf-8')
    f = ui['fields']
    flags = f[3] & 0x0800  # keep only the UTF-8 flag; sizes are known, no descriptor

    out = io.BytesIO()
    out.write(data[:first])  # launch script, unchanged
    new_off = {}
    for e in sorted(entries, key=lambda e: e['loc']):
        new_off[e['name']] = out.tell() - base
        if e is ui:
            out.write(struct.pack('<IHHHHHIIIHH', LOC_SIG, f[2], flags, 0, dos_time, dos_date,
                                  crc, len(new_ui), len(new_ui), len(name_b), 0))
            out.write(name_b)
            out.write(new_ui)
        else:
            out.write(data[e['loc']:e['end']])
    new_cd_start = out.tell()
    for e in entries:  # central directory in the original order
        if e is ui:
            out.write(struct.pack('<IHHHHHHIIIHHHHHII', CEN_SIG, f[1], f[2], flags, 0, dos_time, dos_date,
                                  crc, len(new_ui), len(new_ui), len(name_b), 0, 0, 0, f[14], f[15],
                                  new_off[e['name']]))
            out.write(name_b)
        else:
            cen = bytearray(e['cen'])
            struct.pack_into('<I', cen, 42, new_off[e['name']])
            out.write(cen)
    cd_size = out.tell() - new_cd_start
    eocd = bytearray(data[eocd_pos:])
    struct.pack_into('<II', eocd, 12, cd_size, new_cd_start - base)
    out.write(eocd)
    result = out.getvalue()

    # Self-check before writing anything: re-parse, compare every other entry
    # byte for byte, and prove the new UI entry is STORED and intact.
    base2, first2, entries2, _ = parse(result)
    if (base2, first2) != (base, first) or result[:first] != data[:first]:
        die('self-check: launch script changed')
    if [e['name'] for e in entries2] != [e['name'] for e in entries]:
        die('self-check: entry list changed')
    for a, b in zip(entries, entries2):
        if a is ui:
            if b['method'] != 0:
                die('self-check: ui-ngx entry is not STORED')
            continue
        if data[a['loc']:a['end']] != result[b['loc']:b['end']] or a['cen'][:42] != b['cen'][:42] \
                or a['cen'][46:] != b['cen'][46:]:
            die('self-check: entry changed: ' + a['name'])
    with zipfile.ZipFile(io.BytesIO(result)) as z:
        bad = z.testzip()
        if bad:
            die('self-check: CRC error in ' + bad)
        with zipfile.ZipFile(io.BytesIO(z.read(ui['name']))) as nz:
            if b'<title>SolarBot</title>' not in nz.read('public/index.html'):
                die('self-check: public/index.html has no <title>SolarBot</title>')

    with open(dst, 'wb') as fh:
        fh.write(result)
    print('overlay_ui: %s replaced (%d bytes -> %d bytes, %d files under public/, STORED); '
          '%d other entries copied byte for byte; preamble %d bytes kept' %
          (ui['name'], len(old_ui), len(new_ui), nfiles, len(entries) - 1, first))
    print('overlay_ui: sha256 official %s' % hashlib.sha256(data).hexdigest())
    print('overlay_ui: sha256 new      %s' % hashlib.sha256(result).hexdigest())


if __name__ == '__main__':
    main()
