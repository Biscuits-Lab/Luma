"""Route the private headless helper before importing any Qt GUI modules."""
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--thumbnail-worker":
        from .thumbnail_worker import main as thumbnail_main
        return thumbnail_main(args[1:])
    if args == ["--shell-register-current-user"]:
        from pathlib import Path
        from . import shell_integration as shell
        from .installer import InstallerError, register_managed_installation
        try:
            register_managed_installation(Path(sys.executable))
            return 0
        except (InstallerError, shell.ShellIntegrationError, OSError):
            return 2
    if args == ["--shell-unregister-if-owned"]:
        from pathlib import Path
        from . import shell_integration as shell
        from .installer import InstallerError, uninstall_managed_installation
        try:
            uninstall_managed_installation(Path(sys.executable))
            return 0
        except (InstallerError, shell.ShellIntegrationError, OSError):
            return 2
    from .app import main as viewer_main
    return viewer_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
