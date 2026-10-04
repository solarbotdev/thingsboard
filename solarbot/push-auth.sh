# Sourced by solarbot-weekly.yml (port and pushtest jobs); not run directly.
#
# push_auth_setup  -- run inside the git repo whose `origin` is the fork.
#   SOLARBOT_PUSH_KEY set (env PUSH_KEY): push over SSH with the repo's write
#     deploy key. SSH pushes are not subject to the "workflows" permission that
#     makes GITHUB_TOKEN refuse history containing upstream workflow commits.
#     Host keys are GitHub's published ones (api.github.com/meta), pinned with
#     StrictHostKeyChecking=yes, not trust-on-first-use.
#   otherwise: HTTPS with PUSH_TOKEN (SOLARBOT_PUSH_TOKEN, else GITHUB_TOKEN).
# push_auth_cleanup -- shreds the key; the caller traps EXIT with it.
# Sets PUSH_MODE=ssh|token. Never echoes the key or the token.
PUSH_KEYDIR=""
PUSH_MODE=token

push_auth_cleanup() {
  if [ -n "${PUSH_KEYDIR:-}" ] && [ -d "$PUSH_KEYDIR" ]; then
    shred -u "$PUSH_KEYDIR/key" 2>/dev/null || rm -f "$PUSH_KEYDIR/key"
    rm -rf "$PUSH_KEYDIR"
  fi
}

push_auth_setup() {
  if [ -z "${PUSH_KEY:-}" ]; then
    PUSH_MODE=token
    echo "push credential: HTTPS token (SOLARBOT_PUSH_KEY not set)"
    return 0
  fi
  PUSH_KEYDIR="$(mktemp -d "${RUNNER_TEMP:-/tmp}/pushkey.XXXXXX")"
  ( umask 077; printf '%s\n' "$PUSH_KEY" > "$PUSH_KEYDIR/key" )
  chmod 600 "$PUSH_KEYDIR/key"
  unset PUSH_KEY
  curl -fsSL --retry 3 -H 'Accept: application/vnd.github+json' https://api.github.com/meta \
    | python3 -c 'import json,sys
for k in json.load(sys.stdin).get("ssh_keys", []):
    print("github.com " + k)' > "$PUSH_KEYDIR/known_hosts"
  [ -s "$PUSH_KEYDIR/known_hosts" ] || { echo "::error::no ssh_keys in api.github.com/meta"; return 1; }
  export GIT_SSH_COMMAND="ssh -i $PUSH_KEYDIR/key -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile=$PUSH_KEYDIR/known_hosts -o StrictHostKeyChecking=yes"
  git remote set-url --push origin "git@github.com:${GITHUB_REPOSITORY}.git"
  PUSH_MODE=ssh
  echo "push credential: SSH deploy key (SOLARBOT_PUSH_KEY), host keys pinned from api.github.com/meta"
}
