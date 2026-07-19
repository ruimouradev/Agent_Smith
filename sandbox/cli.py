"""Alexandre - sandbox entrypoint: CLI parser."""

import sys
import argparse
from pathlib import Path

from contract.models import SandboxConfig
from sandbox.supervisor import LocalSandbox

def main():
    parser = argparse.ArgumentParser(description="Agent Smith Sandbox CLI")
    parser.add_argument("config", nargs="?", default=None, help="Path to config.json")
    parser.add_argument("--mcp-stdio", type=str, help="MCP stdio command")
    parser.add_argument("--mcp-server", type=str, help="MCP server URL")
    
    args = parser.parse_args()

    try:
        if args.config:
            config_path = Path(args.config)
            if config_path.exists():
                config = SandboxConfig.model_validate_json(config_path.read_text())
            else:
                print(f"Error: Config file not found at {args.config}", file=sys.stderr)
                sys.exit(1)
        else:
            config = SandboxConfig()

        # MCP features (Phase 2):
        # We will parse --mcp-stdio and --mcp-server here
        # and fetch schemas to build the manual.
        manual = ""
        
        sandbox = LocalSandbox(config=config, manual=manual)
        
        # Read the code payload from stdin
        code = sys.stdin.read()
        
        # Run it and print observation to stdout (cleanly)
        observation = sandbox.run(code)
        print(observation, end="")
        
    except Exception as e:
        # Graceful fallback on crash
        print(f"Sandbox CLI crashed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
