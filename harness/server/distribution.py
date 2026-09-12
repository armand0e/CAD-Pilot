"""GitHub source installation, with private account pairing passed only locally."""
import shlex

REPOSITORY_URL = 'https://github.com/armand0e/CAD-Pilot'
INSTALLER_URL = 'https://raw.githubusercontent.com/armand0e/CAD-Pilot/main/install.sh'


def connection_command(pairing, origin):
    # Positional arguments keep the account credential out of shell source and Git URLs.
    # Finish the download before executing, so HTTP errors cannot run a partial installer.
    script = f'set -e; installer=$(curl -fsSL {shlex.quote(INSTALLER_URL)}); exec bash -c "$installer" cadpilot "$@"'
    return 'bash -c ' + shlex.quote(script) + ' cadpilot ' + shlex.join([origin, pairing['id'], pairing['token']])


def connection_commands(pairing, origin):
    bash = connection_command(pairing, origin)
    def powershell_quote(value):
        return "'" + value.replace("'", "''") + "'"
    arguments = ' '.join(map(powershell_quote, [origin, pairing['id'], pairing['token']]))
    # The downloaded script uses LF line endings and only ASCII. Passing it as one
    # string to WSL stdin avoids nested PowerShell/Win32/Bash quoting of shell source.
    windows = f'''& {{
  $ErrorActionPreference = 'Stop'
  $cadPilotInstaller = (Invoke-WebRequest -UseBasicParsing {powershell_quote(INSTALLER_URL)}).Content
  $cadPilotInstaller | wsl.exe --exec bash -s -- {arguments}
  if ($LASTEXITCODE -ne 0) {{ throw 'CADPilot setup failed. Check the output above.' }}
}}'''
    return {'windows': windows, 'linux': bash, 'macos': bash}
