import sys

from ops_agent_cli import __version__

if __name__ == "__main__":
    if "--version" in sys.argv[1:]:
        print(f"ops-agent {__version__}")
        raise SystemExit(0)

    from ops_agent_cli.main import main

    raise SystemExit(main())
