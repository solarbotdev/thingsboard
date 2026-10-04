# SolarBot build of ThingsBoard CE

`ghcr.io/solarbotdev/tb-node:<version>-solarbot.<N>` is the official
`thingsboard/tb-node:<version>` image with **only the web UI** (ui-ngx)
rebuilt with SolarBot branding. Every other entry of `thingsboard.jar`, the
JRE, scripts and config are byte-identical to the official image, so the
server, DB schema and upgrade path are upstream's. The VPS
(dash.solarbot.com.br) runs this image.

## Branches and tags

| Ref | What |
| --- | --- |
| `master` | Untouched mirror of upstream `thingsboard/thingsboard` master. Never pushed to by us. |
| `solarbot/main` (default branch) | Only tooling: the workflows below, `solarbot/weekly.py`, this README. No ThingsBoard source. |
| `solarbot/v<upstream tag>` | e.g. `solarbot/v4.3.1.4`: upstream tag `v4.3.1.4` + our commits (logo/favicon/title, theme, `solarbot/` build files). No `.github/` changes. |
| git tag `v<version>-solarbot.<N>` | e.g. `v4.3.1.4-solarbot.1`: the exact commit image `<version>-solarbot.<N>` was built from. Created by the build after it publishes. |

`<version>` is the version in `pom.xml` and `ui-ngx/package.json` at the
upstream tag (upstream tag `v4.4` would be version `4.4.0`). `N` starts at 1
and grows for a rebuild of the same version (a branding fix, a build fix).
Image tags and git tags are **never** moved, deleted or overwritten; the build
refuses an existing one.

`solarbot/main` is an orphan branch (no shared history with upstream) so the
default branch holds none of upstream's workflows (they would run in the
fork) and the tooling is not mixed into ThingsBoard's tree.

## Workflows (on `solarbot/main`)

**`solarbot-build.yml`**: build, test and optionally publish one image.
Reusable (`workflow_call`) and manual (`workflow_dispatch`), inputs `ref`
(branch/tag/SHA) and `image_tag` (empty = build and test only).

```sh
# test a branch without publishing
gh workflow run solarbot-build.yml -R solarbotdev/thingsboard -f ref=solarbot/v4.3.1.4
# publish a rebuild (N=2) and create git tag v4.3.1.4-solarbot.2
gh workflow run solarbot-build.yml -R solarbotdev/thingsboard -f ref=solarbot/v4.3.1.4 -f image_tag=4.3.1.4-solarbot.2
```

Steps: version check (`image_tag` must be `<package.json version>-solarbot.<N>`
and package.json must equal the root pom), official base pinned by digest,
node/yarn versions from `ui-ngx/pom.xml`, refuse an existing image or git
tag, `docker buildx build -f solarbot/Dockerfile` (the branch's own
Dockerfile), then smoke tests:

- `solarbot/verify_image.py` on the official and the new jar: the nested
  `BOOT-INF/lib/ui-ngx-*.jar` is STORED (Spring Boot cannot load it
  compressed); `<title>SolarBot</title>`, logo and favicon hashes equal to the
  branch's files; every other entry identical by SHA-256; launch-script
  preamble identical.
- the image: official layers kept plus exactly one; same
  User/Entrypoint/Cmd/Env/WorkingDir.
- live: Postgres 16, `INSTALL_TB=true`, start, served title/logo/favicon,
  sysadmin login through `/api/auth/login`.

Then push, then the annotated git tag. Releases are made this way (dispatch),
not by pushing a git tag: version branches carry no workflow files (see
below), and a tag-push trigger only runs workflows present in the tagged tree.

**`solarbot-weekly.yml`**: Saturdays 06:00 UTC and on demand
(`gh workflow run solarbot-weekly.yml -R solarbotdev/thingsboard`). Saturday,
because the VPS's update cron runs Sunday 04:47 UTC: a release from the week
gets its image the day before, with a day to fix a failed port.

1. **plan** (`solarbot/weekly.py plan`): upstream GitHub releases that are not
   drafts or prereleases and match `^v\d+(\.\d+){1,3}$`, against the tags in
   GHCR. Targets: the newest release if it is newer than every image, and the
   newest patch of the line (first three components) of every
   `solarbot/v*` branch; minus anything that already has an image.
   Releases whose `LICENSE` is not Apache-2.0 are **held back** (see below).
