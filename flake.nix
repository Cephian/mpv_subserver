{
  description = "MPV Subtitle Viewer - Language learning subtitle viewer for MPV";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3;  # Use default Python 3 version
        pythonPackages = python.pkgs;

        mpv-subtitle-viewer = pythonPackages.buildPythonPackage {
          pname = "mpv-subtitle-viewer";
          version = "0.1.0";
          src = ./.;

          pyproject = true;

          build-system = [
            pythonPackages.setuptools
          ];

          propagatedBuildInputs = [
            pythonPackages.fastapi
            pythonPackages.uvicorn
            pythonPackages.websockets
          ];

          # Don't run tests during build
          doCheck = false;

          meta = {
            mainProgram = "mpv_subserver";
          };
        };
      in
      {
        packages.default = mpv-subtitle-viewer;

        devShells.default = pkgs.mkShell {
          buildInputs = [
            python
            pythonPackages.fastapi
            pythonPackages.uvicorn
            pythonPackages.websockets
            pythonPackages.setuptools
            pythonPackages.pytest
            pythonPackages.pytest-asyncio
            pythonPackages.ruff
          ];

          shellHook = ''
            echo "MPV Subtitle Viewer development environment"
            echo ""
            echo "Usage:"
            echo "  python -m server.main  # Run server"
            echo "  pytest                 # Run tests"
            echo "  ruff check .           # Lint code"
            echo "  ruff format .          # Format code"
            echo ""
            echo "Build:"
            echo "  nix run .#             # Build and run"
          '';
        };
      }
    );
}
