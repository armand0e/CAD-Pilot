"""GitHub source installation, with private account pairing passed only locally."""
import shlex

REPOSITORY_URL = 'https://github.com/armand0e/CAD-Pilot'
INSTALLER_URL = 'https://raw.githubusercontent.com/armand0e/CAD-Pilot/main/install.sh'


def connection_command(pairing, origin):
    # Positional arguments keep the account credential out of shell source and Git URLs.
    # Finish the download before executing, so HTTP errors cannot run a partial installer.
    script = f'set -e; installer=$(curl -fsSL {shlex.quote(INSTALLER_URL)}); exec bash -c "$installer" cadpilot "$@"'
    return 'bash -c ' + shlex.quote(script) + ' cadpilot ' + shlex.join([origin, pairing['id'], pairing['token']])