2. **port**: `solarbot/v<tag>` = upstream tag + cherry-pick of
   `git rev-list <prev upstream tag>..solarbot/v<prev>`, where `prev` is the
   newest earlier solarbot branch that has an image. Commits touching only
   `.github/` are skipped. Pushed only if every pick applies. If the branch
   already exists (an earlier failed build, or a hand fix) it is reused as is
   after checking it sits on the upstream tag.
3. **build**: calls `solarbot-build.yml` with `image_tag=<version>-solarbot.1`.
4. **report**: on failure, issue "SolarBot build for ThingsBoard vX needs
   attention" (one per version; later failures comment on it, success closes
   it) with the failed job/step and run link. A new minor/major line (first
   three components differ from the newest image) that got an image gets the
   issue "ThingsBoard vX image ready — manual upgrade": the VPS applies only
   patches/rebuilds of the line it runs.

Running it twice does nothing new: published versions are not selected
again. A concurrency group keeps two runs from overlapping.

### License gate (ThingsBoard 4.4+)

ThingsBoard 4.4 is licensed **BUSL-1.1**, not Apache-2.0. Its no-charge
production grant requires the ThingsBoard name, logo and "Powered by
ThingsBoard" attribution to stay visible and unmodified and a license key, and
caps commercial use at 100 devices on one server. A rebranded 4.4 image is
therefore not something to run in production without a commercial license.
The weekly job does not build such a release; it opens the issue "ThingsBoard
vX is not Apache-2.0 licensed — not built automatically" once. To build one
anyway, add the upstream tag to the repository variable
`SOLARBOT_ALLOW_NON_APACHE` (space-separated, e.g. `v4.4`) and run the weekly
workflow. The 4.4 port also needs real work (see the issue / handover notes):
4.4 moved logo, title, favicon and palette into a runtime white-labeling
service, so the 4.3 commits conflict.

### Pushing with GITHUB_TOKEN

`GITHUB_TOKEN` cannot push commits that create or change workflow files.
That is why version branches carry none. If the push of a new port is
refused because upstream's own history in that release changed its
workflows, create a fine-grained token (this repository only, Contents and
Workflows read/write) and store it as the secret `SOLARBOT_PUSH_TOKEN`; the
port job uses it when present.

## Hand-fixing a port

When the weekly issue reports a cherry-pick conflict for `vX` (previous
branch `solarbot/vP`):

```sh
git fetch https://github.com/thingsboard/thingsboard.git refs/tags/vX:refs/tags/vX refs/tags/vP:refs/tags/vP
git fetch origin solarbot/vP
git switch -c solarbot/vX vX
for c in $(git rev-list --reverse vP..origin/solarbot/vP); do
  git cherry-pick "$c" || break      # resolve, re-doing the intent on vX's source
done                                  # (skip commits that only touch .github/)
git push origin solarbot/vX
gh workflow run solarbot-weekly.yml -R solarbotdev/thingsboard   # reuses the branch, builds, tags
```

Keep the branch linear (no merges) and free of `.github/` changes; the next
port cherry-picks exactly `vX..solarbot/vX`. When resolving, look for: the
logo (`ui-ngx/src/assets/logo_title_white.svg`), `thingsboard.ico`,
`<title>` in `index.html` and `appTitle` in `environments/*.ts`, the
palettes in `scss/constants.scss` and `theme.scss`, hard-coded `#305680` /
`rgba(48,86,128,…)` chrome colors, and `solarbot/Dockerfile` (base image
path `/usr/share/thingsboard/bin/thingsboard.jar`, nested
`BOOT-INF/lib/ui-ngx-<version>.jar`).

## Palette

| Role | Color | Notes |
| --- | --- | --- |
| Primary (500) | graphite `#231F20` | white text 16.3:1. Tints 50 `#e9e9e9` … 400 `#4f4c4d`; shades 600 `#1e1a1b` … 900 `#0e0c0d` |
| Accent (500) | orange `#F18A21` | graphite text 6.5:1 (white only 2.5:1). 50 `#fef3e9` … 400 `#f4a14d`, 600 `#d4791d`, 700 `#9f5b16`, 800 `#854c12` |
| Secondary | `#4a4546` | |
| Dark theme highlight | the orange | login pages, toolbar search |

Defined in `ui-ngx/src/scss/constants.scss` and `ui-ngx/src/theme.scss` on
each version branch. Widget configuration defaults and the data-visualisation
color list keep upstream's colors on purpose (they end up in saved configs).
